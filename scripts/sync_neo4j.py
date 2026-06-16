"""Sync all applicants from DynamoDB/Postgres to Neo4j.

Pass 1 — node properties + :SIMILAR_TO edges
  GraphService.sync_from_service():
    - MERGE :Applicant nodes (name, policy_type, risk_tier, location, contact, enterprise_identifier)
    - MERGE :SIMILAR_TO edges (policy_type / risk_tier / location matches)

Pass 2 — applicant embeddings
  Generates text representation per applicant, embeds via configured provider
  (Ollama / Google / Bedrock), and stores as float-array property on :Applicant
  for the applicant-embedding HNSW vector index.

Usage:
    python3 scripts/sync_neo4j.py [--concurrency N] [--skip-embeddings]
"""

import sys
import asyncio
import argparse

sys.path.insert(0, "src")

from core_data.service.graph_service import GraphService
from core_data.service.applicant_service import ApplicantService
from core_data.db.vector.factory import get_applicant_embeddings_graph
from shared.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)


def _applicant_text(a: dict) -> str:
    """Build a short text snippet for embedding an applicant record."""
    parts = [
        a.get("applicant_name") or "",
        a.get("policy_type") or "",
        a.get("risk_tier") or "",
    ]
    loc = a.get("location_address") or {}
    if isinstance(loc, dict):
        parts.append(loc.get("city") or loc.get("state") or "")
    coverage = a.get("coverage_request") or {}
    if isinstance(coverage, dict):
        parts.append(coverage.get("coverage_type") or "")
    return " ".join(p for p in parts if p).strip()


async def _sync_embeddings(applicants: list, concurrency: int) -> int:
    from shared.common.router.semantic_router import embed_texts

    graph = get_applicant_embeddings_graph()
    sem = asyncio.Semaphore(concurrency)

    async def _one(record: dict) -> bool:
        applicant = record.get("applicant") or record
        aid = applicant.get("applicant_id")
        text = _applicant_text(applicant)
        if not aid or not text:
            return False
        async with sem:
            try:
                embeddings = await embed_texts([text])
                graph.upsert(aid, text, embeddings[0])
                return True
            except Exception as e:
                logger.warning("embedding failed applicant_id=%s: %s", aid, e)
                return False

    results = await asyncio.gather(*[_one(a) for a in applicants], return_exceptions=True)
    return sum(1 for r in results if r is True)


def run(concurrency: int = 5, skip_embeddings: bool = False) -> None:
    applicant_svc = ApplicantService()
    graph_svc = GraphService()

    print("Pass 1: syncing applicant nodes and :SIMILAR_TO edges to Neo4j...")
    try:
        count = graph_svc.sync_from_service(applicant_svc)
        print(f"  applicants synced: {count}")
    except Exception as e:
        print(f"  FAILED: {e}")
        sys.exit(1)

    if skip_embeddings:
        print("Embeddings skipped (--skip-embeddings).")
        print("Sync complete.")
        return

    print("Pass 2: generating and upserting applicant embeddings...")
    try:
        all_applicants = applicant_svc.get_all_applicants(limit=10000)
        ok = asyncio.run(_sync_embeddings(all_applicants, concurrency=concurrency))
        print(f"  embeddings upserted: {ok}/{len(all_applicants)}")
    except Exception as e:
        print(f"  FAILED: {e}")
        sys.exit(1)

    print("Sync complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bulk-sync applicants to Neo4j")
    parser.add_argument("--concurrency", type=int, default=5, help="Max concurrent embed calls (default: 5)")
    parser.add_argument("--skip-embeddings", action="store_true", help="Skip Pass 2 (node+edge sync only)")
    args = parser.parse_args()
    run(concurrency=args.concurrency, skip_embeddings=args.skip_embeddings)
