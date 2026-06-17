"""ToolLoopGuardMiddleware — hash-based tool call loop detector.

Detects when an agent calls the same MCP tool with identical arguments more than
once within a single agent run. On the first call the tool executes normally and
the result is cached. On subsequent identical calls the handler is skipped and a
synthetic ToolMessage is returned containing the cached result and a LOOP_DETECTED
prefix, preventing infinite reasoning loops without losing the information.

State is stored in a ContextVar keyed per invocation. The abefore_agent hook
resets the state at the start of each agent.ainvoke(), so cached agent instances
never bleed state across requests.
"""

import contextvars
import hashlib
import json
from typing import Any

from mltek.common.agents.bridge import AgentMiddleware
from mltek.common.agents.bridge import ToolMessage

from mltek.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)

_dedup_seen: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "_dedup_seen", default=None
)


def _compute_call_key(name: str, args: Any) -> str:
    try:
        payload = json.dumps({"tool": name, "args": args}, sort_keys=True)
    except (TypeError, ValueError):
        payload = repr({"tool": name, "args": args})
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class ToolLoopGuardMiddleware(AgentMiddleware):
    """Deduplicates repeated (tool, args) calls within one agent run.

    On the first identical call: executes via handler and caches the result.
    On subsequent identical calls beyond max_duplicate_count: returns a synthetic
    ToolMessage with the cached result and a LOOP_DETECTED prefix — no tool
    execution occurs.

    Args:
        max_duplicate_count: Number of allowed calls with identical (name, args)
            before blocking. Default 1 means the 2nd identical call is blocked.
    """

    def __init__(self, *, max_duplicate_count: int = 1) -> None:
        super().__init__()
        if max_duplicate_count < 1:
            raise ValueError(f"max_duplicate_count must be >= 1, got {max_duplicate_count}")
        self.max_duplicate_count = max_duplicate_count

    async def abefore_agent(self, state, runtime) -> dict | None:
        _dedup_seen.set({})
        return None

    async def awrap_tool_call(self, request, handler) -> ToolMessage:
        name = request.tool_call["name"]
        args = request.tool_call.get("args", {})
        tid  = request.tool_call["id"]
        key  = _compute_call_key(name, args)

        seen = _dedup_seen.get()
        if seen is None:
            seen = {}
            _dedup_seen.set(seen)
        entry = seen.get(key)

        if entry and entry["count"] >= self.max_duplicate_count:
            logger.warning(
                "TOOL_LOOP_DETECTED tool=%s hash=%s call_count=%d",
                name, key, entry["count"] + 1,
            )
            return ToolMessage(
                content=(
                    f"[LOOP_DETECTED] '{name}' was already called with identical arguments. "
                    f"Previous result: {entry['result']}. Use this result; do not call again."
                ),
                tool_call_id=tid,
                name=name,
                status="error",
            )

        result = await handler(request)

        if isinstance(result, ToolMessage):
            call_count = (entry["count"] + 1) if entry else 1
            seen[key] = {"count": call_count, "result": str(result.content)[:500]}
            logger.info("ToolLoopGuard: tool=%s hash=%s call_count=%d status=%s", name, key, call_count, getattr(result, "status", "ok"))

        return result
