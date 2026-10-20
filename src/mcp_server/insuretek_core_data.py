"""MCP Server for Insuretek Core Data Tools.

Applicant Tools:
    - create_applicant: Create a new insurance applicant and initiate underwriting intake
    - get_applicant: Retrieve an applicant by applicant_id
    - search_applicants_by_policy_type: Filter applicants by policy type
    - get_all_applicants: List all applicants with pagination
    - update_applicant: Partial update an existing applicant record
    - quote_applicant: Generate a premium quote for an applicant
    - delete_applicant: Soft-delete an applicant record

Policy Tools:
    - create_policy: Create or replace the active policy for an applicant
    - get_active_policy: Retrieve the current active policy
    - get_policy_history: List historical policy records
    - bind_policy: Bind a quoted policy to BOUND status
    - cancel_policy: Cancel the active policy

GraphRAG Tools:
    - find_similar_applicants: Find similar entities using graph scoring
    - query_applicant_graph: Answer natural-language queries against the knowledge graph

Run: python3 src/mcp_server/insuretek_core_data.py [--port PORT] [--log-level LEVEL] [--json-response]
"""

from typing import Sequence
import asyncio

from mcp.server.lowlevel import Server
from mcp.types import Tool, TextContent, ImageContent, EmbeddedResource
from mltek.common.starlette_app import StreamableStarletteApp, run_starlette_app

from mltek.config.config_common import Config
from mltek.common.infra.context import set_app_name, set_env, get_env
from mltek.common.router.resolver import register_domain_provider
from insuretek.domain_provider import InsureTekDomainProvider

set_app_name("insuretek")
register_domain_provider(InsureTekDomainProvider())
from mltek.common.infra.error_helpers import handle_mcp_tool_errors
from core_data.service import  ApplicantService
from mltek.common.log.logging_config import setup_structured_logging
from mcp_server.utilities.tools_schemas import get_tool_schema, get_tool_description
from mcp_server.utilities.constants import InsurtekTools
from mltek.common.tools.format import format_tool_result
from mcp_server.handlers.graphrag_tools import GraphRAGHandlers
from mcp_server.handlers.applicant_tools import ApplicantHandlers
from mcp_server.handlers.policy_tools import PolicyHandlers
from core_data.service.graph_service import GraphService

logger = setup_structured_logging(__name__)


def validate_required_arguments(arguments: dict, required_keys: list[str]) -> None:
    for key in required_keys:
        if key not in arguments:
            raise ValueError(f"Missing required argument: {key}")


class CoreDataServer:
    def __init__(self):
        self._executor = None
        self.graphrag = GraphRAGHandlers(GraphService())
        applicant_svc = ApplicantService()
        self.applicant = ApplicantHandlers(service=applicant_svc)
        self.policy = PolicyHandlers(service=applicant_svc)
        logger.debug("Insuretek Core Data Server initialized")

    def _get_executor(self):
        if self._executor is None:
            from concurrent.futures import ThreadPoolExecutor
            self._executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="insuretek_db_")
            logger.debug("ThreadPoolExecutor created with 5 workers")
        return self._executor

    async def _run_in_executor(self, func, *args):
        import contextvars
        loop = asyncio.get_event_loop()
        ctx = contextvars.copy_context()
        return await loop.run_in_executor(self._get_executor(), ctx.run, func, *args)

    def shutdown(self):
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            logger.debug("ThreadPoolExecutor shut down")


class EnvMiddleware:
    """Reads X-Env header and sets env context variable for per-request DynamoDB table routing."""

    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            raw_env = headers.get(b"x-env", b"prod").decode()
            env = raw_env if raw_env in ("prod", "shadow") else "prod"
            token = set_env(env)
            try:
                await self._app(scope, receive, send)
            finally:
                from contextvars import Token
                if isinstance(token, Token):
                    from mltek.common.infra.context import _env_var
                    _env_var.reset(token)
        else:
            await self._app(scope, receive, send)


def main(
    port: int,
    log_level: str,
    json_response: bool,
) -> int:
    logger.info(f"Insuretek Core Data MCP Server Starting on port {port} (log_level={log_level})")

    try:
        Config.validate()
    except ValueError as e:
        logger.error(f"Configuration validation failed: {e}")
        return 1

    server = Server("core_data_server")
    core_data_server = CoreDataServer()

    @server.call_tool()
    @handle_mcp_tool_errors
    async def call_tool(
        name: str, arguments: dict
    ) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        correlation_id = arguments.pop("correlation_id", "")
        logger.info("Calling tool: %s env=%s correlation_id=%s", name, get_env(), correlation_id)
        result = None
        match name:
                case InsurtekTools.FIND_SIMILAR_APPLICANTS.value:
                    validate_required_arguments(arguments, ["applicant_id"])
                    result = await core_data_server._run_in_executor(
                        core_data_server.graphrag.find_similar_applicants,
                        arguments["applicant_id"],
                        int(arguments.get("top_k", 5)),
                    )
                case InsurtekTools.QUERY_APPLICANT_GRAPH.value:
                    validate_required_arguments(arguments, ["query"])
                    result = await core_data_server._run_in_executor(
                        core_data_server.graphrag.query_applicant_graph, arguments["query"]
                    )
                case InsurtekTools.CREATE_APPLICANT.value:
                    if "applicant_json" not in arguments:
                        arguments = {"applicant_json": arguments}
                    validate_required_arguments(arguments, ["applicant_json"])
                    result = await core_data_server._run_in_executor(core_data_server.applicant.create_applicant, arguments)
                case InsurtekTools.GET_APPLICANT.value:
                    validate_required_arguments(arguments, ["applicant_id"])
                    result = await core_data_server._run_in_executor(core_data_server.applicant.get_applicant, arguments["applicant_id"])
                case InsurtekTools.SEARCH_APPLICANTS.value:
                    validate_required_arguments(arguments, ["policy_type"])
                    result = await core_data_server._run_in_executor(core_data_server.applicant.search_applicants_by_policy_type, arguments["policy_type"])
                case InsurtekTools.GET_ALL_APPLICANTS.value:
                    result = await core_data_server._run_in_executor(core_data_server.applicant.get_all_applicants, arguments.get("before_created_at"))
                case InsurtekTools.GET_HIGH_RISK_APPLICANTS.value:
                    result = await core_data_server._run_in_executor(core_data_server.applicant.get_high_risk_applicants)
                case InsurtekTools.UPDATE_APPLICANT.value:
                    validate_required_arguments(arguments, ["applicant_id"])
                    result = await core_data_server._run_in_executor(lambda: core_data_server.applicant.update_applicant(**arguments))
                case InsurtekTools.QUOTE_APPLICANT.value:
                    validate_required_arguments(arguments, ["applicant_id"])
                    result = await core_data_server._run_in_executor(core_data_server.applicant.quote_applicant, arguments["applicant_id"])
                case InsurtekTools.DELETE_APPLICANT.value:
                    validate_required_arguments(arguments, ["applicant_id"])
                    result = await core_data_server._run_in_executor(core_data_server.applicant.delete_applicant, arguments["applicant_id"])
                case InsurtekTools.CREATE_POLICY.value:
                    validate_required_arguments(arguments, ["applicant_id", "policy_type"])
                    result = await core_data_server._run_in_executor(lambda: core_data_server.policy.create_policy(**arguments))
                case InsurtekTools.GET_ACTIVE_POLICY.value:
                    validate_required_arguments(arguments, ["applicant_id"])
                    result = await core_data_server._run_in_executor(core_data_server.policy.get_active_policy, arguments["applicant_id"])
                case InsurtekTools.GET_POLICY_HISTORY.value:
                    validate_required_arguments(arguments, ["applicant_id"])
                    result = await core_data_server._run_in_executor(core_data_server.policy.get_policy_history, arguments["applicant_id"])
                case InsurtekTools.BIND_POLICY.value:
                    validate_required_arguments(arguments, ["applicant_id", "policy_id"])
                    result = await core_data_server._run_in_executor(core_data_server.policy.bind_policy, arguments["applicant_id"], arguments["policy_id"])
                case InsurtekTools.CANCEL_POLICY.value:
                    validate_required_arguments(arguments, ["applicant_id"])
                    result = await core_data_server._run_in_executor(core_data_server.policy.cancel_policy, arguments["applicant_id"])
                case _:
                    raise ValueError(f"Unknown tool: {name}")
        tool_result = format_tool_result(result)
        result_str = tool_result[0].text
        logger.info(f"Tool '{name}' completed successfully", extra_fields={"result_length": len(result_str)})
        return tool_result

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(name=t.value, description=get_tool_description(t.value), inputSchema=get_tool_schema(t.value))
            for t in InsurtekTools
        ]

    starlette_app = EnvMiddleware(
        StreamableStarletteApp(
            server=server,
            json_response=json_response,
            path="/mcp_core",
            health_response={"status": "ok", "service": "insuretek-core-data"},
            debug=False,
        ).create_app()
    )

    logger.info(f"Insuretek Core Data MCP server listening on http://{Config.SERVER_HOST}:{Config.MCP_CORE_PORT}")
    run_starlette_app(starlette_app, host=Config.SERVER_HOST, port=Config.MCP_CORE_PORT, access_log=False)
    return 0

if __name__ == "__main__":
    main(Config.MCP_CORE_PORT, Config.LOG_LEVEL, True)  # type: ignore[call-arg]
