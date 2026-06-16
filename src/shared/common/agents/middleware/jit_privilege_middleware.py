"""JitPrivilegeMiddleware — cryptographic JIT privilege gate.

Sits at position 0 in the middleware stack (before ToolIntentMiddleware).
For each tool call:

1. Not a restricted tool → pass through immediately (zero overhead).
2. Restricted → evaluate session policy:
   - GRANTED → execute the tool call immediately (no extra LangGraph step).
   - DENIED  → return [JIT_DENIED] ToolMessage.

Single-step design: control and execution happen in the same middleware
invocation. No token round-trip, no extra graph step, no recursion risk.
"""

from __future__ import annotations

from shared.common.agents.bridge import AgentMiddleware
from shared.common.agents.bridge import ToolMessage

from shared.common.infra.context import get_jit_session
from shared.common.log import setup_structured_logging
from shared.policy.access.jit_elevation_policy import JitElevationPolicy

logger = setup_structured_logging(__name__)


class JitPrivilegeMiddleware(AgentMiddleware):
    """Enforces JIT privilege elevation for restricted tools.

    Args:
        policy: JitElevationPolicy instance. Defaults to module singleton.
    """

    def __init__(self, policy: JitElevationPolicy | None = None) -> None:
        super().__init__()
        self._policy = policy if policy is not None else JitElevationPolicy()

    async def awrap_tool_call(self, request, handler) -> ToolMessage:
        name = request.tool_call["name"]
        tid  = request.tool_call["id"]

        # ── Fast path: unrestricted tool ─────────────────────────────────────
        if name not in self._policy._restricted_tools:
            return await handler(request)

        phase, session_id, env = get_jit_session()

        decision = await self._policy.evaluate(
            tool_name=name,
            arguments=request.tool_call.get("args", {}),
            env=env,
            phase=phase,
        )

        if decision.allowed:
            logger.info(
                "JIT elevation GRANTED tool=%s phase=%s env=%s session=%s",
                name, phase, env, session_id[:8],
            )
            return await handler(request)

        logger.warning(
            "JIT elevation DENIED tool=%s phase=%s env=%s session=%s reason=%s",
            name, phase, env, session_id[:8], decision.reason,
        )
        return ToolMessage(
            content=f"[JIT_DENIED] {decision.reason}",
            tool_call_id=tid,
            name=name,
            status="error",
        )
