"""Agent creation utilities."""

import asyncio
from typing import List

from langchain_core.callbacks.base import AsyncCallbackHandler
from shared.common.agents.bridge import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
    BaseTool,
    ChatOllama,
    ChatGoogleGenerativeAI,
    ChatBedrockConverse,
    create_agent,
    traceable,
    ToolCallLimitMiddleware,
)
from shared.common.agents.middleware import CorrelationMiddleware, ToolLoopGuardMiddleware, ToolIntentMiddleware
from shared.config import config
from shared.common.tools.mcp_tools import get_tools, tools_list_by_capability
from shared.common.infra import get_last_message_content
from shared.common.infra.context import get_token_phase, add_request_tokens, get_request_tokens
from shared.common.agents import prompts as agent_prompts
from shared.common.log import setup_structured_logging


logger = setup_structured_logging(__name__)

# ── Per-request token usage tracking ─────────────────────────────────────────
# Accumulation lives in context._task_tokens (dict keyed by task id).
# add_request_tokens / get_request_tokens are re-exported here for existing callers.

# run_id → estimated input tokens; used when model doesn't report usage
_run_input_est: dict[str, int] = {}

try:
    import tiktoken as _tiktoken
    _enc = _tiktoken.get_encoding("cl100k_base")
    def _count(text: str) -> int:
        return len(_enc.encode(text))
except Exception:
    def _count(text: str) -> int:  # type: ignore[misc]
        return len(text) // 4


class _TokenUsageCallback(AsyncCallbackHandler):
    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs) -> None:
        text = " ".join(
            getattr(m, "content", "") or ""
            for batch in messages
            for m in batch
            if isinstance(getattr(m, "content", ""), str)
        )
        _run_input_est[str(run_id)] = _count(text)

    async def on_llm_end(self, response, *, run_id=None, **kwargs) -> None:
        usage = (response.llm_output or {}).get("token_usage", {})
        call_tokens    = usage.get("total_tokens", 0)
        prompt_tokens  = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        if not call_tokens:
            prompt_tokens = _run_input_est.pop(str(run_id), 0) if run_id else 0
            output_text = " ".join(
                getattr(g, "text", "") or ""
                for gen_list in (response.generations or [])
                for g in gen_list
            )
            completion_tokens = _count(output_text)
            call_tokens = prompt_tokens + completion_tokens
        else:
            _run_input_est.pop(str(run_id), None)

        add_request_tokens(call_tokens)
        from shared.common.infra.context import get_active_agent
        logger.info(
            "LLM token usage: phase=%s agent=%s prompt=%d completion=%d call_total=%d",
            get_token_phase(), get_active_agent() or "—", prompt_tokens, completion_tokens, call_tokens,
        )


_token_cb = _TokenUsageCallback()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_llm(cfg: dict):
    if config.is_using_google():
        if ChatGoogleGenerativeAI is None:
            raise ImportError("langchain-google-genai is not installed. Run: pip install langchain-google-genai")
        return ChatGoogleGenerativeAI(**cfg, callbacks=[_token_cb])
    if config.is_using_bedrock():
        if ChatBedrockConverse is None:
            raise ImportError("langchain-aws is not installed. Run: pip install langchain-aws")
        return ChatBedrockConverse(**cfg, callbacks=[_token_cb])
    return ChatOllama(**cfg, callbacks=[_token_cb])


@traceable(name="get_generalistic_llm")
def get_generalistic_llm(model: str = None):
    return _build_llm(config.get_llm_config(model))

def get_chat_llm(model: str = None):
    return _build_llm(config.get_llm_config_no_think(model))


def _active_model_name() -> str:
    cfg = config.get_llm_config()
    return cfg.get("model") or cfg.get("model_id", "unknown")


def _make_agent(tools: List[BaseTool], system_prompt: str, tool_limiter: ToolCallLimitMiddleware | None = None, max_duplicate_count: int = 1, semantic_middleware=None, condenser=None, jit_middleware=None):
    """Create an agent with the given tools and middleware stack."""
    system_prompt = f"You are running on model: {_active_model_name()}.\n\n{system_prompt}"
    middleware = [CorrelationMiddleware()]
    middleware += [m for m in [jit_middleware, semantic_middleware, condenser] if m is not None]
    middleware.append(ToolLoopGuardMiddleware(max_duplicate_count=max_duplicate_count))
    if isinstance(tool_limiter, ToolCallLimitMiddleware):
        middleware.append(tool_limiter)

    agent = create_agent(model=get_generalistic_llm(), tools=tools, system_prompt=system_prompt, middleware=middleware)
    logger.debug("Creating agent type=%s tool_count=%d", type(agent).__name__, len(tools))
    return agent


# ── Agent factories ───────────────────────────────────────────────────────────

def create_sme_agent(tools: List[BaseTool]):
    return _make_agent(tools, agent_prompts.SME_SYSTEM_PROMPT, tool_limiter=ToolCallLimitMiddleware(run_limit=1, exit_behavior="continue"), semantic_middleware=ToolIntentMiddleware())

def create_boarding_agent(tools: List[BaseTool]):
    from shared.common.agents.middleware import JitPrivilegeMiddleware, ToolResultCondenserMiddleware
    return _make_agent(tools, agent_prompts.BOARDING_SYSTEM_PROMPT, tool_limiter=ToolCallLimitMiddleware(run_limit=2, exit_behavior="end"), semantic_middleware=ToolIntentMiddleware(), condenser=ToolResultCondenserMiddleware(), jit_middleware=JitPrivilegeMiddleware())

def create_applicant_agent(tools: List[BaseTool]):
    return _make_agent(tools, agent_prompts.APPLICANT_SYSTEM_PROMPT, tool_limiter=ToolCallLimitMiddleware(run_limit=1, exit_behavior="end"), semantic_middleware=ToolIntentMiddleware())


def create_workflow_agent(tools: List[BaseTool]):
    tool_limiter = ToolCallLimitMiddleware(run_limit=2, exit_behavior="end")
    return _make_agent(tools, agent_prompts.WORKFLOW_SYSTEM_PROMPT, tool_limiter=tool_limiter, semantic_middleware=ToolIntentMiddleware())

def create_guardian_agent(tools: List[BaseTool], response_format=None):
    agent = create_agent(
        model=get_chat_llm(),
        tools=tools,
        system_prompt=agent_prompts.GUARDIAN_SYSTEM_PROMPT,
        response_format=response_format,
    )
    if hasattr(agent, "model"):
        agent.model = agent.model.bind_tools(tools)
    return agent


async def create_multi_agent_map(env: str = "prod") -> dict:
    """Return an agent map of named specialized agents."""
    tools = await get_tools(env=env)
    by_cap = tools_list_by_capability(tools)
    return {
        "sme_agent":       create_sme_agent(by_cap["semantic"]),
        "underwriting_agent": create_boarding_agent(by_cap["admin"]),
        "applicant_agent":    create_applicant_agent(by_cap.get("applicant", [])),
        "workflow_agent":  create_workflow_agent(by_cap["workflow"]),
        "guardian_agent":  create_guardian_agent(by_cap.get("guardian", [])),
    }


# Agent maps are expensive to build — cache process-wide.
# Keyed by env ("prod" / "shadow") so each gets its own MCP client with the right X-Env header.
_cached_agent_maps: dict[str, dict] = {}
_agent_map_lock = asyncio.Lock()


async def get_cached_agent_map(env: str = "prod") -> dict:
    if env in _cached_agent_maps:
        return _cached_agent_maps[env]
    async with _agent_map_lock:
        if env not in _cached_agent_maps:
            logger.info("Building agent map env=%s (first request)", env)
            _cached_agent_maps[env] = await create_multi_agent_map(env=env)
            logger.info("Agent map cached env=%s — %d agents", env, len(_cached_agent_maps[env]))
    return _cached_agent_maps[env]


def clear_agent_map_cache() -> None:
    """Drop all cached agent maps so the next request rebuilds them."""
    _cached_agent_maps.clear()


# ── Entry points ──────────────────────────────────────────────────────────────


def _get_pydantic_ai_model(model: str | None = None):
    """Build a PydanticAI model for the current provider config (pydantic-ai 1.x API)."""
    if config.is_using_google():
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider
        cfg = config.get_llm_config_no_think(model)
        return GoogleModel(model_name=cfg["model"], provider=GoogleProvider(api_key=cfg.get("google_api_key")))
    if config.is_using_bedrock():
        from pydantic_ai.models.bedrock import BedrockModel
        from pydantic_ai.providers.bedrock import BedrockProvider
        cfg = config.get_llm_config_no_think(model)
        provider_kwargs = {"region_name": cfg.get("region_name")}
        if cfg.get("aws_access_key_id"):
            provider_kwargs["aws_access_key_id"] = cfg["aws_access_key_id"]
            provider_kwargs["aws_secret_access_key"] = cfg.get("aws_secret_access_key")
        return BedrockModel(cfg["model_id"], provider=BedrockProvider(**provider_kwargs))
    from pydantic_ai.models.ollama import OllamaModel
    from pydantic_ai.providers.ollama import OllamaProvider
    cfg = config.get_llm_config_no_think(model)
    base_url = cfg.get("base_url", "http://localhost:11434").rstrip("/") + "/v1"
    return OllamaModel(cfg["model"], provider=OllamaProvider(base_url=base_url))


def _insert_missing_commas(text: str) -> str:
    """Insert commas missing between JSON object properties and array elements."""
    import re
    # "value"\n  "key"  →  "value",\n  "key"
    text = re.sub(r'("(?:[^"\\]|\\.)*")\s*\n(\s*")', r'\1,\n\2', text)
    # }\n  {  →  },\n  {
    text = re.sub(r'}\s*\n(\s*\{)', r'},\n\1', text)
    # ]\n  {  or  ]\n  "  →  with comma
    text = re.sub(r']\s*\n(\s*[\[{"])', r'],\n\1', text)
    return text


def _quote_unquoted_keys(text: str) -> str:
    """Quote bare object keys: {subject: "x"} → {"subject": "x"}."""
    import re
    return re.sub(r'(?<=[{,])\s*([A-Za-z_]\w*)\s*:', lambda m: f' "{m.group(1)}":', text)


def parse_llm_json(blob: str, context: str = "") -> dict:
    """Extract and parse a JSON object from LLM text with progressive repair.

    Each pass applies a fresh transformation so repairs don't compound.
    Runs until first success; logs the raw blob on total failure.
    """
    import ast
    import json
    import re

    label = f" [{context}]" if context else ""

    # Pass 1 — plain
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        pass

    # Pass 2 — Python literals (True/False/None → true/false/null)
    p2 = re.sub(r'\bTrue\b', 'true', blob)
    p2 = re.sub(r'\bFalse\b', 'false', p2)
    p2 = re.sub(r'\bNone\b', 'null', p2)
    try:
        return json.loads(p2)
    except json.JSONDecodeError:
        pass

    # Pass 3 — ast.literal_eval (single-quoted strings, Python dict syntax)
    try:
        result = ast.literal_eval(blob)
        if isinstance(result, dict):
            return result
    except (ValueError, SyntaxError):
        pass

    # Pass 4 — quote unquoted keys: {key: "val"} → {"key": "val"}
    p4 = _quote_unquoted_keys(p2)
    try:
        return json.loads(p4)
    except json.JSONDecodeError:
        pass

    # Pass 5 — insert missing commas (on top of key-quoted blob)
    p5 = _insert_missing_commas(p4)
    try:
        return json.loads(p5)
    except json.JSONDecodeError:
        pass

    # Pass 6 — quote bare unquoted string values
    p6 = re.sub(
        r'("[\w]+"\s*:\s*)(?!")(?!\[)(?!\{)(?!-?\d)(?!true\b)(?!false\b)(?!null\b)([^,\n}\]]+)',
        lambda m: m.group(1) + '"' + m.group(2).strip() + '"',
        p5,
    )
    try:
        return json.loads(p6)
    except json.JSONDecodeError as exc:
        logger.warning("parse_llm_json%s all passes failed — blob: %s", label, blob[:400])
        raise ValueError(f"parse_llm_json{label}: all repair passes failed — {exc}") from exc


def _flat_schema(model_cls: type) -> str:
    """Return a human-readable field list for the prompt — avoids $defs/$ref that confuse small models."""
    import inspect
    import typing

    def _type_label(annotation) -> str:
        origin = getattr(annotation, "__origin__", None)
        args = getattr(annotation, "__args__", ())
        if origin is list:
            inner = _type_label(args[0]) if args else "any"
            return f"array of {inner} objects"
        if origin is typing.Union:
            non_none = [a for a in args if a is not type(None)]
            return _type_label(non_none[0]) if non_none else "string"
        if inspect.isclass(annotation) and hasattr(annotation, "model_fields"):
            sub = ", ".join(
                f"{n}: {_type_label(f.annotation)}" for n, f in annotation.model_fields.items()
            )
            return "{" + sub + "}"
        name = getattr(annotation, "__name__", str(annotation))
        return {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}.get(name, name)

    lines = []
    for name, field in model_cls.model_fields.items():
        desc = field.description or ""
        type_hint = _type_label(field.annotation)
        lines.append(f'  "{name}": {type_hint}' + (f'  // {desc[:80]}' if desc else ""))
    return "{\n" + ",\n".join(lines) + "\n}"


async def call_structured_agent(task: str, result_type: type, system_prompt: str = "", model: str | None = None):
    """Single-turn LLM call returning a validated Pydantic model.

    Uses call_chat_agent + JSON extraction — avoids pydantic-ai Agent retry
    machinery which is unreliable with local Ollama models.
    """
    import re
    schema_str = _flat_schema(result_type)
    full_system = (
        (system_prompt.rstrip() + "\n\n") if system_prompt else ""
    ) + (
        "Return ONLY a JSON object with the following fields filled in with your answers. "
        "Do NOT copy or return the schema — fill in real values. "
        "Enclose ALL string values in double quotes. No markdown fences, no extra text.\n"
        "Required output shape:\n" + schema_str
    )
    llm = get_chat_llm(model) if model else None
    raw = await call_chat_agent(task, system_prompt=full_system, llm=llm)
    logger.warning("call_structured_agent raw type=%s: %s", result_type.__name__, raw[:300])
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if not m:
        raise ValueError(f"call_structured_agent: no JSON in response for {result_type.__name__}")
    data = parse_llm_json(m.group(0), context=result_type.__name__)
    result = result_type.model_validate(data)
    logger.info("call_structured_agent exit result_type=%s", result_type.__name__)
    return result


async def call_chat_agent(
    prompt: str | list[BaseMessage],
    system_prompt: str | None = None,
    llm=None,
) -> str:
    """Async LLM call returning plain text. For structured output use call_structured_agent."""
    llm = llm or get_chat_llm()

    if isinstance(prompt, str):
        messages = [HumanMessage(content=prompt)]
        if system_prompt is not None:
            messages.insert(0, SystemMessage(content=system_prompt))
    else:
        messages = prompt

    from shared.common.infra.context import get_active_agent
    total_chars = sum(len(getattr(m, "content", "") or "") for m in messages)
    model_name  = getattr(llm, "model_name", None) or getattr(llm, "model", None) or "unknown"
    agent       = get_active_agent() or "—"
    logger.debug(
        "Invoking chat LLM: agent=%s model=%s msg_count=%d total_chars=%d has_system=%s",
        agent, model_name, len(messages), total_chars, system_prompt is not None,
    )
    result = await llm.ainvoke(messages)
    logger.info("Chat LLM completed: agent=%s model=%s msg_count=%d total_chars=%d", agent, model_name, len(messages), total_chars)
    from shared.common.infra.vendor import normalize_llm_content
    return normalize_llm_content(getattr(result, "content", result))


