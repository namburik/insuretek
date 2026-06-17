"""Semantic router — embedding-based agent selection.

Embeds agent descriptions from resolver_config.yaml at startup.
Routes each query via cosine similarity — no LLM call at routing time.

Supports Ollama (nomic-embed-text) and Google (text-embedding-004)
based on the MODEL_PROVIDER setting, matching the existing LLM bridge.
"""

import math
from typing import Dict, List, Optional

from mltek.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)


# ── Embedding ─────────────────────────────────────────────────────────────────

async def _embed_ollama(texts: List[str], model: str, base_url: str) -> List[List[float]]:
    from langchain_ollama import OllamaEmbeddings
    embedder = OllamaEmbeddings(model=model, base_url=base_url)
    return await embedder.aembed_documents(texts)


async def _embed_google(texts: List[str], model: str) -> List[List[float]]:
    from mltek.common.agents.bridge import GoogleGenerativeAIEmbeddings
    embedder = GoogleGenerativeAIEmbeddings(model=model)
    return await embedder.aembed_documents(texts)


async def _embed_bedrock(texts: List[str], model: str, region: str) -> List[List[float]]:
    from langchain_aws import BedrockEmbeddings
    embedder = BedrockEmbeddings(model_id=model, region_name=region)
    return await embedder.aembed_documents(texts)


async def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed a list of texts using the configured provider."""
    from mltek.config import config

    if config.is_using_google():
        return await _embed_google(texts, config.GOOGLE_EMBEDDING_MODEL)

    if config.is_using_bedrock():
        return await _embed_bedrock(texts, config.BEDROCK_EMBEDDING_MODEL, config.AWS_REGION)

    return await _embed_ollama(texts, config.EMBEDDING_MODEL, config.OLLAMA_BASE_URL)


# ── Cosine similarity ─────────────────────────────────────────────────────────

def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    return dot / mag if mag else 0.0


# ── SemanticRouter ────────────────────────────────────────────────────────────

class SemanticRouter:
    """Cosine-similarity router over embedded agent descriptions.

    Usage:
        router = await SemanticRouter.build(nodes_cfg)
        agent_id = await router.route(query)
    """

    def __init__(self, agent_vecs: Dict[str, List[float]]):
        self._vecs = agent_vecs

    @classmethod
    async def build(cls, nodes_cfg: List[dict]) -> "SemanticRouter":
        """Embed all agent descriptions and return a ready router."""
        ids = [n["id"] for n in nodes_cfg]
        descriptions = [n.get("description", n["id"]) for n in nodes_cfg]

        logger.info("SemanticRouter: embedding %d agent descriptions", len(ids))
        vecs = await embed_texts(descriptions)
        agent_vecs = dict(zip(ids, vecs))
        logger.info("SemanticRouter: ready — agents=%s", list(agent_vecs))
        return cls(agent_vecs)

    async def route(
        self, query: str, default: str = "", threshold: float = 0.0
    ) -> tuple[Optional[str], float]:
        """Return (agent_id, confidence) where confidence is the best cosine score.

        If threshold > 0 and the best score falls below it, returns (default, score)
        so the caller can fall through to LLM intent classification.
        Confidence is 0.0 on error or when no route is found.
        """
        try:
            query_vecs = await embed_texts([query])
            q = query_vecs[0]
            scores = {aid: _cosine(q, vec) for aid, vec in self._vecs.items()}
            best = max(scores, key=scores.get)
            best_score = scores[best]
            logger.info(
                "SemanticRouter: routed query to '%s' (score=%.3f) scores=%s",
                best,
                best_score,
                {k: round(v, 3) for k, v in scores.items()},
            )
            if threshold > 0 and best_score < threshold:
                logger.info(
                    "SemanticRouter: score %.3f below threshold %.3f — falling back", best_score, threshold
                )
                return default or None, best_score
            return best, best_score
        except Exception as exc:
            logger.warning("SemanticRouter.route failed: %s", exc)
            return default or None, 0.0
