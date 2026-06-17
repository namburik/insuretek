"""DomainPolicyProvider — abstract contract that domain apps implement to plug into mltek."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DomainPolicyProvider(ABC):
    """Domain apps implement this once and register it via `register_domain_provider()`.

    The resolver uses only this interface — it never imports from the domain package directly.
    Provides all domain-specific policies, prompts, and configuration.
    """

    @abstractmethod
    def get_hydration_fetchers(self) -> dict[str, Any]:
        """Return a mapping of entity_type → callable(entity_id: str) -> str."""
        ...

    @abstractmethod
    def get_metadata_store(self) -> Any:
        """Return a MetadataStore implementation for MetadataEnrichmentPolicy."""
        ...

    @abstractmethod
    def get_synthesis_policy(self) -> Any | None:
        """Return a post-agent synthesis policy (e.g. SemanticSynthesisPolicy), or None."""
        ...

    @abstractmethod
    def get_agent_domain_mapping(self) -> dict[str, str]:
        """Return mapping of agent_id → domain string for MetadataEnrichmentPolicy and HydrationPolicy.
        Example: {"merchant_agent": "merchant", "underwriting_agent": "underwriting"}
        """
        ...

    @abstractmethod
    def get_system_prompt(self, prompt_type: str) -> str | None:
        """Return a domain-specific system prompt for a given type.

        Common types: tool_intent_policy, llm_judge, prompt_injection,
                     critic, reformulator, guardian.
        Returns None if the prompt type is not applicable to this domain.
        """
        ...

    @abstractmethod
    def get_caller_auth_config(self) -> Any:
        """Return CallerAuthConfig (role→agent mapping) from domain's policy_config.yaml.

        Loaded from domain config file at runtime, allowing domain-specific role definitions.
        """
        ...

    @abstractmethod
    def get_agent_cost_config(self) -> Any:
        """Return AgentCostConfig (per-agent call limits) from domain's policy_config.yaml.

        Per-agent rate limits are domain-specific (different agents in different domains).
        """
        ...

    @abstractmethod
    def get_jit_elevation_config(self) -> dict[str, Any]:
        """Return JIT elevation policy config: restricted_tools and blocked_phases.

        restricted_tools: domain-specific write tools requiring JIT elevation
        blocked_phases: domain workflow states where no write tool is permitted
        Example: {"restricted_tools": ["create_merchant", "update_merchant"],
                  "blocked_phases": ["collecting"]}
        """
        ...

    @abstractmethod
    def get_guardian_config(self) -> Any:
        """Return GuardianConfig (threat detection rules) from domain's policy_config.yaml.

        Domain-specific: unguarded_agents, scope_keywords, name_fields vary by domain.
        """
        ...

    @abstractmethod
    def get_audit_config(self) -> dict[str, Any]:
        """Return audit configuration: buffering agents and domain classification keywords.

        Keys:
          - audit_agents: agents whose allowed controls are buffered
          - domain_keywords: (domain_name, keyword_tuple) pairs for intent classification
          - entity_kwarg_map: {kwarg_name → entity_type} for entity extraction
        """
        ...

    @abstractmethod
    def get_evaluation_config(self) -> dict[str, Any]:
        """Return response evaluation keywords per operation type.

        Keys:
          - {operation_type}: {expected: [keywords], excluded: [keywords]}
        Example:
          {"query": {"expected": ["merchant", "id"], "excluded": ["error"]}}
        """
        ...

    @abstractmethod
    def get_tool_condenser_config(self) -> dict[str, Any]:
        """Return tool result condenser rules: which write-tool results to condense.

        Keys:
          - {tool_name}: {extract_fields: [field_names], summary_template: str}
        Example:
          {"create_merchant": {"extract_fields": ["merchant_id"], "summary_template": "merchant_id: {0} ✓"}}
        """
        ...

    @abstractmethod
    def get_agent_factory_config(self) -> dict[str, Any]:
        """Return agent creation configuration: tool limits and middleware per agent type.

        Keys per agent type:
          - tool_limit: max tool calls per agent
          - exit_behavior: "continue" or "end"
          - middlewares: list of middleware names to apply
        """
        ...

    @abstractmethod
    def get_routing_config(self) -> dict[str, Any]:
        """Return router configuration: keyword routes and intent disambiguation rules.

        Keys:
          - keyword_routes: list of (regex_pattern, agent_id) tuples
          - intent_resolver_system: system prompt for LLM intent classification
        """
        ...

    @abstractmethod
    def get_entity_patterns(self) -> dict[str, str]:
        """Return domain entity ID patterns for hydration.

        Keys:
          - entity_type (e.g., "merchant", "workflow"): regex pattern to match IDs
        Example:
          {"merchant": "[0-9a-f]{8}...", "workflow": "wf_[a-z0-9]{8,}"}
        """
        ...

    def get_guarded_agents(self) -> set[str]:
        """Return agent IDs that require Guardian pre-flight checks. Override to customize."""
        return set()

    def get_guarded_tools(self) -> set[str]:
        """Return tool names that require Guardian checks. Empty set = all tools in guarded agents. Override to customize."""
        return set()
