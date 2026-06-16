"""Configuration-driven multi-agent dispatcher."""

from .resolver import load_resolver_config, AgentExecutor, call_resolver, call_resolver_safe

__all__ = [
    "load_resolver_config",
    "AgentExecutor",
    "call_resolver",
    "call_resolver_safe",
]
