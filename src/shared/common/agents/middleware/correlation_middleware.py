"""CorrelationMiddleware — injects the current correlation ID into every tool call.

The MCP server is a separate process and cannot read CorrelationContext directly.
This middleware stamps the active correlation_id from the resolver's process into
the tool call args before execution, ensuring the MCP tool always receives the
correct ID for the current request regardless of what the LLM included.
"""

from shared.common.agents.bridge import AgentMiddleware, ToolMessage
from shared.common.infra.context import CorrelationContext
from shared.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)


class CorrelationMiddleware(AgentMiddleware):
    async def awrap_tool_call(self, request, handler) -> ToolMessage:
        correlation_id = CorrelationContext.get()
        if correlation_id:
            args = request.tool_call.get("args", {})
            if isinstance(args, dict):
                request = request.override(
                    tool_call={**request.tool_call, "args": {**args, "correlation_id": correlation_id}}
                )
        return await handler(request)
