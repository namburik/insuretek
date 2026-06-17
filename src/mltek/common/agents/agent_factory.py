"""Abstract agent factory — domains register their agent builders."""

from abc import ABC, abstractmethod
from typing import List, Callable, Any

from mltek.common.agents.bridge import BaseTool

# Global registry for domain agent factory
_agent_factory: "AgentFactory | None" = None


class AgentFactory(ABC):
    """Abstract interface for domain-specific agent creation."""

    @abstractmethod
    def create_agent(self, agent_id: str, agent_config: dict, tools: List[BaseTool], jit_policy: Any = None) -> Any:
        """Create an agent based on its configuration.

        Args:
            agent_id: The agent identifier (e.g., "sme_agent", "underwriting_agent")
            agent_config: Agent configuration from resolver_config.yaml (capability, tools, etc.)
            tools: Available tools filtered by capability
            jit_policy: Optional JIT elevation policy for write agents
        """
        ...


def register_agent_factory(factory: AgentFactory) -> None:
    """Register domain agent factory. Call once at startup."""
    global _agent_factory
    _agent_factory = factory


def get_agent_factory() -> AgentFactory | None:
    """Get registered domain agent factory, or None if not registered."""
    return _agent_factory
