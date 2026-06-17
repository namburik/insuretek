"""Tool policies — rule-based control and JIT phase-aware privilege elevation."""

from __future__ import annotations

import re
from abc import abstractmethod
from typing import Any

from mltek.common.log import setup_structured_logging
from mltek.policy.abstract_policy import Policy, PolicyDecision
from mltek.common.infra.audit_decorator import audit_policy_decision

logger = setup_structured_logging(__name__)


class ToolPolicy(Policy):

    @abstractmethod
    async def evaluate(
        self,
        tool_name: str,
        arguments: dict,
        source: str = "unknown",
        env: str = "prod",
        **kwargs: Any,
    ) -> PolicyDecision:
        ...


class RuleBasedToolPolicy(ToolPolicy):
    """Allow/deny tool policy. Evaluation order: allowed_tools → denied_tools → denied_argument_patterns."""

    def __init__(
        self,
        allowed_tools: set[str] | None = None,
        denied_tools: set[str] | None = None,
        denied_argument_patterns: dict[str, str] | None = None,
    ):
        self.allowed_tools = allowed_tools
        self.denied_tools = denied_tools or set()
        self._denied_arg_patterns = {
            k: re.compile(v) for k, v in (denied_argument_patterns or {}).items()
        }

    @audit_policy_decision(policy_type="rule_based_tool", operation_kwarg="tool_name")
    async def evaluate(
        self,
        tool_name: str,
        arguments: dict,
        source: str = "unknown",
        env: str = "prod",
        **kwargs: Any,
    ) -> PolicyDecision:
        if self.allowed_tools is not None and tool_name not in self.allowed_tools:
            logger.info("RuleBasedToolPolicy DENIED not_in_allowlist tool=%s source=%s env=%s", tool_name, source, env)
            return PolicyDecision(
                allowed=False,
                reason=f"Tool '{tool_name}' is not permitted in this environment.",
                metadata={"tool_name": tool_name, "source": source, "env": env},
            )

        if tool_name in self.denied_tools:
            logger.info("RuleBasedToolPolicy DENIED explicit_deny tool=%s source=%s env=%s", tool_name, source, env)
            return PolicyDecision(
                allowed=False,
                reason=f"Tool '{tool_name}' is explicitly denied by policy.",
                metadata={"tool_name": tool_name, "source": source, "env": env},
            )

        for arg_name, pattern in self._denied_arg_patterns.items():
            arg_value = arguments.get(arg_name)
            if isinstance(arg_value, str) and pattern.search(arg_value):
                logger.info("RuleBasedToolPolicy DENIED arg_pattern tool=%s arg=%s source=%s env=%s", tool_name, arg_name, source, env)
                return PolicyDecision(
                    allowed=False,
                    reason=f"Tool argument '{arg_name}' matches denied pattern.",
                    metadata={"tool_name": tool_name, "arg_name": arg_name, "pattern": pattern.pattern},
                )

        logger.info("RuleBasedToolPolicy allowed: tool=%s source=%s env=%s", tool_name, source, env)
        return PolicyDecision(
            allowed=True,
            reason="Allowed by rule-based tool policy.",
            metadata={"tool_name": tool_name, "source": source, "env": env},
        )


# Domain-specific restricted tools are supplied by:
# 1. policy_config.yaml in the domain's config directory, or
# 2. DomainPolicyProvider.get_jit_elevation_config() at runtime.
# mltek has no default restricted tools (those are domain-specific write operations).
RESTRICTED_TOOLS: frozenset[str] = frozenset()

# Phases in which no write tool may be granted regardless of env.
# Domain-specific blocked phases are supplied by:
# 1. policy_config.yaml in the domain's config directory, or
# 2. DomainPolicyProvider.get_jit_elevation_config() at runtime.
# mltek has no default blocked phases (those are domain-specific states).
_DEFAULT_BLOCKED_PHASES: frozenset[str] = frozenset()


class JitElevationPolicy(ToolPolicy):

    def __init__(
        self,
        restricted_tools: frozenset[str] | None = None,
        blocked_phases: frozenset[str] | None = None,
    ) -> None:
        self._restricted_tools = restricted_tools if restricted_tools is not None else RESTRICTED_TOOLS
        self._blocked_phases = blocked_phases if blocked_phases is not None else _DEFAULT_BLOCKED_PHASES

    @audit_policy_decision(policy_type="jit_elevation", operation_kwarg="tool_name")
    async def evaluate(
        self,
        tool_name: str,
        arguments: dict,
        source: str = "unknown",
        env: str = "prod",
        phase: str = "collecting",
        **kwargs: Any,
    ) -> PolicyDecision:
        meta = {"tool_name": tool_name, "env": env, "phase": phase}

        if tool_name not in self._restricted_tools:
            # Unrestricted — should not reach this path via JitPrivilegeMiddleware,
            # but guard defensively.
            logger.info("JIT policy: unrestricted tool=%s phase=%s", tool_name, phase)
            return PolicyDecision(allowed=True, reason="Unrestricted tool.", metadata=meta)

        if phase in self._blocked_phases:
            logger.info("JIT policy: DENIED blocked phase=%s tool=%s", phase, tool_name)
            return PolicyDecision(
                allowed=False,
                reason=(
                    f"Tool '{tool_name}' requires session phase 'offering_optional' or later. "
                    f"Current phase: '{phase}'."
                ),
                metadata=meta,
            )

        logger.info("JIT policy: GRANTED tool=%s phase=%s env=%s", tool_name, phase, env)
        return PolicyDecision(
            allowed=True,
            reason=f"Elevation granted for tool '{tool_name}' at phase '{phase}'.",
            metadata=meta,
        )
