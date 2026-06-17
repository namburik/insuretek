"""GraphRAG tool utilities — applicant knowledge graph queries."""

from core_data.service.graph_service import GraphService
from core_data.db.graph.neo4j_client import get_neo4j_client
from mltek.common.log.logging_config import setup_structured_logging
from mltek.common.infra.error_helpers import handle_tool_errors as _handle_tool_errors

logger = setup_structured_logging(__name__)


class GraphRAGHandlers:
    def __init__(self, knowledge_graph: GraphService, applicant_service=None):
        self.kg = knowledge_graph
        self.applicant_service = applicant_service
        if applicant_service is not None:
            self.kg.applicant_service = applicant_service
            count = self.kg.sync_from_service(applicant_service)
            logger.info(f"GraphRAG knowledge graph loaded {count} applicants from DB")

    @_handle_tool_errors()
    def find_similar_applicants(self, applicant_id: str, top_k: int = 5) -> dict:
        """Find applicants similar to the given applicant by policy type and location.

        Uses Neo4j when available, falls back to the in-memory graph.
        """
        if not applicant_id or not isinstance(applicant_id, str):
            raise ValueError("applicant_id must be a non-empty string")
        top_k = max(1, min(int(top_k), 20))

        neo4j = get_neo4j_client()
        if neo4j.is_connected():
            results = neo4j.find_similar_applicants(applicant_id, top_k)
            similar = [
                {
                    "applicant_id": r["applicant_id"],
                    "name": r.get("name"),
                    "policy_type": r.get("policy_type"),
                    "location": r.get("location"),
                    "risk_tier": r.get("risk_tier"),
                    "match_score": round(r["score"], 3),
                }
                for r in results
            ]
            logger.info(f"Found {len(similar)} similar applicants for {applicant_id} (Neo4j)")
        else:
            mem_results = self.kg.find_similar_applicants(applicant_id, limit=top_k)
            similar = [
                {**node.to_dict(), "match_score": round(score, 3)}
                for node, score in (mem_results or [])
            ]
            logger.info(f"Found {len(similar)} similar applicants for {applicant_id} (in-memory)")

        return self._build_similar_applicants_payload(applicant_id, similar)

    @_handle_tool_errors()
    def query_applicant_graph(self, query: str) -> dict:
        """Answer a natural language question about the applicant knowledge graph."""
        if not query or not isinstance(query, str):
            raise ValueError("query must be a non-empty string")
        logger.info(f"Querying applicant graph: {query[:100]}")
        results = self.kg.query_applicant_graph_raw(query, applicant_service=self.applicant_service)
        logger.info("query_applicant_graph raw response query=%s result_count=%d", query, len(results))
        return self._build_query_applicant_graph_payload(query, results)

    def _build_similar_applicants_payload(self, applicant_id: str, similar: list[dict]) -> dict:
        return {
            "applicant_id": applicant_id,
            "similar_count": len(similar),
            "similar_applicants": similar,
        }

    def _build_query_applicant_graph_payload(self, query: str, results: list[dict]) -> dict:
        if not results:
            return {
                "query": query,
                "matches": [],
                "answer": "No applicants matched the query.",
            }

        formatted = [
            {
                "applicant_id": row.get("applicant_id") or row.get("applicant_id"),
                "name": row.get("name") or row.get("account_name") or row.get("applicant_name"),
                "industry": row.get("industry") or row.get("applicant_industry") or row.get("applicant_type"),
                "location": row.get("location") or row.get("city") or row.get("state"),
                "risk_band": row.get("risk_band"),
            }
            for row in results
        ]

        return {
            "query": query,
            "matches": formatted,
            "match_count": len(formatted),
            "answer": f"Found {len(formatted)} matching applicants.",
        }

