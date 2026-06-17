"""MCP tool argument utilities."""

from mltek.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)


def normalize_id(value: str | None) -> str | None:
    """Strip whitespace and slashes from an ID — LLMs sometimes emit "/abc-123/"."""
    if not isinstance(value, str):
        return value
    return value.strip().strip("/")


def require_arg(arguments: dict, key: str) -> str:
    """Extract and normalize a required string argument, raising ValueError if absent or empty."""
    value = normalize_id(arguments.get(key))
    if not value:
        raise ValueError(f"{key} is required")
    return value


def unwrap_object_argument(arguments: dict) -> dict:
    """Unwrap {"object": {...}} → {...} for LLM providers that wrap tool arguments in a single key."""
    if isinstance(arguments, dict) and list(arguments.keys()) == ["object"] and isinstance(arguments["object"], dict):
        return arguments["object"]
    return arguments
