"""Base classes and utilities for all database models.

WARNING: This is an INTERNAL implementation detail.
DO NOT import or use DatabaseModel directly from external code.
DO NOT extend DatabaseModel in service or domain layers.

This module is only for database model implementations (models_applicant.py, etc.).

Provided:
- DatabaseModel: Base class for Pydantic models (INTERNAL USE ONLY)
- current_timestamp_ms(): Utility for UTC timestamps (INTERNAL USE ONLY)
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Dict, Any
from datetime import datetime, timezone
from decimal import Decimal
import json


def current_timestamp_ms() -> int:
    """INTERNAL: Do not use in service or domain layers."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class DatabaseModel(BaseModel):
    """Base model for all database models with standard configuration and methods.
    
    WARNING: This is for INTERNAL database model use only.
    Service layers should work with specific models (Applicant,  etc.),
    not with DatabaseModel directly.
    
    Provides:
    - Standard Pydantic configuration (validate_assignment=True)
    - Unified to_backend_item() and from_backend_item() interface
    - Automatic enum serialization via @field_serializer(when_used="json")
    - Timestamp validation (must be non-negative milliseconds since epoch; 0 accepted)
    """
    model_config = ConfigDict(validate_assignment=True)
    
    @field_validator('created_at', 'updated_at', 'activated_at', 'added_at', mode='before', check_fields=False)
    @classmethod
    def handle_timestamp_types(cls, v, info):
        # DynamoDB returns Decimal for numbers; PostgreSQL returns int
        if v is None:
            return v
        if isinstance(v, Decimal):
            v = int(v)
        elif isinstance(v, str) and v.isdigit():
            v = int(v)
        # 0 accepted as "clock unavailable" in tests
        if not isinstance(v, int) or v < 0:
            raise ValueError(f"Timestamp must be a non-negative integer (milliseconds since epoch), got {v!r}")
        return v
    
    @field_validator('metadata', 'location_address', 'contact_info', mode='before', check_fields=False)
    @classmethod
    def parse_json_dicts(cls, v, info):
        # DynamoDB stores JSON as strings; PostgreSQL returns proper dicts
        if v is None:
            return v
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, ValueError):
                return {}
        if isinstance(v, dict):
            return v
        # Pass Pydantic models through so Pydantic validates normally
        if isinstance(v, BaseModel):
            return v
        return {}
    
    def to_backend_item(self) -> Dict[str, Any]:
        # Enums serialized via @field_serializer decorators on subclasses
        return self.model_dump(mode="json", exclude_none=True)

    @classmethod
    def from_backend_item(cls, item: Dict[str, Any]):
        return cls.model_validate(item)

    def to_response(self) -> Dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)
