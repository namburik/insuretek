"""ToolResultCondenserMiddleware — adapter that replaces large tool payloads
with compact summaries before they enter LangGraph message state.

Sits after ToolIntentMiddleware, before ToolLoopGuardMiddleware.
Only applied to write-heavy agents (boarding_agent). Error ToolMessages
are always passed through unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from shared.common.agents.bridge import AgentMiddleware
from shared.common.agents.bridge import ToolMessage

from shared.common.log import setup_structured_logging

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


def _condense(tool_name: str, content: Any) -> Any:
    data = _parse(content)
    if data is None:
        return content

    match tool_name:
        case "create_applicant":
            return f"applicant_id: {_extract(data, 'applicant_id')} ✓"
        case "update_applicant":
            return f"applicant_id: {_extract(data, 'applicant_id')} updated ✓"
        case _:
            return content


class ToolResultCondenserMiddleware(AgentMiddleware):
    """Replaces large write-tool payloads with compact summaries.

    Errors and blocked messages (status='error') pass through unchanged so
    the agent always sees the full error detail.
    """

    async def awrap_tool_call(self, request, handler) -> ToolMessage:
        result = await handler(request)
        if getattr(result, "status", None) == "error":
            return result
        name = request.tool_call.get("name", "")
        condensed = _condense(name, result.content)
        condensed_flag = condensed is not result.content
        if condensed_flag:
            logger.debug("ToolResultCondenser: tool=%s condensed payload", name)
            result.content = condensed
        logger.info("ToolResultCondenser: tool=%s condensed=%s status=%s", name, condensed_flag, getattr(result, "status", "ok"))
        return result
