"""Cost policies — sliding-window store, RateLimitPolicy, AgentCostPolicy, TokenBudgetPolicy."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections import deque
from threading import Lock
from typing import Any, Optional

from pydantic import BaseModel, Field

from mltek.common.infra.audit_decorator import audit_policy_decision
from mltek.common.log import setup_structured_logging
from mltek.policy.abstract_policy import Policy, PolicyDecision

logger = setup_structured_logging(__name__)


# ── Sliding-window store ───────────────────────────────────────────────────────

class RateLimitStore(ABC):
    @abstractmethod
    def prune_and_count(self, key: str, window_secs: float, now: float) -> int: ...
    @abstractmethod
    def record(self, key: str, now: float) -> None: ...


class InProcessRateLimitStore(RateLimitStore):
    """Thread-safe in-process sliding window store (single-instance deployments)."""

    def __init__(self) -> None:
        self._data: dict[str, deque] = {}
        self._lock = Lock()

    def prune_and_count(self, key: str, window_secs: float, now: float) -> int:
        with self._lock:
            dq = self._data.setdefault(key, deque())
            cutoff = now - window_secs
            while dq and dq[0] < cutoff:
                dq.popleft()
            return len(dq)

    def record(self, key: str, now: float) -> None:
        with self._lock:
            self._data.setdefault(key, deque()).append(now)


_shared_store: RateLimitStore | None = None


def get_rate_limit_store() -> RateLimitStore:
    """Return the process-wide in-process store (swap to Redis by replacing this singleton)."""
    global _shared_store
    if _shared_store is None:
        _shared_store = InProcessRateLimitStore()
    return _shared_store


_store_impl = get_rate_limit_store()


def _prune_and_count(key: str, window_secs: float, now: float) -> int:
    return _store_impl.prune_and_count(key, window_secs, now)


def _record(key: str, now: float) -> None:
    _store_impl.record(key, now)


# ── RateLimitPolicy ────────────────────────────────────────────────────────────

class RateLimitConfig(BaseModel):
    """Sliding-window rate limit config. Set session_max_calls or tenant_max_calls to 0 to disable."""
    session_window_secs: float = 60.0
    session_max_calls:   int   = 30
    tenant_window_secs:  float = 60.0
    tenant_max_calls:    int   = 200


class RateLimitPolicy(Policy):
    """Enforces sliding-window rate limits at session and tenant granularity."""

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self._config = config or RateLimitConfig()

    @audit_policy_decision(policy_type="rate_limit", operation_kwarg="session_id")
    async def evaluate(
        self,
        session_id: str = "",
        tenant_id:  str = "",
        **kwargs:   Any,
    ) -> PolicyDecision:
        cfg = self._config
        now = time.monotonic()

        if cfg.session_max_calls > 0 and session_id:
            session_key   = f"session:{session_id}"
            session_count = _prune_and_count(session_key, cfg.session_window_secs, now)
            if session_count >= cfg.session_max_calls:
                logger.info(
                    "RateLimitPolicy BLOCKED session=%s count=%d limit=%d window=%.0fs",
                    session_id[:8], session_count, cfg.session_max_calls, cfg.session_window_secs,
                )
                return PolicyDecision(
                    allowed=False,
                    reason=(
                        f"Session rate limit exceeded: {session_count} calls "
                        f"in {cfg.session_window_secs:.0f}s (max {cfg.session_max_calls})."
                    ),
                    metadata={"session_id": session_id, "count": session_count,
                               "limit": cfg.session_max_calls, "window": cfg.session_window_secs},
                )

        if cfg.tenant_max_calls > 0 and tenant_id:
            tenant_key   = f"tenant:{tenant_id}"
            tenant_count = _prune_and_count(tenant_key, cfg.tenant_window_secs, now)
            if tenant_count >= cfg.tenant_max_calls:
                logger.info(
                    "RateLimitPolicy BLOCKED tenant=%s count=%d limit=%d window=%.0fs",
                    tenant_id, tenant_count, cfg.tenant_max_calls, cfg.tenant_window_secs,
                )
                return PolicyDecision(
                    allowed=False,
                    reason=(
                        f"Tenant rate limit exceeded: {tenant_count} calls "
                        f"in {cfg.tenant_window_secs:.0f}s (max {cfg.tenant_max_calls})."
                    ),
                    metadata={"tenant_id": tenant_id, "count": tenant_count,
                               "limit": cfg.tenant_max_calls, "window": cfg.tenant_window_secs},
                )

        if session_id:
            _record(f"session:{session_id}", now)
        if tenant_id:
            _record(f"tenant:{tenant_id}", now)

        logger.info(
            "RateLimitPolicy allowed session=%s tenant=%s",
            session_id[:8] if session_id else "—", tenant_id or "—",
        )
        return PolicyDecision(
            allowed=True,
            reason="Within rate limits.",
            metadata={"session_id": session_id, "tenant_id": tenant_id},
        )


# ── AgentCostPolicy ────────────────────────────────────────────────────────────

# Domain-specific per-agent limits are supplied by:
# 1. policy_config.yaml in the domain's config directory, or
# 2. DomainPolicyProvider.get_agent_cost_config() at runtime.
# mltek has no hardcoded agent names (those are domain-specific).
_DEFAULT_AGENT_LIMITS: dict[str, int] = {}


class AgentCostConfig(BaseModel):
    """Per-agent rate limits. default_limit applies to agents not in agent_limits; 0 = unlimited."""

    window_secs:   float = 60.0
    agent_limits:  dict[str, int] = Field(default_factory=lambda: dict(_DEFAULT_AGENT_LIMITS))
    default_limit: int = 0


class AgentCostPolicy(Policy):
    """Enforces per-agent call limits. Key: agent_cost:{session_id}:{agent_id} — isolated from RateLimitPolicy keys."""

    def __init__(
        self,
        config: Optional[AgentCostConfig] = None,
        store: Optional[RateLimitStore] = None,
    ) -> None:
        self._config = config or AgentCostConfig()
        self._store = store or get_rate_limit_store()

    @audit_policy_decision(policy_type="agent_cost", operation_kwarg="agent_id")
    async def evaluate(
        self,
        session_id: str = "",
        agent_id: str = "",
        **kwargs: Any,
    ) -> PolicyDecision:
        limit = self._config.agent_limits.get(agent_id) or self._config.default_limit
        if not limit or not session_id:
            return PolicyDecision(
                allowed=True,
                reason="No agent cost limit configured.",
                metadata={"agent_id": agent_id},
            )

        now = time.monotonic()
        key = f"agent_cost:{session_id}:{agent_id}"
        count = self._store.prune_and_count(key, self._config.window_secs, now)

        if count >= limit:
            logger.info(
                "AgentCostPolicy BLOCKED session=%s agent=%s count=%d limit=%d window=%.0fs",
                session_id[:8] if session_id else "—",
                agent_id, count, limit, self._config.window_secs,
            )
            return PolicyDecision(
                allowed=False,
                reason=(
                    f"Agent '{agent_id}' rate limit exceeded: {count} calls "
                    f"in {self._config.window_secs:.0f}s (max {limit})."
                ),
                metadata={
                    "agent_id": agent_id,
                    "count": count,
                    "limit": limit,
                    "window": self._config.window_secs,
                },
            )

        self._store.record(key, now)
        logger.info(
            "AgentCostPolicy allowed session=%s agent=%s count=%d/%d",
            session_id[:8] if session_id else "—", agent_id, count + 1, limit,
        )
        return PolicyDecision(
            allowed=True,
            reason=f"Within agent cost limits ({count + 1}/{limit}).",
            metadata={"agent_id": agent_id, "count": count + 1, "limit": limit},
        )


# ── TokenBudgetPolicy ──────────────────────────────────────────────────────────

try:
    import tiktoken
    _tokenizer = tiktoken.get_encoding("cl100k_base")
    def count_tokens(text: str) -> int:
        return len(_tokenizer.encode(text))
except Exception:
    def count_tokens(text: str) -> int:  # type: ignore[misc]
        return len(text) // 4


_budgets: dict[str, int] = {}
_budget_lock = Lock()


def _get_used(session_id: str) -> int:
    with _budget_lock:
        return _budgets.get(session_id, 0)


def _add_used(session_id: str, tokens: int) -> int:
    with _budget_lock:
        new_total = _budgets.get(session_id, 0) + tokens
        _budgets[session_id] = new_total
        return new_total


def reset_session(session_id: str) -> None:
    """Clear the token counter for a session (e.g. on session expiry)."""
    with _budget_lock:
        _budgets.pop(session_id, None)


class TokenBudgetConfig(BaseModel):
    """Token budget config. Set max_session_tokens or max_request_tokens to 0 for unlimited."""
    max_session_tokens:        int   = 50_000
    portal_max_session_tokens: int   = 10_000
    max_request_tokens:        int   = 8_000
    warn_threshold:            float = 0.8


class TokenBudgetPolicy(Policy):
    """Enforces token caps. evaluate() checks; consume() records. Failed calls don't count against budget."""

    def __init__(self, config: TokenBudgetConfig | None = None) -> None:
        self._config = config or TokenBudgetConfig()

    def estimate(self, text: str) -> int:
        return count_tokens(text)

    def consume(self, session_id: str, tokens_used: int) -> int:
        """Record tokens_used against the session budget. Returns new total."""
        from mltek.common.infra.context import get_active_agent
        new_total = _add_used(session_id, tokens_used)
        agent = get_active_agent()
        logger.info(
            "TokenBudget consume: session=%s agent=%s added=%d total=%d limit=%d",
            session_id[:8] if session_id else "—",
            agent or "—",
            tokens_used, new_total, self._config.max_session_tokens,
        )
        return new_total

    @audit_policy_decision(policy_type="token_budget", operation_kwarg="session_id")
    async def evaluate(
        self,
        session_id:       str = "",
        tokens_requested: int = 0,
        caller_id:        str = "",
        **kwargs: Any,
    ) -> PolicyDecision:
        cfg        = self._config
        session_limit = (
            cfg.portal_max_session_tokens
            if caller_id == "portal" and cfg.portal_max_session_tokens > 0
            else cfg.max_session_tokens
        )
        used       = _get_used(session_id)
        projected  = used + tokens_requested
        meta = {
            "session_id":       session_id,
            "tokens_used":      used,
            "tokens_requested": tokens_requested,
            "tokens_projected": projected,
            "limit":            session_limit,
        }

        if cfg.max_request_tokens > 0 and tokens_requested > cfg.max_request_tokens:
            logger.info(
                "TokenBudgetPolicy BLOCKED: request too large session=%s requested=%d limit=%d",
                session_id[:8] if session_id else "—", tokens_requested, cfg.max_request_tokens,
            )
            return PolicyDecision(
                allowed=False,
                reason=(
                    f"Request size {tokens_requested} tokens exceeds per-request limit "
                    f"of {cfg.max_request_tokens} tokens."
                ),
                metadata={**meta, "violation": "request_limit"},
            )

        if session_limit > 0 and projected > session_limit:
            logger.info(
                "TokenBudgetPolicy BLOCKED: session exhausted session=%s projected=%d limit=%d caller=%s",
                session_id[:8] if session_id else "—", projected, session_limit, caller_id or "—",
            )
            return PolicyDecision(
                allowed=False,
                reason=(
                    f"Session token budget exhausted: {used} used + {tokens_requested} requested "
                    f"= {projected} exceeds limit of {session_limit}."
                ),
                metadata={**meta, "violation": "session_limit"},
            )

        if session_limit > 0 and projected >= cfg.warn_threshold * session_limit:
            logger.warning(
                "TokenBudgetPolicy WARNING: session=%s at %.0f%% of budget (%d/%d) caller=%s",
                session_id[:8] if session_id else "—",
                100 * projected / session_limit,
                projected, session_limit, caller_id or "—",
            )

        logger.info(
            "TokenBudgetPolicy allowed: session=%s projected=%d limit=%d caller=%s",
            session_id[:8] if session_id else "—", projected, session_limit, caller_id or "—",
        )
        return PolicyDecision(
            allowed=True,
            reason=f"Session within token budget: {projected} projected (used {used} + request {tokens_requested}) of {session_limit} limit ({100 * projected // session_limit}% used, {session_limit - projected} remaining).",
            metadata=meta,
        )
