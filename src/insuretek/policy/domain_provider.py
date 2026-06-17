"""InsureTek concrete DomainPolicyProvider — registers domain implementations with mltek."""

from __future__ import annotations

from mltek.config.domain_provider import DomainPolicyProvider


class InsureTekDomainProvider(DomainPolicyProvider):
    """Provides InsureTek-specific policy implementations to the mltek resolver."""

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

    def get_system_prompt(self, prompt_type: str) -> str | None:
        from insuretek.common.agents import prompts
        prompt_map = {
            "tool_intent_policy": prompts.TOOL_INTENT_POLICY_SYSTEM_PROMPT,
            "llm_judge": prompts.LLM_JUDGE_SYSTEM_PROMPT,
            "prompt_injection": prompts.PROMPT_INJECTION_SYSTEM_PROMPT,
            "critic": prompts.CRITIC_SYSTEM_PROMPT,
            "reformulator": prompts.REFORMULATOR_SYSTEM_PROMPT,
            "guardian": prompts.GUARDIAN_SYSTEM_PROMPT,
            "sme": prompts.SME_SYSTEM_PROMPT,
            "applicant": prompts.APPLICANT_SYSTEM_PROMPT,
            "workflow": prompts.WORKFLOW_SYSTEM_PROMPT,
            "boarding": prompts.BOARDING_SYSTEM_PROMPT,
        }
        return prompt_map.get(prompt_type)

    def get_guarded_agents(self) -> set[str]:
        from insuretek.common.agents.prompts import GUARDIAN_GUARDED_AGENTS
        return GUARDIAN_GUARDED_AGENTS

    def get_guarded_tools(self) -> set[str]:
        from insuretek.common.agents.prompts import GUARDIAN_GUARDED_TOOLS
        return GUARDIAN_GUARDED_TOOLS

    def get_caller_auth_config(self):
        from mltek.common.infra import load_config
        from mltek.policy.access.caller_authorization_policy import CallerAuthConfig
        import os

        domain_config_path = os.getenv("DOMAIN_CONFIG_PATH", "src/insuretek/config")
        policy_cfg = load_config(f"{domain_config_path}/policy_config.yaml")
        return CallerAuthConfig.model_validate(policy_cfg["caller_auth"])

    def get_agent_cost_config(self):
        from mltek.common.infra import load_config
        from mltek.policy.access.cost_policy import AgentCostConfig
        import os

        domain_config_path = os.getenv("DOMAIN_CONFIG_PATH", "src/insuretek/config")
        policy_cfg = load_config(f"{domain_config_path}/policy_config.yaml")
        return AgentCostConfig.model_validate(policy_cfg["agent_cost"])

    def get_jit_elevation_config(self) -> dict:
        from mltek.common.infra import load_config
        import os

        domain_config_path = os.getenv("DOMAIN_CONFIG_PATH", "src/insuretek/config")
        policy_cfg = load_config(f"{domain_config_path}/policy_config.yaml")
        jit_cfg = policy_cfg.get("jit_elevation", {})
        # Convert lists to frozensets for policy
        return {
            "restricted_tools": frozenset(jit_cfg.get("restricted_tools", [])),
            "blocked_phases": frozenset(jit_cfg.get("blocked_phases", [])),
        }

    def get_guardian_config(self):
        from mltek.common.infra import load_config
        from mltek.policy.guard.guardian_policy import GuardianConfig
        import os

        domain_config_path = os.getenv("DOMAIN_CONFIG_PATH", "src/insuretek/config")
        policy_cfg = load_config(f"{domain_config_path}/policy_config.yaml")
        return GuardianConfig.model_validate(policy_cfg["guardian"])

    def get_entity_patterns(self) -> dict[str, str]:
        """Return entity ID patterns for hydration."""
        return {
            "applicant": r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "workflow": r"\bwf_[a-z0-9]{8,}\b",
        }

    def get_routing_config(self) -> dict:
        from mltek.common.infra import load_config
        import os

        domain_config_path = os.getenv("DOMAIN_CONFIG_PATH", "src/insuretek/config")
        policy_cfg = load_config(f"{domain_config_path}/policy_config.yaml")
        routing_cfg = policy_cfg.get("routing", {})

        # Convert keyword routes list of lists to list of tuples
        keyword_routes = []
        for route in routing_cfg.get("keyword_routes", []):
            if isinstance(route, (list, tuple)) and len(route) == 2:
                keyword_routes.append(tuple(route))

        return {
            "keyword_routes": keyword_routes,
            "intent_resolver_system": routing_cfg.get("intent_resolver_system", ""),
        }

    def get_agent_factory_config(self) -> dict:
        from mltek.common.infra import load_config
        import os

        domain_config_path = os.getenv("DOMAIN_CONFIG_PATH", "src/insuretek/config")
        policy_cfg = load_config(f"{domain_config_path}/policy_config.yaml")
        return policy_cfg.get("agents", {})

    def get_tool_condenser_config(self) -> dict:
        from mltek.common.infra import load_config
        import os

        domain_config_path = os.getenv("DOMAIN_CONFIG_PATH", "src/insuretek/config")
        policy_cfg = load_config(f"{domain_config_path}/policy_config.yaml")
        return policy_cfg.get("tool_condenser", {})

    def get_evaluation_config(self) -> dict:
        from mltek.common.infra import load_config
        import os

        domain_config_path = os.getenv("DOMAIN_CONFIG_PATH", "src/insuretek/config")
        policy_cfg = load_config(f"{domain_config_path}/policy_config.yaml")
        return policy_cfg.get("evaluation", {})

    def get_audit_config(self) -> dict:
        from mltek.common.infra import load_config
        import os

        domain_config_path = os.getenv("DOMAIN_CONFIG_PATH", "src/insuretek/config")
        policy_cfg = load_config(f"{domain_config_path}/policy_config.yaml")
        audit_cfg = policy_cfg.get("audit", {})
        return {
            "audit_agents": frozenset(audit_cfg.get("audit_agents", [])),
            "domain_keywords": tuple(
                (domain, tuple(keywords))
                for domain, keywords in audit_cfg.get("domain_keywords", [])
            ),
            "entity_kwarg_map": audit_cfg.get("entity_kwarg_map", {}),
        }
