"""Synchronization utilities between PostgreSQL and Neo4j."""

import functools
import threading
from typing import List, Dict, Any, Optional
from core_data.db.graph.neo4j_client import get_neo4j_client
from shared.common.log.logging_config import setup_structured_logging

logger = setup_structured_logging(__name__)


def _sync_op(default):
    """Guard + catch for sync ops. Returns `default` when Neo4j disconnected or on any exception."""
    def decorator(func):
        op = func.__name__.replace("_", " ")
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            if not self.neo4j_client.is_connected():
                logger.warning(f"Neo4j not connected, skipping {op}")
                return type(default)() if isinstance(default, (list, dict)) else default
            try:
                return func(self, *args, **kwargs)
            except Exception as exc:
                logger.error(f"Failed to {op}: {exc}")
                return type(default)() if isinstance(default, (list, dict)) else default
        return wrapper
    return decorator


class GraphSync:
    """Synchronize data between PostgreSQL and Neo4j."""

    def __init__(self):
        self.neo4j_client = get_neo4j_client()

    @_sync_op(False)
    def sync_applicant(self, applicant_id: str) -> bool:
        from core_data.repositories.applicant_repository import ApplicantRepository
        applicant = ApplicantRepository().get_by_id(applicant_id)
        if not applicant:
            logger.warning("sync_applicant: applicant not found applicant_id=%s", applicant_id)
            return False
        loc = getattr(applicant, "location_address", None) or {}
        if hasattr(loc, "__dict__"):
            loc = vars(loc)
        contact = getattr(applicant, "contact_info", None) or {}
        if hasattr(contact, "__dict__"):
            contact = vars(contact)
        name = getattr(contact, "full_name", None) or loc.get("city") or applicant_id
        policy_type = getattr(applicant, "policy_type", None)
        policy_type_val = policy_type.value if hasattr(policy_type, "value") else (policy_type or "")
        risk_tier = getattr(applicant, "risk_tier", None)
        risk_tier_val = risk_tier.value if hasattr(risk_tier, "value") else (risk_tier or "STANDARD")
        location = loc.get("city") or loc.get("state") or ""
        self.neo4j_client.upsert_applicant_properties(
            applicant_id=applicant_id,
            name=name,
            policy_type=policy_type_val,
            location=location,
            risk_tier=risk_tier_val,
            contact_email=contact.get("email") if isinstance(contact, dict) else getattr(contact, "email", None),
            contact_phone=contact.get("phone") if isinstance(contact, dict) else getattr(contact, "phone", None),
            enterprise_identifier=getattr(applicant, "enterprise_identifier", None),
            state_or_province_code=loc.get("state") if isinstance(loc, dict) else getattr(loc, "state", None),
            country_code=loc.get("country_code") if isinstance(loc, dict) else getattr(loc, "country_code", None),
            postal_code=loc.get("zip_code") if isinstance(loc, dict) else getattr(loc, "zip_code", None),
        )
        logger.info("sync_applicant: upserted Neo4j node applicant_id=%s", applicant_id)
        return True


    @staticmethod
    def _compute_risk_band(m: dict) -> str:
        score = m.get("risk_score")
        if score is None:
            return "unknown"
        try:
            s = float(score)
        except (ValueError, TypeError):
            return "unknown"
        if s >= 0.75:
            return "high"
        if s >= 0.40:
            return "medium"
        return "low"


_instance: Optional[GraphSync] = None
_lock = threading.Lock()


def get_graph_sync() -> GraphSync:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = GraphSync()
    return _instance
