"""Repository for checking portal access by email ID."""

from shared.common.log.logging_config import setup_structured_logging
from core_data.db.factory import get_portal_access_table_operations

logger = setup_structured_logging(__name__)


class PortalAccessRepository:
    def __init__(self):
        self.table = get_portal_access_table_operations()

    def is_allowed(self, email: str) -> bool:
        """Return True if the email exists in the portal_access table."""
        try:
            item = self.table.get_item({"email_id": email.lower().strip()})
            allowed = item is not None
            logger.info("portal_access check email=%s allowed=%s", email, allowed)
            return allowed
        except Exception as exc:
            logger.warning("portal_access lookup failed email=%s error=%s", email, exc)
            return False
