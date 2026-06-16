"""DynamoDB table operation abstractions."""

from decimal import Decimal
from typing import Dict, List, Optional, Any
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr
import boto3
from shared.config.config_common import Config



from shared.common.log.logging_config import setup_structured_logging

logger = setup_structured_logging(__name__)


def _filter_active(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter items to return only active ones."""
    return [item for item in items if item.get("is_active", True)]


def get_dynamodb_resource():
    """Factory function to create a DynamoDB resource with configured credentials.
    
    Returns:
        boto3 DynamoDB resource
    """
    logger.debug("Using DynamoDB resource endpoint %s for region %s", Config.DYNAMODB_ENDPOINT, Config.AWS_REGION)
    kwargs = {"region_name": Config.AWS_REGION}
    if Config.AWS_ACCESS_KEY_ID and Config.AWS_SECRET_ACCESS_KEY:
        kwargs["aws_access_key_id"] = Config.AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = Config.AWS_SECRET_ACCESS_KEY
    if Config.DYNAMODB_ENDPOINT:
        kwargs["endpoint_url"] = Config.DYNAMODB_ENDPOINT

    return boto3.resource("dynamodb", **kwargs)


def _normalize_dynamodb_value(value: Any) -> Any:
    """Recursively convert unsupported Python types for DynamoDB writes, dropping None values."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _normalize_dynamodb_value(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        normalized = [_normalize_dynamodb_value(v) for v in value if v is not None]
        return tuple(normalized) if isinstance(value, tuple) else normalized
    return value


class DynamoDBTable:
    """Abstract base class for DynamoDB table operations."""

    def __init__(self, table):
        """Initialize with a DynamoDB table resource.
        
        Args:
            table: boto3 DynamoDB table resource
        """
        self.table = table
        self.table_name = table.name

    def _refresh_table_metadata(self) -> None:
        """Refresh table metadata from DynamoDB to avoid stale schema validation."""
        try:
            self.table.load()
        except ClientError as e:
            logger.warning(f"Failed to refresh DynamoDB table metadata for {self.table_name}: {e}")

    def _build_projection_expression(self, projection: List[str]) -> tuple[str, Dict[str, str]]:
        """Build a ProjectionExpression and ExpressionAttributeNames mapping.

        DynamoDB reserves some attribute names such as `status`, so we always
        alias projection attributes using placeholders.
        """
        expr_names: Dict[str, str] = {}
        parts: List[str] = []
        for idx, attr in enumerate(projection):
            if "." in attr:
                segments = []
                for seg_idx, segment in enumerate(attr.split(".")):
                    placeholder = f"#p{idx}_{seg_idx}"
                    expr_names[placeholder] = segment
                    segments.append(placeholder)
                parts.append(".".join(segments))
            else:
                placeholder = f"#p{idx}"
                expr_names[placeholder] = attr
                parts.append(placeholder)
        return ", ".join(parts), expr_names

    def get_item(self, key: Dict[str, Any], projection: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """Get a single item by key.

        Args:
            key: Primary key dictionary
            projection: Optional list of attribute names to return

        Returns:
            Item dictionary or None if not found
        """
        try:
            kwargs: Dict[str, Any] = {"Key": key}
            if projection:
                expr, expr_names = self._build_projection_expression(projection)
                kwargs["ProjectionExpression"] = expr
                kwargs["ExpressionAttributeNames"] = expr_names
            response = self.table.get_item(**kwargs)
            item = response.get("Item")
            logger.debug("DynamoDB get_item table=%s key=%s item=%s", self.table_name, key, item)
            return item
        except ClientError as e:
            logger.error(f"Failed to get item from {self.table_name}: {e}")
            raise

    def put_item(self, item: Dict[str, Any], condition: Optional[Any] = None) -> None:
        """Put an item into the table.

        Args:
            item: Item dictionary to store
            condition: Optional boto3 ConditionExpression (e.g. Attr("applicant_id").not_exists())
        """
        try:
            logger.debug("Putting item into DynamoDB table %s: %s", self.table_name, item)
            kwargs: Dict[str, Any] = {"Item": _normalize_dynamodb_value(item)}
            if condition is not None:
                kwargs["ConditionExpression"] = condition
            self.table.put_item(**kwargs)
        except ClientError as e:
            logger.error(f"Failed to put item to {self.table_name}: {e}")
            raise

    def update_item(self, key: Dict[str, Any], fields: Dict[str, Any]) -> None:
        """Atomically update specific attributes without touching others.

        Uses DynamoDB UpdateExpression so only the supplied fields are written —
        no read-modify-write, no last-writer-wins risk.

        Args:
            key: Primary key dict (e.g. {"applicant_id": "..."})
            fields: Attribute names → new values (must not include key attributes)
        """
        if not fields:
            return
        try:
            expr_names: Dict[str, str] = {}
            expr_values: Dict[str, Any] = {}
            set_parts: List[str] = []
            for i, (attr, val) in enumerate(fields.items()):
                name_tok = f"#f{i}"
                val_tok = f":v{i}"
                expr_names[name_tok] = attr
                expr_values[val_tok] = _normalize_dynamodb_value(val)
                set_parts.append(f"{name_tok} = {val_tok}")
            logger.info(
                "Updating DynamoDB table %s item %s with fields %s",
                self.table_name,
                key,
                fields,
            )
            self.table.update_item(
                Key=key,
                UpdateExpression="SET " + ", ".join(set_parts),
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values,
            )
        except ClientError as e:
            logger.error(f"Failed to update_item in {self.table_name}: {e}")
            raise

    def batch_put_items(self, items: List[Dict[str, Any]]) -> None:
        """Write multiple items using batch_writer (handles 25-item API limit automatically)."""
        if not items:
            return
        logger.debug("Batch writing %d items to %s", len(items), self.table_name)
        with self.table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=_normalize_dynamodb_value(item))

    def delete_item(self, key: Dict[str, Any]) -> None:
        """Delete an item by key.
        
        Args:
            key: Primary key dictionary
        """
        try:
            logger.info("Deleting item from DynamoDB table %s with key %s", self.table_name, key)
            self.table.delete_item(Key=key)
        except ClientError as e:
            logger.error(f"Failed to delete item from {self.table_name}: {e}")
            raise

    def query(self, key_condition_expression, index_name: Optional[str] = None,
              filter_expression=None, limit: Optional[int] = None,
              scan_index_forward: bool = True,
              projection: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Query items using key condition expression, paginating through all result pages.

        `limit` caps the number of *returned* items, not the number DynamoDB evaluates
        per page — do not pass it as DynamoDB's Limit when FilterExpression is active.
        """
        try:
            kwargs: Dict[str, Any] = {
                "KeyConditionExpression": key_condition_expression,
                "ScanIndexForward": scan_index_forward,
            }
            if index_name:
                kwargs["IndexName"] = index_name
            if filter_expression:
                kwargs["FilterExpression"] = filter_expression
            if projection:
                expr, expr_names = self._build_projection_expression(projection)
                kwargs["ProjectionExpression"] = expr
                kwargs["ExpressionAttributeNames"] = expr_names

            items: List[Dict[str, Any]] = []
            while True:
                response = self.table.query(**kwargs)
                items.extend(response.get("Items", []))
                last_key = response.get("LastEvaluatedKey")
                if not last_key or (limit and len(items) >= limit):
                    break
                kwargs["ExclusiveStartKey"] = last_key

            return items[:limit] if limit else items
        except ClientError as e:
            logger.error(f"Failed to query {self.table_name}: {e}")
            raise

    def scan(self, filter_expression=None, limit: Optional[int] = None,
             projection: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Scan items in the table, paginating through all result pages.

        `limit` caps the number of *returned* items. DynamoDB's own Limit parameter
        caps items *evaluated* per page (not returned), which is incorrect when a
        FilterExpression is active — so we paginate manually instead.
        """
        try:
            kwargs: Dict[str, Any] = {}
            if filter_expression:
                kwargs["FilterExpression"] = filter_expression
            if projection:
                expr, expr_names = self._build_projection_expression(projection)
                kwargs["ProjectionExpression"] = expr
                kwargs["ExpressionAttributeNames"] = expr_names

            items: List[Dict[str, Any]] = []
            while True:
                response = self.table.scan(**kwargs)
                items.extend(response.get("Items", []))
                last_key = response.get("LastEvaluatedKey")
                if not last_key or (limit and len(items) >= limit):
                    break
                kwargs["ExclusiveStartKey"] = last_key

            return items[:limit] if limit else items
        except ClientError as e:
            logger.error(f"Failed to scan {self.table_name}: {e}")
            raise

    # Convenience methods for common key patterns
    def get_by_id(self, id_key: str, id_value: str) -> Optional[Dict[str, Any]]:
        return self.get_item({id_key: id_value})

    def get_by_keys(self, **keys) -> Optional[Dict[str, Any]]:
        return self.get_item(keys)

    def _find_index_for_key(self, key_name: str) -> Optional[str]:
        """Find a GSI name whose hash key matches the requested key."""
        gsis = getattr(self.table, "global_secondary_indexes", None)
        if not gsis:
            return None

        for gsi in gsis:
            for key_schema in gsi.get("KeySchema", []):
                if key_schema.get("AttributeName") == key_name and key_schema.get("KeyType") == "HASH":
                    return gsi.get("IndexName")
        return None

    def query_by_key(self, key_name: str, key_value: str, index_name: Optional[str] = None, filter_expression=None) -> List[Dict[str, Any]]:
        """Query items by a key (generic).
        
        Args:
            key_name: Key attribute name
            key_value: Key value
            index_name: Optional GSI/LSI name for GSI/LSI queries
            filter_expression: Optional filter expression applied after the key condition
            
        Returns:
            List of matching items
        """
        if index_name is None:
            if not any(key["AttributeName"] == key_name for key in self.table.key_schema):
                index_name = self._find_index_for_key(key_name)
        return self.query(
            Key(key_name).eq(key_value),
            index_name=index_name,
            filter_expression=filter_expression,
        )

    def delete_by_keys(self, **keys) -> None:
        self.delete_item(keys)

    def list_active(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        return self.scan(filter_expression=Attr("is_active").eq(True), limit=limit)

    def delete_by_id(self, id_key: str, id_value: str) -> None:
        self.delete_item({id_key: id_value})

    def get_active_items(self, key_name: str, key_value: str) -> List[Dict[str, Any]]:
        """Query active items by key.
        
        Args:
            key_name: Key attribute name
            key_value: Key value
            
        Returns:
            List of active matching items
        """
        items = self.query_by_key(key_name, key_value)
        return _filter_active(items)

    def list_all(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.scan(limit=limit)


    # ===== Applicant mapping convenience methods =====
    def query_by_applicant(self, applicant_id: str) -> List[Dict[str, Any]]:
        return self.query(Key("applicant_id").eq(applicant_id))

    # ===== Auth client convenience methods =====
    def get_active_by_applicant(self, applicant_id: str) -> List[Dict[str, Any]]:
        items = self.query_by_applicant(applicant_id)
        return _filter_active(items)
