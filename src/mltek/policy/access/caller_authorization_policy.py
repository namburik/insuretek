"""CallerAuthorizationPolicy — role-based agent invocation control.

Built-in roles: READ_ONLY (query agents) → OPERATOR (+ write agents) → ADMIN (all).
Default resolver promotes every caller to ADMIN until wired to a real identity store.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, Field, model_validator

from mltek.common.infra.audit_decorator import audit_policy_decision
from mltek.common.log import setup_structured_logging
from mltek.policy.abstract_policy import Policy, PolicyDecision

logger = setup_structured_logging(__name__)


# ── Role definitions ───────────────────────────────────────────────────────────

class CallerRole(str, Enum):
    READ_ONLY = "read_only"
    OPERATOR  = "operator"
    ADMIN     = "admin"


# Default empty role→agent mapping. Domain-specific roles are supplied by:
# 1. policy_config.yaml in the domain's config directory, or
# 2. DomainPolicyProvider.get_caller_auth_config() at runtime.
# mltek has no hardcoded agent names (those are domain-specific).
_DEFAULT_ROLE_AGENTS: dict[CallerRole, frozenset[str]] = {
    CallerRole.ADMIN: frozenset(),  # empty = all agents permitted
}


# ── Config ─────────────────────────────────────────────────────────────────────

class CallerAuthConfig(BaseModel):
    """Role→agent mapping config. ADMIN with empty frozenset allows all agents."""
    role_agents:  dict[CallerRole, frozenset[str]] = Field(
        default_factory=lambda: dict(_DEFAULT_ROLE_AGENTS)
    )
    default_role: CallerRole = CallerRole.ADMIN

    @model_validator(mode="before")
    @classmethod
    def _coerce_role_agents(cls, data: dict) -> dict:
        if not isinstance(data, dict):
            return data
        if "default_role" in data and isinstance(data["default_role"], str):
            try:
                data = {**data, "default_role": CallerRole(data["default_role"])}
            except ValueError:
                data = {k: v for k, v in data.items() if k != "default_role"}
        if "role_agents" in data:
            role_agents: dict[CallerRole, frozenset[str]] = {}
            for role_str, agents in data["role_agents"].items():
                try:
                    role_agents[CallerRole(role_str)] = frozenset(agents)
                except ValueError:
                    pass
            data = {**data, "role_agents": {**_DEFAULT_ROLE_AGENTS, **role_agents}}
        return data


# ── Policy ─────────────────────────────────────────────────────────────────────

class CallerAuthorizationPolicy(Policy):
    """Checks whether a caller's role permits invoking the given agent."""

    def __init__(
        self,
        config:        CallerAuthConfig | None = None,
        role_resolver: Callable[[str], CallerRole | None] | None = None,
    ) -> None:
        self._config   = config or CallerAuthConfig()
        self._resolver = role_resolver or (lambda _: None)

    def _resolve_role(self, caller_id: str) -> CallerRole:
        role = self._resolver(caller_id)
        return role if role is not None else self._config.default_role

    def _is_permitted(self, role: CallerRole, agent_id: str) -> bool:
        allowed_agents = self._config.role_agents.get(role, frozenset())
        # Empty frozenset for ADMIN means all agents are permitted
        return not allowed_agents or agent_id in allowed_agents

    @audit_policy_decision(policy_type="caller_auth", operation_kwarg="agent_id")
    async def evaluate(
        self,
        caller_id:  str = "",
        agent_id:   str = "",
        session_id: str = "",
        **kwargs:   Any,
    ) -> PolicyDecision:
        role = self._resolve_role(caller_id)
        meta = {"caller_id": caller_id, "agent_id": agent_id, "role": role.value, "session_id": session_id}

        if self._is_permitted(role, agent_id):
            logger.info(
                "CallerAuthorizationPolicy GRANTED caller=%s agent=%s role=%s session=%s",
                caller_id or "—", agent_id, role.value, session_id[:8] if session_id else "—",
            )
            return PolicyDecision(
                allowed=True,
                reason=f"Role '{role.value}' is authorized to invoke '{agent_id}'.",
                metadata=meta,
            )

        logger.info(
            "CallerAuthorizationPolicy DENIED caller=%s agent=%s role=%s session=%s",
            caller_id or "—", agent_id, role.value, session_id[:8] if session_id else "—",
        )
        return PolicyDecision(
            allowed=False,
            reason=(
                f"Role '{role.value}' is not authorized to invoke '{agent_id}'. "
                f"Permitted agents: {sorted(self._config.role_agents.get(role, set()))}."
            ),
            metadata=meta,
        )
