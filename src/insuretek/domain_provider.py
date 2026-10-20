"""InsureTek concrete DomainPolicyProvider — registers domain implementations with mltek."""

from __future__ import annotations

import os

from mltek.common.infra import load_config
from mltek.domain_provider import DomainPolicyProvider

_CONFIG_PATH = os.getenv("DOMAIN_CONFIG_PATH", "src/insuretek/config")


class InsureTekDomainProvider(DomainPolicyProvider):
    """Provides InsureTek-specific policy implementations to the mltek resolver.

    policy_config.yaml is loaded once at construction and cached for the lifetime
    of this provider instance.
    """

    def __init__(self) -> None:
        self._cfg: dict = load_config(f"{_CONFIG_PATH}/policy_config.yaml")

    # ── Hydration / metadata ──────────────────────────────────────────────────

    def get_hydration_fetchers(self) -> dict:
        from insuretek.policy.context.hydration_fetchers import INSURETEK_FETCHERS
        return INSURETEK_FETCHERS

    def get_metadata_store(self):
        from insuretek.policy.context.metadata_store import StaticMetadataStore
        return StaticMetadataStore()

    def get_synthesis_policy(self):
        from insuretek.policy.output import SemanticSynthesisPolicy
        return SemanticSynthesisPolicy()

    def get_agent_domain_mapping(self) -> dict[str, str]:
        from insuretek.common.agents.prompts import AGENT_DOMAIN
        return AGENT_DOMAIN

    # ── Prompts / agents ──────────────────────────────────────────────────────

    def get_system_prompt(self, prompt_type: str) -> str | None:
        from insuretek.common.agents import prompts
        return {
            "tool_intent_policy": prompts.TOOL_INTENT_POLICY_SYSTEM_PROMPT,
            "llm_judge":          prompts.LLM_JUDGE_SYSTEM_PROMPT,
            "prompt_injection":   prompts.PROMPT_INJECTION_SYSTEM_PROMPT,
            "critic":             prompts.CRITIC_SYSTEM_PROMPT,
            "reformulator":       prompts.REFORMULATOR_SYSTEM_PROMPT,
            "guardian":           prompts.GUARDIAN_SYSTEM_PROMPT,
            "sme":                prompts.SME_SYSTEM_PROMPT,
            "applicant":          prompts.APPLICANT_SYSTEM_PROMPT,
            "workflow":           prompts.WORKFLOW_SYSTEM_PROMPT,
            "boarding":           prompts.BOARDING_SYSTEM_PROMPT,
        }.get(prompt_type)

    def get_guarded_agents(self) -> set[str]:
        from insuretek.common.agents.prompts import GUARDIAN_GUARDED_AGENTS
        return GUARDIAN_GUARDED_AGENTS

    def get_guarded_tools(self) -> set[str]:
        from insuretek.common.agents.prompts import GUARDIAN_GUARDED_TOOLS
        return GUARDIAN_GUARDED_TOOLS

    def get_agent_factory(self):
        from insuretek.common.agents.factory import InsureTekAgentFactory
        return InsureTekAgentFactory()

    # ── Policy config (all sourced from cached self._cfg) ─────────────────────

    def get_caller_auth_config(self):
        from mltek.policy.access.caller_authorization_policy import CallerAuthConfig
        return CallerAuthConfig.model_validate(self._cfg["caller_auth"])

    def get_agent_cost_config(self):
        from mltek.policy.access.cost_policy import AgentCostConfig
        return AgentCostConfig.model_validate(self._cfg["agent_cost"])

    def get_jit_elevation_config(self) -> dict:
        jit = self._cfg.get("jit_elevation", {})
        return {
            "restricted_tools": frozenset(jit.get("restricted_tools", [])),
            "blocked_phases":   frozenset(jit.get("blocked_phases", [])),
        }

    def get_guardian_config(self):
        from mltek.policy.guard.guardian_policy import GuardianConfig
        return GuardianConfig.model_validate(self._cfg["guardian"])

    def get_routing_config(self) -> dict:
        routing = self._cfg.get("routing", {})
        keyword_routes = [
            tuple(r) for r in routing.get("keyword_routes", [])
            if isinstance(r, (list, tuple)) and len(r) == 2
        ]
        return {
            "keyword_routes":         keyword_routes,
            "intent_resolver_system": routing.get("intent_resolver_system", ""),
        }

    def get_audit_config(self) -> dict:
        audit = self._cfg.get("audit", {})
        return {
            "audit_agents":    frozenset(audit.get("audit_agents", [])),
            "domain_keywords": tuple(
                (domain, tuple(kws))
                for domain, kws in audit.get("domain_keywords", [])
            ),
            "entity_kwarg_map": audit.get("entity_kwarg_map", {}),
        }

    def get_agent_factory_config(self) -> dict:
        return self._cfg.get("agents", {})

    def get_tool_condenser_config(self) -> dict:
        return self._cfg.get("tool_condenser", {})

    def get_evaluation_config(self) -> dict:
        return self._cfg.get("evaluation", {})

    # ── Entity patterns ───────────────────────────────────────────────────────

    def get_entity_patterns(self) -> dict[str, str]:
        return {
            "applicant": r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "workflow":  r"\bwf_[a-z0-9]{8,}\b",
        }

    # ── Audit persistence ─────────────────────────────────────────────────────

    def get_audit_writer(self):
        from core_data.service.policy_audit_service import get_audit_writer
        return get_audit_writer()
