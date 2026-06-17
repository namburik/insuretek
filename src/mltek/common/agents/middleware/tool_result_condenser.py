"""ToolResultCondenserMiddleware — adapter that replaces large tool payloads
with compact summaries before they enter LangGraph message state.

Sits after ToolIntentMiddleware, before ToolLoopGuardMiddleware.
Only applied to write-heavy agents (boarding_agent). Error ToolMessages
are always passed through unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from mltek.common.agents.bridge import AgentMiddleware
from mltek.common.agents.bridge import ToolMessage

from mltek.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)


def _extract(data: dict, *keys: str) -> str:
    for key in keys:
        val = data.get(key)
        if val:
            return str(val)
    return "unknown"


def _parse(content: Any) -> dict | None:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _condense(tool_name: str, content: Any, condenser_config: dict | None = None) -> Any:
    """Condense tool result payload using domain rules from config.

    Args:
        tool_name: Name of the tool (e.g., "create_merchant")
        content: Tool result content (dict or JSON string)
        condenser_config: Domain tool condenser config from DomainPolicyProvider
    """
    data = _parse(content)
    if data is None:
        return content

    # No config = no condensing
    if not condenser_config:
        return content

    # Check if this tool has condensing rules
    tool_rule = condenser_config.get(tool_name)
    if not tool_rule:
        return content

    # Extract fields and format with template
    extract_fields = tool_rule.get("extract_fields", [])
    template = tool_rule.get("summary_template", "")

    if not extract_fields or not template:
        return content

    extracted_values = [_extract(data, *extract_fields)]
    try:
        return template.format(*extracted_values)
    except (IndexError, KeyError):
        return content


class ToolResultCondenserMiddleware(AgentMiddleware):
    """Replaces large write-tool payloads with compact summaries.

    Errors and blocked messages (status='error') pass through unchanged so
    the agent always sees the full error detail.

    Condenser rules loaded from domain config via DomainPolicyProvider.
    """

    def __init__(self, condenser_config: dict | None = None):
        super().__init__()
        self._condenser_config = condenser_config or {}
        if not condenser_config:
            # Load from domain provider if not provided
            try:
                from mltek.common.router.resolver import get_domain_provider
                provider = get_domain_provider()
                if provider:
                    self._condenser_config = provider.get_tool_condenser_config()
            except Exception:
                pass

    async def awrap_tool_call(self, request, handler) -> ToolMessage:
        result = await handler(request)
        if getattr(result, "status", None) == "error":
            return result
        name = request.tool_call.get("name", "")
        condensed = _condense(name, result.content, self._condenser_config)
        condensed_flag = condensed is not result.content
        if condensed_flag:
            logger.debug("ToolResultCondenser: tool=%s condensed payload", name)
            result.content = condensed
        logger.info("ToolResultCondenser: tool=%s condensed=%s status=%s", name, condensed_flag, getattr(result, "status", "ok"))
        return result
