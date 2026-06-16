"""Repository for recording and querying OAuth login activity."""

from datetime import datetime, timezone
from typing import List, Optional

from shared.common.log.logging_config import setup_structured_logging
from core_data.db.factory import get_login_activity_table_operations

logger = setup_structured_logging(__name__)


class LoginActivityRepository:
    def __init__(self):
        self.table = get_login_activity_table_operations()

    def record(
        self,
        provider: str,
        identity: str,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        """Write one login event.

        Args:
            provider:   "github" or "linkedin"
            identity:   GitHub login or LinkedIn email
            ip:         Client IP address
            user_agent: Browser user-agent string
        """
        user_id = f"{provider}:{identity}"
        login_at = int(datetime.now(timezone.utc).timestamp() * 1000)
        item = {
            "user_id": user_id,
            "login_at": login_at,
            "provider": provider,
            "identity": identity,
        }
        if ip:
            item["ip"] = ip
        if user_agent:
            item["user_agent"] = user_agent
        try:
            self.table.put_item(item)
            logger.info("Login recorded: provider=%s identity=%s ip=%s", provider, identity, ip)
        except Exception as exc:
            logger.warning("Failed to record login activity: %s", exc)

    def get_by_user(self, provider: str, identity: str, limit: int = 50) -> List[dict]:
        from boto3.dynamodb.conditions import Key
        user_id = f"{provider}:{identity}"
        return self.table.query(
            Key("user_id").eq(user_id),
            limit=limit,
            scan_index_forward=False,
        )

    def get_by_provider(self, provider: str, limit: int = 100) -> List[dict]:
        from boto3.dynamodb.conditions import Key
        return self.table.query(
            Key("provider").eq(provider),
            index_name="provider_index",
            limit=limit,
            scan_index_forward=False,
        )
