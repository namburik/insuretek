"""Common setup utilities for agent demos."""

import asyncio
from mltek.common.log import setup_structured_logging
import json
import re
from mltek.common.agents.bridge import traceable

logger = setup_structured_logging(__name__)


def get_last_message(messages: list):
    """Extract the last message from a messages list.
    
    Args:
        messages: List of messages
        
    Returns:
        The last message object, or None if list is empty
    """
    return messages[-1] if messages else None


def get_last_message_content(messages: list) -> str:
    """Extract content from the last message in a list.

    Args:
        messages: List of message objects

    Returns:
        Content string from last message, or empty string if not found
    """
    last_msg = get_last_message(messages)
    if last_msg is None:
        return ""

    from mltek.common.infra.vendor import normalize_stream_chunk
    from mltek.common.tools.format import normalize_tool_response_text

    if hasattr(last_msg, 'content'):
        content = last_msg.content
        if content is None:
            return ""
        if isinstance(content, (dict, list)):
            return normalize_tool_response_text(normalize_stream_chunk(content))
        return normalize_tool_response_text(content)

    if isinstance(last_msg, (dict, list)):
        return normalize_tool_response_text(normalize_stream_chunk(last_msg))
    return normalize_tool_response_text(last_msg)


def get_last_human_message_content(messages: list) -> str:
    """Extract the last human/user message from the messages list.

    This skips assistant and tool messages, returning the last user-originated
    content that should represent intent.
    """
    for message in reversed(messages or []):
        if _is_tool_message(message) or _is_assistant_message(message):
            continue
        return get_last_message_content([message])
    return ""


def _message_role(message) -> str:
    if isinstance(message, dict):
        return str(
            message.get("role")
            or message.get("recipient")
            or message.get("type")
            or ""
        ).lower()
    return type(message).__name__.lower()


def _is_assistant_message(message) -> bool:
    role = _message_role(message)
    return any(token in role for token in ("assistant", "ai", "assistant_message", "ai_message"))


def _is_tool_message(message) -> bool:
    role = _message_role(message)
    return any(token in role for token in ("tool", "tool_result", "tool_use", "function_call"))


def _is_agent_limit_error(content: str) -> bool:
    lc = content.lower()
    return "tool call limit" in lc or "run limit exceeded" in lc


def extract_tool_message_content(result: dict, tool_list: set[str] | None = None) -> str | None:
    """Return normalized content from a ToolMessage in result["messages"], or None.

    Three cases:
      1. Tool-call limit hit  → first successful tool message (forward iteration).
      2. Known tool called (name in tool_list) → last successful tool message (reverse).
      3. No matching tool messages → None (caller falls back to AI message).

    tool_list: set of tool names to filter by.
               When None, any tool message is accepted (no name filter).
    """
    messages = result.get("messages", [])
    tool_msgs = [
        m for m in messages
        if hasattr(m, "tool_call_id") or "tool" in type(m).__name__.lower()
    ]

    if tool_list is not None:
        tool_msgs = [m for m in tool_msgs if getattr(m, "name", None) in tool_list]

    if not tool_msgs:
        return None

    limit_hit = any(_is_agent_limit_error(get_last_message_content([m]) or "") for m in tool_msgs)
    for m in (tool_msgs if limit_hit else reversed(tool_msgs)):
        raw = get_last_message_content([m])
        if raw and not _is_agent_limit_error(raw):
            return raw
    return None


@traceable(name="extract_result_content")
def extract_result_content(result, dominant: str = "ai") -> str:
    """Extract a readable content string from a result object.

    dominant="ai"   — prefer the last assistant/AI message (default, user-facing reply)
    dominant="tool" — prefer the last tool message (raw structured data, bypasses LLM reformulation)
    """
    if isinstance(result, dict) and "messages" in result:
        messages = result.get("messages", [])

        # Build a list of (msg, content, clsname) for non-empty contents.
        # Skip system and human messages — they are prompt context, never responses.
        candidates = []
        for m in messages:
            cls = type(m).__name__.lower()
            if "system" in cls or "human" in cls:
                continue
            raw = get_last_message_content([m])
            if raw is None:
                continue
            # Coerce non-string contents (lists/dicts/etc.) to a string for uniform handling
            if not isinstance(raw, str):
                try:
                    c = json.dumps(raw, ensure_ascii=False)
                except Exception:
                    c = str(raw)
            else:
                c = raw

            if c and c.strip():
                candidates.append((m, c, type(m).__name__.lower()))

        def _pick_ai():
            ai_candidates = [(m, c, cls) for m, c, cls in candidates
                             if _is_assistant_message(m) or "ai" in cls or "assistant" in cls]
            if any("tool call limit" in c.lower() for _, c, _ in ai_candidates):
                return None
            return ai_candidates[-1][1] if ai_candidates else None

        def _pick_tool():
            tool_cands = [
                (m, c, cls) for m, c, cls in candidates
                if _is_tool_message(m) or "tool" in cls or hasattr(m, "name") or hasattr(m, "tool_call_id")
            ]
            limit_hit = any(_is_agent_limit_error(c) for _, c, _ in tool_cands)
            for _, c, _ in (tool_cands if limit_hit else reversed(tool_cands)):
                if not _is_agent_limit_error(c):
                    return c
            return None

        primary, fallback = ( _pick_ai,_pick_tool) if dominant != "tool" else (_pick_tool, _pick_ai)
        picked = primary() or fallback()
        if picked:
            return picked

        if candidates:
            return candidates[-1][1]

        return None

    if isinstance(result, dict):
        from mltek.common.tools.format import normalize_tool_response_text
        for key in ["content", "text", "message", "response", "reply", "output", "result"]:
            if key in result:
                normalized = normalize_tool_response_text(result[key])
                if normalized and normalized.strip():
                    return normalized
        return normalize_tool_response_text(result)

    if isinstance(result, list):
        from mltek.common.tools.format import normalize_tool_response_text
        return normalize_tool_response_text(result)

    return str(result)


_EXTRACT_STATE_PATTERNS = {
    "uuid":           r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    "pol_pattern":    r"pol_[0-9a-f]{16}",
    "price_pattern":  r"price_[0-9a-fA-F]{16}",
    "client_pattern": r"client_[0-9a-fA-F]{16}",
}


def extract_json_from_text(text: str) -> dict | None:
    """Find the first JSON object in a free-form text string."""
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text, start)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
    return None


def extract_json_object(text: str) -> str | None:
    """Extract the first complete balanced JSON object from a string."""
    if not isinstance(text, str):
        return None

    start = text.find('{')
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def repair_truncated_json(text: str) -> str:
    """Close open JSON structures and strip incomplete trailing syntax."""
    s = text.rstrip()

    if s.endswith(','):
        s = s[:-1].rstrip()

    stack: list[str] = []
    in_string = False
    escape = False
    str_start = -1

    for i, ch in enumerate(s):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            if in_string:
                in_string = False
                str_start = -1
            else:
                in_string = True
                str_start = i
            continue
        if in_string:
            continue
        if ch == '{':
            stack.append('}')
        elif ch == '[':
            stack.append(']')
        elif ch in ('}', ']') and stack and stack[-1] == ch:
            stack.pop()

    if in_string and str_start != -1:
        before_str = s[:str_start].rstrip()
        if before_str and before_str[-1] in (',', '{', '['):
            s = before_str
            if s.endswith(','):
                s = s[:-1].rstrip()
        else:
            s += '"'

    s += ''.join(reversed(stack))
    return s


def extract_state_value(text: str, spec: str | dict) -> str | None:
    """Extract a value from text using a named pattern, regex, or JSON key."""
    if isinstance(spec, str):
        pattern = _EXTRACT_STATE_PATTERNS.get(spec, spec)
        m = re.search(pattern, str(text), re.IGNORECASE)
        if not m:
            return None
        return m.group(1) if m.lastindex else m.group(0)

    if isinstance(spec, dict):
        if "regex" in spec:
            m = re.search(spec["regex"], str(text), re.IGNORECASE)
            if m:
                return m.group(1) if m.lastindex else m.group(0)
        if "json_key" in spec:
            key = spec["json_key"]
            for obj_m in re.finditer(r"\{[^{}]*\}", str(text)):
                fragment = obj_m.group()
                if key not in fragment:
                    continue
                try:
                    obj = json.loads(fragment)
                    if key in obj and obj[key] is not None:
                        return str(obj[key])
                except Exception:
                    pass
    return None


def extract_plain_content(response_content) -> str:
    """Normalize various response content types to a readable string.

    - Strings are returned as-is.
    - Lists of strings are joined with spaces.
    - Other objects are JSON-dumped when possible, otherwise stringified.
    """
    from mltek.common.tools.format import normalize_tool_response_text

    if response_content is None:
        return ""
    if isinstance(response_content, str):
        return normalize_tool_response_text(response_content)
    if isinstance(response_content, list):
        joined = " ".join(str(i) for i in response_content if isinstance(i, str))
        return normalize_tool_response_text(joined or json.dumps(response_content, ensure_ascii=False))
    try:
        return json.dumps(response_content, ensure_ascii=False)
    except Exception:
        return str(response_content)



def deep_merge(base: dict, update: dict) -> dict:
    """Recursively merge *update* into *base* so nested dicts accumulate."""
    result = dict(base)
    for k, v in update.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


async def run_async_demo(demo_coro, demo_name: str):
    """Run an asynchronous demo with standard error handling and logging.
    
    Args:
        demo_coro: Awaitable coroutine that returns demo result
        demo_name: Name of the demo for logging
    """
    try:
        logger.info(f"Starting {demo_name}...")
        result = await demo_coro
        logger.info(f"{demo_name} completed successfully")
        return result
    except asyncio.TimeoutError:
        logger.error("Operation timed out")
        return None
    except ConnectionError as e:
        logger.error(f"Connection error: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error: {type(e).__name__}: {e}", exc_info=True)
        logger.error(f"ERROR: {type(e).__name__}: {e}")
        return None
    finally:
        logger.info(f"{demo_name} finished")
