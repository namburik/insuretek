"""Base classes and utilities for all database models."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict
import json

from pydantic import BaseModel, ConfigDict, field_validator


def current_timestamp_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class DatabaseModel(BaseModel):
    """Base Pydantic model for DynamoDB/PostgreSQL-backed records.

    Handles timestamp coercion (Decimal from DynamoDB, int from Postgres)
    and JSON string → dict coercion for metadata/nested fields.
    """
    model_config = ConfigDict(validate_assignment=True)

    @field_validator("created_at", "updated_at", "activated_at", "added_at",
                     mode="before", check_fields=False)
    @classmethod
    def _coerce_timestamp(cls, v):
        if v is None:
            return v
        if isinstance(v, Decimal):
            v = int(v)
        elif isinstance(v, str) and v.isdigit():
            v = int(v)
        if not isinstance(v, int) or v < 0:
            raise ValueError(f"Timestamp must be a non-negative integer (ms since epoch), got {v!r}")
        return v

    @field_validator("metadata", "location_address", "contact_info",
                     mode="before", check_fields=False)
    @classmethod
    def _coerce_json_dict(cls, v):
        if v is None:
            return v
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, ValueError):
                return {}
        if isinstance(v, (dict, BaseModel)):
            return v
        return {}

    def to_backend_item(self) -> Dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

    @classmethod
    def from_backend_item(cls, item: Dict[str, Any]):
        return cls.model_validate(item)

    def to_response(self) -> Dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)
