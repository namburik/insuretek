"""MCP tool argument utilities."""

from typing import Any, Dict

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


def format_quality_summary(data: Dict[str, Any] | None) -> str:
    if not data:
        return "No quality metrics found."
    r = data.get("routing", {})
    j = data.get("judge", {})
    agent  = data.get("agent_id") or "all agents"
    window = data.get("window_hours", 24)
    return "\n".join([
        f"**Quality Summary** — agent `{agent}` · window {window}h",
        "",
        "| Signal | Total | Value |",
        "|--------|-------|-------|",
        f"| Routing confidence | {r.get('total', 0)} | avg {r.get('avg_confidence', 0):.4f} · {r.get('below_threshold', 0)} below threshold |",
        f"| Judge verdict | {j.get('total', 0)} | {'pass rate {:.2%}'.format(j['pass_rate']) if j.get('pass_rate') is not None else 'no data'} · avg confidence {j.get('avg_confidence', 0):.4f} |",
    ])
