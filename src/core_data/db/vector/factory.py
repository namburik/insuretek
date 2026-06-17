"""Vector operations on Applicant nodes in Neo4j — no pgvector/Postgres dependency."""

from __future__ import annotations

from typing import Any

from mltek.common.log import setup_structured_logging
from core_data.db.graph.neo4j_client import get_neo4j_client

logger = setup_structured_logging(__name__)


class ApplicantEmbeddingsGraph:
    """Neo4j-backed applicant embedding store.

    Embeddings are stored as a float-array property on :Applicant nodes and
    queried via the `applicant-embedding` HNSW vector index (created on connect).
    """

    def __init__(self):
        self.client = get_neo4j_client()

    def upsert(self, applicant_id: str, text: str, embedding: list[float]) -> None:
        """Upsert an applicant embedding into the graph."""
        self.client.upsert_applicant_embedding(applicant_id, text, embedding)
        logger.info("ApplicantEmbeddingsGraph upsert exit applicant_id=%s dim=%d", applicant_id, len(embedding))

    def cosine_search(
        self,
        query_embedding: list[float],
        limit: int,
        applicant_id: str | None = None,
        alpha: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Search applicants by embedding similarity."""
        results = self.client.vector_search_applicants(
            query_embedding, limit=limit, applicant_id=applicant_id, alpha=alpha,
        )
        logger.info("ApplicantEmbeddingsGraph cosine_search exit limit=%d results=%d", limit, len(results))
        return results


def get_applicant_embeddings_graph() -> ApplicantEmbeddingsGraph:
    """Factory function for creating an applicant embeddings graph instance."""
    return ApplicantEmbeddingsGraph()
