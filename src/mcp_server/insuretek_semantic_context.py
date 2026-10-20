import asyncio
import threading
from typing import Sequence

from mcp.server.lowlevel import Server
from mcp.types import Tool, TextContent, ImageContent, EmbeddedResource
from mltek.common.starlette_app import StreamableStarletteApp, run_starlette_app

# Expose src/ so that `dbtek.*` package imports resolve
from core_data.service.graph_service import GraphService
from core_data.service.applicant_service import ApplicantService
from core_data.service.policy_audit_service import ForensicAuditService
from mltek.config import config
from mltek.common.infra.context import set_app_name
from mltek.common.router.resolver import register_domain_provider
from mltek.common.log import setup_structured_logging
from insuretek.domain_provider import InsureTekDomainProvider

set_app_name("insuretek")
register_domain_provider(InsureTekDomainProvider())
from mltek.common.infra.error_helpers import handle_mcp_tool_errors
from mltek.common.tools.format import format_text_result, format_tool_result, format_json_as_text
from mcp_server.utilities.transform import unwrap_object_argument
from mcp_server.utilities.constants import MSAgentTools
from mcp_server.utilities.tools_schemas import get_tool_description, get_tool_schema

logger = setup_structured_logging(__name__)


class MsSemanticContextServer:

    def __init__(self):
        self._graph_service: GraphService | None = None
        self._graph_init_lock = threading.Lock()
        self._applicant_service = ApplicantService()
        self._forensic_service = ForensicAuditService()

    def _get_graph_service(self) -> GraphService:
        with self._graph_init_lock:
            if self._graph_service is None:
                self._graph_service = GraphService()
                logger.info("Deferred KG sync until first graph query or explicit sync request.")
        return self._graph_service

    def kg_similar_applicants(
        self,
        applicant_id: str = "",
        query_text: str = "",
        limit: int = 5,
        alpha: float = 0.5,
    ) -> str:
        """Unified similarity search returning ranked applicants with scores and business attributes.

        - query_text only   → semantic vector search
        - applicant_id only → hybrid (embedding) or graph fallback
        - both              → hybrid vector + graph scoring
        """
        if query_text:
            from core_data.service.applicant_link_service import ApplicantLinkService
            _loop = asyncio.new_event_loop()
            try:
                results = _loop.run_until_complete(ApplicantLinkService().hybrid_similar(
                    query_text=query_text,
                    limit=limit,
                    alpha=alpha,
                    applicant_id=applicant_id or None,
                ))
            finally:
                _loop.close()
            mode = "hybrid" if applicant_id else "vector"
        else:
            from core_data.db.graph.neo4j_client import get_neo4j_client
            neo4j = get_neo4j_client()
            stored_embedding = neo4j.get_applicant_embedding(applicant_id) if applicant_id else None
            if stored_embedding:
                results = neo4j.vector_search_applicants(stored_embedding, limit=limit, applicant_id=applicant_id, alpha=alpha)
                mode = "hybrid"
            else:
                results = neo4j.find_similar_applicants(applicant_id, limit)
                mode = "graph"

        if not results:
            return "No similar applicants found. Try a different applicant ID or search description."

        header = f"applicant_id: {applicant_id or '—'}  query: {query_text or '—'}  mode: {mode}"
        lines = [header, ""]
        for i, r in enumerate(results, 1):
            aid = r.get("applicant_id", "")
            pct = round((r.get("combined_score") or r.get("score") or 0.0) * 100)
            g   = r.get("graph_score", 0.0)
            v   = r.get("vector_score", 0.0)
            name     = r.get("name", "") or ""
            industry = r.get("industry", "") or ""
            location = r.get("location", "") or ""
            risk     = r.get("risk_band", "") or ""
            mcc      = r.get("mcc", "") or ""
            attrs = ", ".join(x for x in [name, industry, location, f"risk:{risk}" if risk else "", f"mcc:{mcc}" if mcc else ""] if x)
            lines.append(f"{i}. {aid}  similarity={pct}%  [graph={g:.2f} vector={v:.2f}]  ({attrs})")

        logger.info("kg_similar_applicants exit mode=%s results=%d", mode, len(results))
        return "\n".join(lines)

    def kg_detect_risk_cluster(self, applicant_id: str) -> str:
        result = self._get_graph_service().kg_detect_risk_cluster(applicant_id)
        return f"applicant_id: {applicant_id}\n" + result

    def kg_explain_applicant(self, applicant_id: str) -> str:
        result = self._get_graph_service().kg_explain_applicant(applicant_id)
        return f"applicant_id: {applicant_id}\n" + result


    def isar_explain_applicant(self, applicant_id: str) -> str:
        return self._applicant_service.isar_explain_applicant(applicant_id)






def main(
    port: int,
    log_level: str,
    json_response: bool,
) -> int:

    SemanticContextServer = Server("semantic_context_server")
    ms_sc_server = MsSemanticContextServer()


    @SemanticContextServer.call_tool()
    @handle_mcp_tool_errors
    async def call_tool(
        name: str, arguments: dict
    ) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
        """Handle tool calls for MS queries."""
        arguments = unwrap_object_argument(arguments)
        correlation_id = arguments.get("correlation_id", "")
        logger.info("semantic_context tool call started tool=%s correlation_id=%s arguments=%s", name, correlation_id, arguments)
        result = None

        # Run synchronous DB/KG calls in a thread pool so the event loop stays free.
        match name:
            case MSAgentTools.KG_SIMILAR_APPLICANTS.value:
                result = await asyncio.to_thread(
                    ms_sc_server.kg_similar_applicants,
                    arguments.get("applicant_id", ""),
                    arguments.get("query_text", ""),
                    int(arguments.get("limit", 5)),
                    float(arguments.get("alpha", 0.5)),
                )
            case MSAgentTools.KG_DETECT_RISK.value:
                result = await asyncio.to_thread(
                    ms_sc_server.kg_detect_risk_cluster,
                    arguments.get("applicant_id", ""),
                )
            case MSAgentTools.KG_EXPLAIN_APPLICANT.value:
                result = await asyncio.to_thread(
                    ms_sc_server.kg_explain_applicant,
                    arguments.get("applicant_id", ""),
                )
            case MSAgentTools.ISAR_EXPLAIN_APPLICANT.value:
                result = await asyncio.to_thread(
                    ms_sc_server.isar_explain_applicant,
                    arguments.get("applicant_id", ""),
                )
            case MSAgentTools.GET_ISAR_EVIDENCE.value:
                result = await asyncio.to_thread(
                    ms_sc_server._forensic_service.get_isar_evidence,
                    arguments.get("applicant_id") or "",
                    arguments.get("correlation_id") or "",
                    int(arguments.get("limit", 2)),
                )
            case _:
                raise ValueError(f"Unknown tool: {name}")

        if isinstance(result, dict):
            response = format_tool_result(result)
            logger.info("semantic_context tool call completed tool=%s structured dict result returned", name)
            return response
        response = format_text_result(result)
        logger.info("semantic_context tool call completed tool=%s result_length=%d", name, len(response[0].text))
        return response


    @SemanticContextServer.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(name=t.value, description=get_tool_description(t.value), inputSchema=get_tool_schema(t.value))
            for t in MSAgentTools
        ]



    starlette_app = StreamableStarletteApp(
        server=SemanticContextServer,
        json_response=json_response,
        path="/mcp_semantic",
        health_response={"status": "ok", "service": "insuretek-semantic-context"},
        debug=False,
    ).create_app()

    logger.info("Semantic Context MCP server listening on http://%s:%s/mcp_semantic", config.SERVER_HOST, port)
    run_starlette_app(starlette_app, host=config.SERVER_HOST, port=port, access_log=False)

    return 0

if __name__ == "__main__":
    main(config.MCP_SEMANTIC_PORT, config.LOG_LEVEL, True)  # type: ignore[call-arg]