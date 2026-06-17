"""RequestContext — centralized per-request state for threading through pipeline.

Consolidates scattered parameters (agent, correlation_id, source, env, etc.)
into a single mutable object that threads through resolver → policies → agents.

Replace scattered function signatures with single context parameter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RequestContext:
    """Mutable per-request state threaded through all pipeline phases.

    Primary use: reducer._ainvoke(ctx) instead of separate args for
    correlation_id, source, env, session_id, messages, etc.
    """

    # Core request metadata
    correlation_id: str
    source: str  # "web", "api", "internal", etc.
    env: str  # "prod", "shadow", etc.
    session_id: str
    caller_id: str = ""

    # Agent and routing state
    messages: list = field(default_factory=list)
    last_msg: str = ""
    target: str = ""  # Selected agent
    routing_confidence: float = 0.0
    tool_hint: str = ""

    # Policy decision metadata (populated by policies)
    guardian_meta: dict | None = None
    reflection_meta: dict | None = None
    judge_meta: dict | None = None
    metadata_meta: dict | None = None

    # Result and output (populated after agent execution)
    result: Any = None
    error: Exception | None = None

    # Token accounting
    tokens_used: int = 0

    # Audit trail
    policy_decisions: list = field(default_factory=list)

    def set_agent(self, agent_id: str, confidence: float = 1.0, tool_hint: str = "") -> None:
        """Record the selected agent after routing."""
        self.target = agent_id
        self.routing_confidence = confidence
        self.tool_hint = tool_hint

    def set_guardian_check(self, allowed: bool, reason: str = "", metadata: dict | None = None) -> None:
        """Record guardian policy decision."""
        self.guardian_meta = {"allowed": allowed, "reason": reason, **(metadata or {})}

    def set_result(self, result: Any) -> None:
        """Store agent execution result."""
        self.result = result

    def set_error(self, exc: Exception) -> None:
        """Store execution error."""
        self.error = exc

    def add_policy_decision(self, policy_name: str, allowed: bool, reason: str = "") -> None:
        """Record a policy check result."""
        self.policy_decisions.append({
            "policy": policy_name,
            "allowed": allowed,
            "reason": reason,
        })

    def is_blocked(self) -> bool:
        """True if any policy decision blocked the request."""
        return any(not d["allowed"] for d in self.policy_decisions)

    def summary(self) -> dict:
        """Return summary for logging."""
        return {
            "correlation_id": self.correlation_id,
            "source": self.source,
            "env": self.env,
            "agent": self.target,
            "confidence": self.routing_confidence,
            "messages": len(self.messages),
            "blocked": self.is_blocked(),
            "tokens": self.tokens_used,
            "policies": len(self.policy_decisions),
        }

    @classmethod
    def from_invoke_ctx(cls, invoke_ctx) -> RequestContext:
        """Convert legacy _InvokeCtx to new RequestContext."""
        ctx = cls(
            correlation_id=invoke_ctx.correlation_id,
            source=invoke_ctx.source,
            env=invoke_ctx.env,
            session_id=invoke_ctx.session_id,
            caller_id=invoke_ctx.caller_id,
            messages=invoke_ctx.messages,
            last_msg=invoke_ctx.last_msg,
            target=invoke_ctx.target,
            routing_confidence=invoke_ctx.routing_confidence,
            tool_hint=invoke_ctx.tool_hint,
            guardian_meta=invoke_ctx.guardian_meta,
            reflection_meta=invoke_ctx.reflection_meta,
            judge_meta=invoke_ctx.judge_meta,
            metadata_meta=invoke_ctx.metadata_meta,
        )
        return ctx

    def to_invoke_ctx(self):
        """Convert to legacy _InvokeCtx for backward compatibility."""
        from mltek.common.router.resolver import _InvokeCtx

        return _InvokeCtx(
            messages=self.messages,
            last_msg=self.last_msg,
            source=self.source,
            env=self.env,
            session_id=self.session_id,
            caller_id=self.caller_id,
            correlation_id=self.correlation_id,
            target=self.target,
            routing_confidence=self.routing_confidence,
            tool_hint=self.tool_hint,
            guardian_meta=self.guardian_meta,
            reflection_meta=self.reflection_meta,
            judge_meta=self.judge_meta,
            metadata_meta=self.metadata_meta,
        )
