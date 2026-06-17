"""VectorEmbeddingRepository — delegates to ApplicantEmbeddingsGraph (Neo4j)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mltek.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)


class VectorEmbeddingRepository:

    @staticmethod
    def _graph():
        from core_data.db.vector.factory import get_applicant_embeddings_graph
        return get_applicant_embeddings_graph()

    def upsert(self, applicant_id: str, text: str, embedding: List[float]) -> None:
        self._graph().upsert(applicant_id, text, embedding)
        logger.info("VectorEmbeddingRepository upsert exit applicant_id=%s", applicant_id)

    def cosine_search(
        self,
        query_embedding: List[float],
        limit: int,
        applicant_id: Optional[str] = None,
        alpha: float = 0.5,
    ) -> List[Dict[str, Any]]:
        results = self._graph().cosine_search(query_embedding, limit, applicant_id=applicant_id, alpha=alpha)
        logger.info("VectorEmbeddingRepository cosine_search exit results=%d", len(results))
        return results
