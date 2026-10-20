"""ToolIntentMiddleware — LLM-scored pre-tool-call intent alignment gate.

abefore_agent resolves the skip decision once: pre-clears if guardian has cleared or
intent is empty, sets _run_checked so awrap_tool_call skips LLM scoring entirely.
awrap_tool_call scores (tool_name, args, intent) against the threshold only when
_run_checked is False; blocked calls return a [INTENT_BLOCKED] ToolMessage.
"""

from __future__ import annotations

import contextvars

from pydantic import BaseModel, Field

from mltek.agents.bridge import AgentMiddleware, ToolMessage

from mltek.common.infra.context import get_intent, get_guardian_cleared, with_token_phase
from mltek.common.log import setup_structured_logging
from mltek.common.infra.audit_decorator import audit_inline

logger = setup_structured_logging(__name__)

_run_checked: contextvars.ContextVar[bool] = contextvars.ContextVar("_tool_intent_run_checked", default=False)


class ToolIntentScore(BaseModel):
    score: float = Field(description="Alignment score between 0.0 (misaligned) and 1.0 (clearly aligned)")
    reason: str = Field(description="One-sentence explanation of the score")



class ToolIntentConfig(BaseModel):
    threshold: float = 0.5

    @classmethod
    def from_dict(cls, d: dict) -> "ToolIntentConfig":
        return cls(threshold=float(d.get("threshold", 0.5)))


class ToolIntentMiddleware(AgentMiddleware):
    """Gates each tool call against the user's intent before execution.

    abefore_agent: resolves skip once — pre-clears if guardian cleared or intent is empty.
    awrap_tool_call: if not pre-cleared, scores (intent, tool_name, args) via LLM,
                     parses SCORE + REASON, blocks if score < threshold.
    """

    def __init__(self, config: ToolIntentConfig | None = None, system_prompt: str | None = None) -> None:
        super().__init__()
        self._config = config or ToolIntentConfig()
        self._system_prompt = system_prompt

    async def abefore_agent(self, state, runtime) -> dict | None:
        if get_guardian_cleared():
            _run_checked.set(True)
        elif not get_intent():
            logger.warning("ToolIntentPolicy: intent empty at agent start — pre-clearing, all tools default allow")
            _run_checked.set(True)
        else:
            _run_checked.set(False)
        return None

    async def awrap_tool_call(self, request, handler) -> ToolMessage:
        name   = request.tool_call["name"]
        args   = request.tool_call.get("args", {})
        tid    = request.tool_call["id"]

        if not _run_checked.get():
            intent = get_intent()
            score, reason = await self._score(name, args, intent)
            _run_checked.set(True)
            allowed = score >= self._config.threshold
            logger.info(
                "ToolIntentPolicy: tool=%s score=%.2f threshold=%.2f aligned=%s",
                name, score, self._config.threshold, allowed,
            )
            audit_inline(
                policy_type="tool_intent",
                operation=name,
                allowed=allowed,
                reason=reason,
                metadata={"score": round(score, 4), "threshold": self._config.threshold, "checked": True},
            )
            if not allowed:
                logger.warning("ToolIntentPolicy BLOCKED: tool=%s score=%.2f reason=%s", name, score, reason)
                return ToolMessage(
                    content=f"[INTENT_BLOCKED] score={score:.2f} — {reason}",
                    tool_call_id=tid,
                    name=name,
                    status="error",
                )
        else:
            logger.debug("ToolIntentPolicy: tool=%s pre-cleared", name)
            audit_inline(
                policy_type="tool_intent",
                operation=name,
                allowed=True,
                reason="Pre-cleared by guardian; intent check skipped",
                metadata={"score": None, "threshold": self._config.threshold, "checked": False},
            )

        return await handler(request)

    @with_token_phase("tool_intent")
    async def _score(self, tool_name: str, args: dict, intent: str) -> tuple[float, str]:
        from mltek.agents.lang_agents import call_structured_agent

        # Get system prompt from instance or domain provider
        system_prompt = self._system_prompt
        if not system_prompt:
            # Try to get from registered domain provider
            try:
                from mltek.common.router.resolver import _domain_provider
                if _domain_provider:
                    system_prompt = _domain_provider.get_system_prompt("tool_intent_policy")
            except (ImportError, AttributeError):
                pass

        if not system_prompt:
            logger.warning("ToolIntentPolicy: no system prompt available; defaulting allow")
            return 1.0, "Tool intent policy not configured; defaulting to allow."

        prompt = (
            f"User intent: {intent}\n\n"
            f"Proposed tool: {tool_name}\n"
            f"Arguments: {args}"
        )
        try:
            result = await call_structured_agent(prompt, ToolIntentScore, system_prompt=system_prompt)
            return result.score, result.reason
        except Exception as exc:
            logger.warning("ToolIntentPolicy LLM call failed: %s — defaulting allow", exc)
            return 1.0, "LLM scoring unavailable; defaulting to allow."
