"""Repository for persisting and querying policy audit records."""

from typing import List

from mltek.common.log.logging_config import setup_structured_logging
from core_data.db.factory import get_policy_audit_table_operations
from core_data.db.models.models_policy_audit import PolicyAuditRecord

logger = setup_structured_logging(__name__)


def _sanitize(value):
    """Recursively strip empty-string values from dicts/lists before writing to the DB."""
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items() if not (isinstance(v, str) and v == "")}
    if isinstance(value, list):
        return [_sanitize(v) for v in value if not (isinstance(v, str) and v == "")]
    return value


class PolicyAuditRepository:
    """Append-only repository for policy decision audit records.

    All public methods swallow exceptions — audit persistence must never
    fail a live request.

    Read methods use query_by_key() which is implemented on both DynamoDB
    and PostgreSQL backends (no boto3 Key dependency in this layer).

    Sort contract: all get_* methods return records most-recent-first
    EXCEPT get_by_entity_id which returns oldest-first (timeline order).
    The sort+slice pattern is always: sort → slice → deserialise, never
    the reverse, to guarantee the limit applies to the correct end of the range.
    """

    def __init__(self):
        self.table = get_policy_audit_table_operations()

    def record(self, record: PolicyAuditRecord) -> None:
        """Write one policy audit record. Swallows all errors."""
        try:
            item = record.to_backend_item()
            self.table.put_item(_sanitize(item))
            logger.info(
                "policy_audit record exit audit_id=%s policy_type=%s allowed=%s",
                record.audit_id, record.policy_type, record.allowed,
            )
        except Exception as exc:
            logger.warning("Failed to record policy audit: %s", exc)

    def get_by_session(self, session_id: str, limit: int = 100) -> List[PolicyAuditRecord]:
        """Return audit records for a session, most recent first."""
        try:
            rows = self.table.query_by_key("session_id", session_id, index_name="session_id_index")
            rows.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
            result = [PolicyAuditRecord.from_backend_item(r) for r in rows[:limit]]
            logger.info("policy_audit get_by_session exit session_id=%s count=%d", session_id, len(result))
            return result
        except Exception as exc:
            logger.warning("Failed to query policy audit by session: %s", exc)
            return []

    def get_by_correlation(self, correlation_id: str, limit: int = 100) -> List[PolicyAuditRecord]:
        """Return audit records for a correlation ID, most recent first."""
        try:
            rows = self.table.query_by_key("correlation_id", correlation_id, index_name="correlation_id_index")
            rows.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
            result = [PolicyAuditRecord.from_backend_item(r) for r in rows[:limit]]
            logger.info("policy_audit get_by_correlation exit correlation_id=%s count=%d", correlation_id, len(result))
            return result
        except Exception as exc:
            logger.warning("Failed to query policy audit by correlation_id: %s", exc)
            return []

    def get_by_policy_type(self, policy_type: str, limit: int = 100, since_ms: int | None = None) -> List[PolicyAuditRecord]:
        """Return audit records for a policy type, most recent first.

        since_ms — if provided, only records with created_at >= since_ms are returned;
                   sort and limit are applied after filtering.

        Note: filtering is done in Python after a full index scan. For high-volume
        policy types (e.g. quality_metric over 24 h) push since_ms to a DB range
        condition on the sort key when the backend supports it.
        """
        try:
            rows = self.table.query_by_key("policy_type", policy_type, index_name="policy_type_index")
            rows.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
            if since_ms is not None:
                rows = [r for r in rows if (r.get("created_at") or 0) >= since_ms]
            result = [PolicyAuditRecord.from_backend_item(r) for r in rows[:limit]]
            logger.info("policy_audit get_by_policy_type exit policy_type=%s count=%d", policy_type, len(result))
            return result
        except Exception as exc:
            logger.warning("Failed to query policy audit by policy_type: %s", exc)
            return []

    def get_by_request_id(self, request_id: str, limit: int = 100) -> List[PolicyAuditRecord]:
        """Return audit records for a workflow request_id, most recent first."""
        try:
            rows = self.table.query_by_key("request_id", request_id, index_name="request_id_index")
            rows.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
            result = [PolicyAuditRecord.from_backend_item(r) for r in rows[:limit]]
            logger.info("policy_audit get_by_request_id exit request_id=%s count=%d", request_id, len(result))
            return result
        except Exception as exc:
            logger.warning("Failed to query policy audit by request_id: %s", exc)
            return []

    def get_by_entity_id(self, entity_id: str, limit: int = 100) -> List[PolicyAuditRecord]:
        """Return audit records for an entity, oldest first (timeline order).

        Sort → slice order is intentional: oldest N records are selected first
        so the timeline is anchored at the start of the entity's history.
        """
        try:
            rows = self.table.query_by_key("entity_id", entity_id, index_name="entity_id_index")
            rows.sort(key=lambda r: r.get("created_at") or 0)          # ascending — oldest first
            result = [PolicyAuditRecord.from_backend_item(r) for r in rows[:limit]]
            logger.info("policy_audit get_by_entity_id exit entity_id=%s count=%d", entity_id, len(result))
            return result
        except Exception as exc:
            logger.warning("Failed to query policy audit by entity_id: %s", exc)
            return []
