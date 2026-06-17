"""RoutingPolicy — declarative (env, agent) → block-message rules; add constraints without editing the resolver."""

from __future__ import annotations

from typing import Any

from mltek.common.log import setup_structured_logging
from mltek.policy.abstract_policy import Policy, PolicyDecision
from mltek.common.infra.audit_decorator import audit_policy_decision

logger = setup_structured_logging(__name__)

# Default rules: (env, agent) → user-facing block message.
# None as env or agent acts as a wildcard.
_DEFAULT_RULES: dict[tuple[str | None, str | None], str] = {
    ("shadow", "sme_agent"): "Advanced Queries are not available in shadow mode.",
}


class RoutingPolicy(Policy):
    """Evaluates whether a resolved agent may run in the current env.

    Match order: exact (env, agent) → wildcard env (None, agent) → wildcard agent (env, None).
    Load from YAML via RoutingPolicy.from_dict(router_config.get("routing", {})).
    """

    def __init__(
        self,
        rules: dict[tuple[str | None, str | None], str] | None = None,
    ) -> None:
        self._rules: dict[tuple[str | None, str | None], str] = dict(
            _DEFAULT_RULES if rules is None else rules
        )

    @classmethod
    def from_dict(cls, d: dict) -> "RoutingPolicy":
        """Load routing rules from YAML config dict.

        Expected format:
          rules:
            - env: shadow        # use "*" or omit for wildcard
              agent: sme_agent   # use "*" or omit for wildcard
              message: "..."
        """
        raw_rules = d.get("rules", [])
        rules: dict[tuple[str | None, str | None], str] = {}
        for entry in raw_rules:
            env   = entry.get("env")   or None
            agent = entry.get("agent") or None
            msg   = entry.get("message", "")
            if env == "*":   env   = None
            if agent == "*": agent = None
            rules[(env, agent)] = msg
        return cls(rules=rules or None)

    @audit_policy_decision(policy_type="routing", operation_kwarg="agent")
    async def evaluate(
        self,
        env: str = "prod",
        agent: str = "",
        **kwargs: Any,
    ) -> PolicyDecision:
        for key in ((env, agent), (None, agent), (env, None)):
            if key in self._rules:
                reason = self._rules[key]
                logger.info("RoutingPolicy BLOCKED env=%s agent=%s reason=%s", env, agent, reason)
                return PolicyDecision(allowed=False, reason=reason, metadata={"env": env, "agent": agent})

        logger.info("RoutingPolicy allowed: agent=%s env=%s", agent, env)
        return PolicyDecision(allowed=True, reason=f"Routed to {agent} in {env}", metadata={"env": env, "agent": agent})
