"""Configuration-driven multi-agent Resolver.

Resolves each request to the best-matching agent via LLM intent classification,
then invokes that agent directly with per-node timeout and recursion limits.
"""

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from shared.common.agents.bridge import AIMessage, HumanMessage
from shared.common.agents.bridge import traceable

from shared.common.log import setup_structured_logging
from shared.common.infra import CorrelationContext, RequestContext, get_last_message_content, get_last_human_message_content, extract_result_content, load_config
from shared.common.infra.context import set_env, set_active_agent, set_audit_session_id, set_intent, set_guardian_cleared, with_token_phase
from shared.common.infra.audit_decorator import flush_pipeline_summary
from shared.common.agents.lang_agents import call_structured_agent, get_cached_agent_map, clear_agent_map_cache, get_request_tokens
from shared.common.router.prompts import INTENT_RESOLVER_SYSTEM, KEYWORD_ROUTES
from shared.policy import GuardianAgent, GuardianConfig, ReflectionConfig
from shared.policy import MetadataEnrichmentPolicy, MetadataConfig, SemanticSynthesisPolicy
from shared.policy.guard.guardian_dispatch_policy import GuardianDispatchPolicy
from shared.common.router.semantic_router import SemanticRouter
from shared.config import config

logger = setup_structured_logging(__name__)


class IntentResolution(BaseModel):
    agent_id: str = Field(description="Exact agent id from the provided list that best matches the intent")
    tool_hint: str = Field(default="", description="Specific tool name if the intent targets one tool, else empty")


# ── Module-level cache (private) ──────────────────────────────────────────────

_cached_executor: dict[str, "AgentExecutor"] = {}
_executor_lock = asyncio.Lock()


def _attach_correlation_to_trace(correlation_id: str) -> None:
    try:
        from shared.common.agents.bridge import get_current_run_tree
        run = get_current_run_tree()
        if run:
            run.extra = run.extra or {}
            run.extra["correlation_id"] = correlation_id
    except (ImportError, AttributeError):
        pass


def _summarize_result(result) -> str:
    if isinstance(result, dict):
        keys = list(result.keys())
        if len(keys) > 10:
            keys = keys[:10] + ["..."]
        summary = ["{...dict...}", f"keys={keys}"]
        messages = result.get("messages")
        if isinstance(messages, list):
            summary.append(f"messages={len(messages)}")
        metadata = result.get("metadata")
        if isinstance(metadata, dict):
            summary.append(f"metadata_domain={metadata.get('domain', 'unknown')}")
        return " ".join(summary)
    if hasattr(result, "content"):
        return str(result.content)
    return str(result)


# ── Config loader ─────────────────────────────────────────────────────────────

def load_resolver_config(config_path: str = None) -> dict:
    """Load and merge resolver_config.yaml + policy_config.yaml.

    policy_config.yaml keys take precedence over any same-named keys already
    present in resolver_config.yaml, so the dedicated policy file is the
    single source of truth for all policy parameters.
    """
    if config_path is None:
        config_path = Path(__file__).resolve().parents[2] / "config" / "resolver_config.yaml"
    cfg = load_config(config_path)
    policy_path = Path(config_path).parent / "policy_config.yaml"
    if policy_path.exists():
        policy_cfg = load_config(policy_path)
        cfg.update(policy_cfg)
    return cfg


# ── Per-request context ───────────────────────────────────────────────────────

@dataclass
class _InvokeCtx:
    """Mutable per-request state threaded through _ainvoke's phases."""
    messages: list
    last_msg: str
    source: str
    env: str
    session_id: str
    caller_id: str
    correlation_id: str
    target: str = ""
    routing_confidence: float = 0.0
    tool_hint: str = ""
    guardian_meta: dict | None = None
    reflection_meta: dict | None = None
    judge_meta: dict | None = None
    metadata_meta: dict | None = None


# ── Agent executor ────────────────────────────────────────────────────────────

class AgentExecutor:
    """Executes the selected agent after intent classification and optional guardian checks."""

    # ── Construction ──────────────────────────────────────────────────────────

    def __init__(self, router_config: dict = None, agent_map: dict = None):
        if router_config is None:
            router_config = load_resolver_config()
        self._agent_map = agent_map or {}
        self._semantic_router: SemanticRouter | None = None
        self._semantic_router_lock = asyncio.Lock()
        self._init_routing(router_config)
        self._init_guardian(router_config)
        self._pipeline = self._build_pipeline(router_config)

    def _init_routing(self, router_config: dict) -> None:
        graph_cfg = router_config.get("graph", {})
        self._nodes_cfg = graph_cfg.get("nodes", [])
        self._node_meta = {n["id"]: n for n in self._nodes_cfg}
        self._default_route = self._normalize_target(graph_cfg.get("default_route", "sme_agent"))
        self._default_timeout = graph_cfg.get("agent_timeout", 60)
        self._routing_mode = graph_cfg.get("routing_mode", "auto")
        self._quality_svc = None
        try:
            from core_data.service.policy_audit_service import QualityMetricsService
            self._quality_svc = QualityMetricsService()
        except Exception:
            pass

    def _init_guardian(self, router_config: dict) -> None:
        self._guardian = None
        self._guardian_dispatch = None
        if not config.is_guardian_enabled():
            return
        guardian_config = GuardianConfig.model_validate(router_config.get("guardian", {}))
        self._guardian = GuardianAgent(guardian_config)
        reflection = None
        if config.is_reflection_enabled():
            from shared.policy import GuardianReflectionPolicy
            reflection_config = ReflectionConfig.model_validate(router_config.get("reflection", {}))
            reflection = GuardianReflectionPolicy(self._guardian, reflection_config)
        self._guardian_dispatch = GuardianDispatchPolicy(self._guardian, reflection)

    def _build_pipeline(self, router_config: dict):
        from shared.policy.pipeline import PolicyPipeline, PolicyPipelineConfig
        from shared.policy.access.cost_policy import RateLimitConfig, TokenBudgetConfig
        from shared.policy.access.caller_authorization_policy import CallerAuthConfig
        from shared.policy.access.routing_policy import RoutingPolicy
        from shared.policy.guard.prompt_injection_policy import PromptInjectionConfig
        from shared.policy.output.sensitive_redaction_policy import SensitiveRedactionConfig

        judge = None
        if router_config.get("judge", {}).get("enabled"):
            from shared.policy import LLMJudge, JudgeConfig
            judge = LLMJudge(JudgeConfig.model_validate(router_config.get("judge", {})))

        hydration_policy = None
        if router_config.get("live_hydration", {}).get("enabled"):
            from shared.policy.context.hydration_policy import LiveHydrationPolicy, HydrationConfig
            hydration_policy = LiveHydrationPolicy(
                HydrationConfig.model_validate(router_config.get("live_hydration", {}))
            )

        routing_policy = RoutingPolicy.from_dict(router_config.get("routing", {}))

        metadata_policy = None
        semantic_synthesis_policy = None
        if router_config.get("metadata", {}).get("enabled"):
            metadata_config = MetadataConfig.model_validate(router_config.get("metadata", {}))
            metadata_policy = MetadataEnrichmentPolicy(metadata_config)
            semantic_synthesis_policy = SemanticSynthesisPolicy()

        return PolicyPipeline(
            PolicyPipelineConfig(
                rate_limit       = RateLimitConfig.model_validate(router_config.get("rate_limit", {})),
                prompt_injection = PromptInjectionConfig.model_validate(router_config.get("prompt_injection", {})),
                caller_auth      = CallerAuthConfig.model_validate(router_config.get("caller_auth", {})),
                token_budget     = TokenBudgetConfig.model_validate(router_config.get("token_budget", {})),
                sensitive_redact = SensitiveRedactionConfig.model_validate(router_config.get("sensitive_redact", {})),
            ),
            hydration=hydration_policy,
            routing=routing_policy,
            metadata_enrichment=metadata_policy,
            isar_synthesis=semantic_synthesis_policy,
            judge=judge,
            guardian=self._guardian,
        )

    def _normalize_target(self, target: str) -> str:
        if target in self._node_meta:
            return target
        for node in self._nodes_cfg:
            if node.get("category") == target:
                return node["id"]
        return target

    # ── Routing ───────────────────────────────────────────────────────────────

    async def _try_semantic_route(self, message: str, valid_ids: set[str]) -> tuple[str, float, str] | None:
        if self._semantic_router is None:
            async with self._semantic_router_lock:
                if self._semantic_router is None:
                    self._semantic_router = await SemanticRouter.build(self._nodes_cfg)
        routing_confidence: float = 0.0
        try:
            threshold = 0.35 if self._routing_mode == "auto" else 0.0
            target, routing_confidence = await self._semantic_router.route(message, default=self._default_route, threshold=threshold)
            if target in valid_ids:
                logger.info("Semantic router → %s (mode=%s, confidence=%.3f)", target, self._routing_mode, routing_confidence)
                return target, routing_confidence, ""
            logger.warning("Semantic router returned unknown id '%s'", target)
        except Exception as exc:
            logger.warning("Semantic router unavailable (%s)", exc)
        if self._routing_mode == "semantic":
            return self._default_route, routing_confidence, ""
        return None

    @with_token_phase("routing")
    async def _resolve_agent(self, message: str) -> tuple[str, float, str]:
        valid_ids = {n["id"] for n in self._nodes_cfg}

        msg_lower = message.lower()
        for pattern, agent_id in KEYWORD_ROUTES:
            if agent_id in valid_ids and re.search(pattern, msg_lower):
                logger.info("Keyword router → %s (pattern=%s)", agent_id, pattern)
                return agent_id, 1.0, ""

        if self._routing_mode != "intent":
            result = await self._try_semantic_route(message, valid_ids)
            if result is not None:
                return result

        agent_list = "\n".join(f'- {n["id"]}: {n.get("description", "")}' for n in self._nodes_cfg)
        system = INTENT_RESOLVER_SYSTEM.format(agent_list=agent_list)
        try:
            from shared.config import config as _cfg
            resolution = await call_structured_agent(
                message, IntentResolution, system_prompt=system, model=_cfg.get_resolver_model()
            )
            target = resolution.agent_id.lower().strip()
            if target in valid_ids:
                logger.info("LLM intent resolver → %s tool_hint=%s (mode=%s)", target, resolution.tool_hint or "—", self._routing_mode)
                return target, 0.0, resolution.tool_hint
            for token in target.split():
                if token in valid_ids:
                    logger.info("LLM intent resolver → %s tool_hint=%s (mode=%s)", token, resolution.tool_hint or "—", self._routing_mode)
                    return token, 0.0, resolution.tool_hint
            logger.warning("LLM resolver returned unknown agent '%s', falling back to '%s'", target, self._default_route)
        except Exception as e:
            logger.warning("LLM resolver failed (%s), falling back to '%s'", e, self._default_route)
        return self._default_route, 0.0, ""

    # ── Response builders ─────────────────────────────────────────────────────

    @staticmethod
    def _decline(messages: list, content: str) -> dict:
        return {"messages": list(messages) + [AIMessage(content=content)]}

    @staticmethod
    def _guardian_block(messages: list, reason: str, guardian_meta: dict, reflection_meta: dict | None = None,
                        prefix: str = "This operation has been blocked by the Insuretek Guardian.") -> dict:
        block_msg = f"{prefix}\n\nReason: {reason}"
        return {"messages": list(messages) + [AIMessage(content=block_msg)], "guardian": guardian_meta, "reflection": reflection_meta}

    # ── Agent runner ──────────────────────────────────────────────────────────

    async def _run_agent(self, agent, messages: list, max_steps: int, timeout: float, target: str) -> dict:
        """Stream the agent to completion, recovering a partial result if it hits the recursion limit."""
        logger.debug("Invoking agent %s of type %s", target, type(agent).__name__)
        from shared.common.agents.bridge import GraphRecursionError
        from shared.common.agents.bridge import ToolMessage as _ToolMessage
        from shared.common.infra.vendor import bedrock_inline_context_to_human
        messages = bedrock_inline_context_to_human(messages)

        _partial: dict = {}

        async def _stream() -> dict:
            async for chunk in agent.astream(
                {"messages": messages},
                {"recursion_limit": max_steps},
                stream_mode="values",
            ):
                _partial.update(chunk)
            return _partial

        try:
            return await asyncio.wait_for(_stream(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error("Agent '%s' timed out after %ds", target, timeout)
            raise
        except GraphRecursionError:
            _msgs = _partial.get("messages", [])
            _last_tool = next(
                (m for m in reversed(_msgs)
                 if isinstance(m, _ToolMessage) and m.status != "error"),
                None,
            )
            if _last_tool:
                logger.warning(
                    "Agent '%s' hit recursion limit but tool '%s' already succeeded — recovering partial result",
                    target, _last_tool.name,
                )
                return _partial
            logger.warning(
                "Agent '%s' hit recursion limit with no successful tool result — returning step-limit error",
                target,
            )
            return {
                "messages": list(_msgs) + [AIMessage(content=(
                    "I was unable to complete your request within the allowed steps. "
                    "Please try a more specific question."
                ))],
            }

    # ── Pipeline phases (in execution order) ──────────────────────────────────

    def _prepare_context(self, input: dict) -> _InvokeCtx:
        """Extract request fields, set request-lifecycle ContextVars, and build the per-request ctx."""
        messages = input.get("messages", [])
        last_msg = get_last_message_content(messages)
        source = input.get("source", "unknown")
        env = input.get("env", "prod")
        set_active_agent("")
        set_intent(get_last_human_message_content(messages))
        session_id = input.get("session_id", "")
        if session_id:
            set_audit_session_id(session_id)
        logger.info(
            "AgentExecutor.ainvoke env=%s source=%s session=%s caller=%s msg_len=%d",
            env, source,
            input.get("session_id", "")[:8] or "—",
            input.get("caller_id", "") or "—",
            len(last_msg),
        )
        return _InvokeCtx(
            messages=messages, last_msg=last_msg, source=source, env=env,
            session_id=input.get("session_id", ""),
            caller_id=input.get("caller_id", ""),
            correlation_id=input.get("correlation_id", ""),
        )

    async def _run_intake_control(self, ctx: _InvokeCtx) -> tuple[bool, dict | None]:
        """Pre-routing pipeline checks (rate limit, prompt injection, hydration)."""
        rc = RequestContext.from_invoke_ctx(ctx)
        intake_decision = await self._pipeline.check_intake(rc)
        if not intake_decision.allowed:
            logger.info("AgentExecutor: pipeline intake blocked reason=%s", intake_decision.reason)
            return False, self._decline(ctx.messages, intake_decision.reason)
        ctx.messages = intake_decision.metadata.get("messages", ctx.messages)
        return True, None

    async def _run_pre_route_safety(self, ctx: _InvokeCtx) -> tuple[bool, dict | None]:
        """Pre-route Guardian check — blocks outright on disallow without reflection (asymmetric vs. post-route)."""
        if self._guardian is None:
            return True, None
        pre_decision = await self._guardian.evaluate(ctx.last_msg, agent_id="pre_route", source=ctx.source, env=ctx.env)
        _pre_risk = pre_decision.metadata.get("risk_level", "UNKNOWN")
        _pre_needs_reflection = pre_decision.metadata.get("needs_reflection", False)
        logger.info(
            "Guardian: pre-route check source=%s allowed=%s risk=%s",
            ctx.source, pre_decision.allowed, _pre_risk,
        )
        if not pre_decision.allowed and not _pre_needs_reflection:
            pre_meta = {
                "allowed": pre_decision.allowed,
                "risk_level": _pre_risk,
                "reason": pre_decision.reason,
                "needs_reflection": _pre_needs_reflection,
            }
            return False, self._guardian_block(ctx.messages, pre_decision.reason, pre_meta)
        return True, None

    async def _resolve_target_agent(self, ctx: _InvokeCtx, forced_agent: str | None) -> None:
        """Resolve (or accept the forced) target agent, validate it, and mark it active."""
        if forced_agent:
            ctx.target = self._normalize_target(forced_agent)
            logger.info("Resolver: forced agent=%s env=%s", ctx.target, ctx.env)
        else:
            _routed, ctx.routing_confidence, ctx.tool_hint = await self._resolve_agent(ctx.last_msg)
            ctx.target = self._normalize_target(_routed)
            logger.info("Resolver: routed agent=%s mode=%s confidence=%.3f env=%s", ctx.target, self._routing_mode, ctx.routing_confidence, ctx.env)
            from shared.policy.metrics.routing_metrics import record_routing_confidence
            record_routing_confidence(
                agent_id=ctx.target,
                confidence=ctx.routing_confidence,
                routing_mode=self._routing_mode,
                session_id=ctx.session_id,
                correlation_id=ctx.correlation_id,
            )

        if ctx.target not in self._agent_map:
            raise ValueError(f"Agent '{ctx.target}' not found in agent_map")

        set_active_agent(ctx.target)

    async def _run_dispatch_control(self, ctx: _InvokeCtx) -> tuple[bool, dict | None]:
        """Post-routing pipeline checks (caller auth, cost, budget, routing, then hydration + metadata enrichment)."""
        full_text = " ".join(getattr(m, "content", "") or "" for m in ctx.messages)
        tokens_requested = self._pipeline.estimate_tokens(full_text)
        rc = RequestContext.from_invoke_ctx(ctx)
        rc.tokens_used = tokens_requested
        dispatch_decision = await self._pipeline.check_dispatch(rc)
        if not dispatch_decision.allowed:
            logger.info("AgentExecutor: pipeline dispatch blocked agent=%s reason=%s", ctx.target, dispatch_decision.reason)
            return False, self._decline(ctx.messages, dispatch_decision.reason)
        ctx.messages = dispatch_decision.metadata.get("messages", ctx.messages)
        ctx.metadata_meta = dispatch_decision.metadata.get("metadata")
        injected_chars = sum(len(getattr(m, "content", "") or "") for m in ctx.messages)
        logger.info(
            "AgentExecutor: dispatch context agent=%s msg_count=%d total_chars=%d est_tokens=%d",
            ctx.target, len(ctx.messages), injected_chars, tokens_requested,
        )
        return True, None

    async def _run_post_route_safety(self, ctx: _InvokeCtx) -> tuple[bool, dict | None]:
        """Post-route Guardian + Reflection — delegated to GuardianDispatchPolicy."""
        decision = await self._guardian_dispatch.evaluate(
            ctx.last_msg, ctx.messages, ctx.target, source=ctx.source, env=ctx.env,
        )
        ctx.guardian_meta  = decision.metadata.get("guardian")
        ctx.reflection_meta = decision.metadata.get("reflection")

        if not decision.allowed:
            return False, self._guardian_block(
                ctx.messages, decision.reason, ctx.guardian_meta, ctx.reflection_meta,
                prefix="This operation has been blocked.",
            )

        ctx.messages = decision.metadata.get("messages", ctx.messages)
        return True, None

    @with_token_phase("agent")
    async def _execute_agent(self, ctx: _InvokeCtx) -> dict:
        """Mark Guardian as cleared (if it ran) and stream the target agent to completion."""
        node_cfg = self._node_meta.get(ctx.target, {})
        max_steps = node_cfg.get("max_steps", 5)
        timeout = node_cfg.get("timeout", self._default_timeout)
        logger.info("Invoking agent=%s category=%s env=%s", ctx.target, node_cfg.get("category", ""), ctx.env)

        if ctx.guardian_meta is not None:
            set_guardian_cleared(True)

        agent = self._agent_map[ctx.target]
        return await self._run_agent(agent, ctx.messages, max_steps, timeout, ctx.target)

    async def _process_response_control(self, ctx: _InvokeCtx, result: dict) -> dict:
        """Post-execution pipeline check (PII egress/scrub), audit flush, judge evaluation, and finalization."""
        _msgs_for_response = result.get("messages", []) if isinstance(result, dict) else [result]
        _agent_reply = get_last_message_content(_msgs_for_response)
        _judge_text = extract_result_content(result) if isinstance(result, dict) else _agent_reply
        rc = RequestContext.from_invoke_ctx(ctx)
        rc.result = _judge_text
        _response_decision = await self._pipeline.check_response(rc)
        # Read after check_response so judge tokens are included in the count.
        tokens_used = get_request_tokens() or self._pipeline.estimate_tokens(_agent_reply)
        self._pipeline.consume_tokens(ctx.session_id, tokens_used)
        if not _response_decision.allowed:
            logger.info("AgentExecutor: pipeline blocked response reason=%s", _response_decision.reason)
            return self._decline(ctx.messages, "Response suppressed by policy.")
        if _response_decision.metadata.get("scrubbed_text"):
            _scrubbed = _response_decision.metadata["scrubbed_text"]
            if isinstance(result, dict) and result.get("messages"):
                result["messages"][-1] = AIMessage(content=_scrubbed)

        # Flush pipeline_summary BEFORE quality metrics so audit records appear in order.
        flush_pipeline_summary(CorrelationContext.get_or_generate())

        ctx.judge_meta = _response_decision.metadata.get("judge")

        return self._finalize_result(result, ctx.target, ctx.guardian_meta, ctx.reflection_meta, ctx.judge_meta, ctx.metadata_meta)

    def _finalize_result(self, result, target: str, guardian_meta, reflection_meta, judge_meta, metadata_meta) -> dict:
        """Stamp routing/policy metadata onto the agent result and log the outcome."""
        if not isinstance(result, dict):
            result = {"messages": [result]}
        result["routed_agent"] = target
        if guardian_meta is not None:
            result["guardian"] = guardian_meta
        if reflection_meta is not None:
            result["reflection"] = reflection_meta
        if judge_meta is not None:
            result["judge"] = judge_meta
        if metadata_meta is not None:
            result["metadata"] = metadata_meta
        logger.debug("Agent %s returned result type=%s result_summary=%s", target, type(result).__name__, _summarize_result(result))
        return result

    # ── Public entry point ────────────────────────────────────────────────────

    async def ainvoke(self, input: dict, forced_agent: str | None = None) -> dict:
        _correlation_id = input.get("correlation_id") or CorrelationContext.get_or_generate()
        env = input.get("env", "prod")
        env_token = set_env(env)
        try:
            return await self._ainvoke(input, forced_agent)
        finally:
            flush_pipeline_summary(_correlation_id)
            from shared.common.infra.context import _env_var
            _env_var.reset(env_token)

    async def _ainvoke(self, input: dict, forced_agent: str | None = None) -> dict:
        """Run each pipeline phase in order; any phase that disallows short-circuits the rest."""
        ctx = self._prepare_context(input)

        allowed, res = await self._run_intake_control(ctx)       # rate limit, prompt injection, hydration
        if not allowed: return res

        allowed, res = await self._run_pre_route_safety(ctx)     # pre-route Guardian — hard block only, no reflection
        if not allowed: return res

        await self._resolve_target_agent(ctx, forced_agent)       # resolve/validate target agent, mark active

        allowed, res = await self._run_dispatch_control(ctx)     # caller auth, cost, token budget, routing admission, metadata
        if not allowed: return res

        if self._guardian_dispatch is not None:
            allowed, res = await self._run_post_route_safety(ctx) # post-route Guardian + Reflection escalation
            if not allowed: return res

        result = await self._execute_agent(ctx)                   # stream target agent to completion
        result = await self._pipeline.synthesize(result)          # post-agent isar synthesis (owned by metadata policy)
        return await self._process_response_control(ctx, result)  # PII scrub, audit flush, judge evaluation


# ── Module-level helpers ──────────────────────────────────────────────────────

async def _get_cached_executor(env: str) -> AgentExecutor:
    if env in _cached_executor:
        return _cached_executor[env]
    async with _executor_lock:
        if env not in _cached_executor:
            agent_map = await get_cached_agent_map(env=env)
            _cached_executor[env] = AgentExecutor(load_resolver_config(), agent_map)
    return _cached_executor[env]


# ── Public utilities ──────────────────────────────────────────────────────────

def invalidate_agent_map() -> None:
    """Force the next request to rebuild the agent map, semantic router, and executor."""
    clear_agent_map_cache()
    _cached_executor.clear()


async def record_stream_tokens(session_id: str, env: str = "prod") -> None:
    """Debit accumulated request tokens for LLM calls that bypassed the resolver (e.g. stream_reply)."""
    tokens = get_request_tokens()
    if not tokens or not session_id:
        return
    executor = await _get_cached_executor(env)
    executor._pipeline.consume_tokens(session_id, tokens)
    logger.info("record_stream_tokens session=%s tokens=%d", session_id[:8], tokens)


# ── Public API ────────────────────────────────────────────────────────────────

async def call_resolver_safe(
    message: str,
    timeout: float,
    source: str = "unknown",
    env: str = "prod",
    session_id: str = "",
    correlation_id: str = "",
    caller_id: str = "",
) -> tuple[dict | None, str | None]:
    """Invoke call_resolver with a timeout. Returns (result, None) on success or (None, error_reply) on failure."""
    logger.info("call_resolver_safe enter source=%s env=%s timeout=%s caller=%s", source, env, timeout, caller_id or "—")
    try:
        result = await asyncio.wait_for(call_resolver(message, source=source, env=env, session_id=session_id, correlation_id=correlation_id, caller_id=caller_id), timeout=timeout)
        logger.info("call_resolver_safe exit source=%s env=%s result_type=%s result_summary=%s", source, env, type(result).__name__, _summarize_result(result))
        return result, None
    except asyncio.TimeoutError:
        logger.warning("RESOLVER_TIMEOUT after %ds", timeout)
        return None, (
            f"The request timed out after {int(timeout)} seconds. "
            "Please try again or simplify your request."
        )
    except Exception as exc:
        logger.error("RESOLVER_ERROR %s", exc, exc_info=True)
        return None, "Something went wrong while processing your request. Please try again."


@traceable(name="call_resolver", run_type="chain")
async def call_resolver(prompt: str, resolver_config: dict = None, agent_map: dict = None, forced_agent: str | None = None, source: str = "unknown", env: str = "prod", session_id: str = "", correlation_id: str = "", caller_id: str = ""):
    """Resolve and dispatch a prompt to the best-matching agent.

    Args:
        forced_agent: When set, skips intent classification and routes directly to this agent ID.
                      Use for workflow steps whose target agent is known at authoring time.
        env: "prod" or "shadow" — controls which DynamoDB tables the MCP tools write to.
    """
    correlation_id = correlation_id or CorrelationContext.get() or CorrelationContext.generate()
    CorrelationContext.set(correlation_id)
    _attach_correlation_to_trace(correlation_id)
    try:
        logger.info("Resolving prompt: agent=%s source=%s env=%s", forced_agent or "auto-resolve", source, env)
        logger.debug("Resolving prompt: '%s'", prompt)
        if resolver_config is None and agent_map is None:
            executor = await _get_cached_executor(env)
        else:
            if agent_map is None:
                agent_map = await get_cached_agent_map(env=env)
            executor = AgentExecutor(resolver_config, agent_map)
        result = await executor.ainvoke(
            {"messages": [HumanMessage(content=prompt)], "source": source, "env": env, "session_id": session_id, "correlation_id": correlation_id, "caller_id": caller_id},
            forced_agent=forced_agent,
        )
        routed_agent = result.get("routed_agent", forced_agent or "unknown") if isinstance(result, dict) else (forced_agent or "unknown")
        logger.info("Resolver dispatch completed: agent=%s source=%s env=%s forced=%s", routed_agent, source, env, bool(forced_agent))
        return result
    except Exception as e:
        raise RuntimeError(f"Resolver failed: {e}") from e
