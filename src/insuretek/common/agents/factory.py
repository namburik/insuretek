"""InsureTek-specific agent factory — implements abstract factory interface."""

from typing import List, Any
from mltek.common.agents.bridge import BaseTool
from mltek.common.agents.agent_factory import AgentFactory
from mltek.common.agents.middleware import JitPrivilegeMiddleware, ToolResultCondenserMiddleware
from mltek.common.agents.middleware import ToolIntentMiddleware
from mltek.common.agents.bridge import ToolCallLimitMiddleware
from mltek.common.agents.lang_agents import _make_agent, _get_domain_prompt


class InsureTekAgentFactory(AgentFactory):
    """InsureTek implementation of AgentFactory — creates agents based on resolver config."""

    # Mapping of agent_id patterns to prompt types and defaults
    _AGENT_CONFIG = {
        "sme_agent": {
            "prompt_type": "sme",
            "default_prompt": "You are an expert agent.",
            "tool_limit": 1,
            "exit_behavior": "continue",
            "middlewares": ["semantic_middleware"],
        },
        "underwriting_agent": {
            "prompt_type": "boarding",
            "default_prompt": "You are an admin agent for write operations.",
            "tool_limit": 2,
            "exit_behavior": "end",
            "middlewares": ["semantic_middleware", "jit_middleware", "condenser"],
        },
        "applicant_agent": {
            "prompt_type": "applicant",
            "default_prompt": "You are a lookup agent for applicants.",
            "tool_limit": 1,
            "exit_behavior": "end",
            "middlewares": ["semantic_middleware"],
        },
        "workflow_agent": {
            "prompt_type": "workflow",
            "default_prompt": "You are a workflow tracking agent.",
            "tool_limit": 2,
            "exit_behavior": "end",
            "middlewares": ["semantic_middleware"],
        },
    }

    def create_agent(self, agent_id: str, agent_config: dict, tools: List[BaseTool], jit_policy: Any = None) -> Any:
        """Create an agent based on resolver config."""
        # Look up defaults for this agent type
        defaults = self._AGENT_CONFIG.get(agent_id, {})
        if not defaults:
            raise ValueError(f"Unknown agent type: {agent_id}")

        # Get prompt from domain provider
        prompt_type = defaults.get("prompt_type")
        prompt = _get_domain_prompt(prompt_type) or defaults.get("default_prompt", "You are an agent.")

        # Build middleware stack
        middlewares = {}
        for mw_name in defaults.get("middlewares", []):
            if mw_name == "semantic_middleware":
                middlewares["semantic_middleware"] = ToolIntentMiddleware()
            elif mw_name == "jit_middleware":
                middlewares["jit_middleware"] = JitPrivilegeMiddleware(policy=jit_policy)
            elif mw_name == "condenser":
                middlewares["condenser"] = ToolResultCondenserMiddleware()

        # Create agent with config
        return _make_agent(
            tools,
            prompt,
            tool_limiter=ToolCallLimitMiddleware(
                run_limit=defaults.get("tool_limit", 1),
                exit_behavior=defaults.get("exit_behavior", "end")
            ),
            **middlewares
        )

