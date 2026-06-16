"""DynamoDB read-path helpers: JSON encoding and value normalisation."""

import json
from typing import Any

from shared.common.tools.format import _serialize


class DDBJsonEncoder(json.JSONEncoder):
    """JSON encoder for DynamoDB-sourced payload values."""

    def default(self, obj: Any) -> Any:
        return _serialize(obj)


def normalize_dynamodb_value(value: Any) -> Any:
    """Recursively convert DynamoDB Decimal values to native Python types."""
    from decimal import Decimal
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: normalize_dynamodb_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_dynamodb_value(v) for v in value]
    return value


def sanitize_dynamodb_map_keys(value: Any) -> Any:
    """Remove empty-string keys from DynamoDB maps before PutItem."""
    if isinstance(value, dict):
        return {
            k: sanitize_dynamodb_map_keys(v)
            for k, v in value.items()
            if isinstance(k, str) and k != ""
        }
    if isinstance(value, list):
        return [sanitize_dynamodb_map_keys(v) for v in value]
    return value
