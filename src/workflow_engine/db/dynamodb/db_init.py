"""Initialize DynamoDB tables for the workflow service."""

import sys
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from mltek.config.config_common import Config
from mltek.common.infra import load_config
from mltek.common.infra.error_helpers import handle_db_errors
from mltek.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)

_SCHEMA_PATH = Path(__file__).parent / "workflow_ddb_schema.json"

_TABLE_NAMES = [
    "itek_workflow_applicant",
    "itek_workflow_approval_tasks",
    "itek_workflow_checkpoints",
    "itek_workflow_checkpoint_writes",
    "itek_workflow_applicant_request",
]


def _make_client(endpoint_url: Optional[str], region_name: Optional[str]):
    return boto3.client(
        "dynamodb",
        endpoint_url=endpoint_url or Config.DYNAMODB_ENDPOINT,
        region_name=region_name or Config.AWS_REGION,
        aws_access_key_id=Config.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=Config.AWS_SECRET_ACCESS_KEY,
    )


def load_schema(schema_path: Optional[str] = None) -> dict:
    path = Path(schema_path) if schema_path else _SCHEMA_PATH
    return load_config(path)


def create_tables(
    endpoint_url: Optional[str] = None,
    region_name: Optional[str] = None,
    schema_path: Optional[str] = None,
) -> None:
    client = _make_client(endpoint_url, region_name)
    schema = load_schema(schema_path)

    for table_def in schema.get("tables", []):
        table_name = table_def["TableName"]
        logger.debug("Creating Workflow DynamoDB table with definition: %s", table_def)
        try:
            client.create_table(**table_def)
            logger.info("✓ Table created: %s", table_name)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                logger.info("✓ Table already exists: %s", table_name)
            else:
                logger.error("✗ Failed to create table %s: %s", table_name, e)
                raise

    for ttl_def in schema.get("ttl", []):
        logger.debug("Updating Workflow DynamoDB TTL definition: %s", ttl_def)
        try:
            client.update_time_to_live(
                TableName=ttl_def["TableName"],
                TimeToLiveSpecification={"Enabled": True, "AttributeName": ttl_def["AttributeName"]},
            )
            logger.info("✓ TTL enabled on %s.%s", ttl_def["TableName"], ttl_def["AttributeName"])
        except ClientError as e:
            logger.warning("TTL update skipped for %s: %s", ttl_def["TableName"], e)


def delete_tables(
    endpoint_url: Optional[str] = None,
    region_name: Optional[str] = None,
) -> None:
    client = _make_client(endpoint_url, region_name)
    for table_name in _TABLE_NAMES:
        try:
            client.delete_table(TableName=table_name)
            logger.info("✓ Table deleted: %s", table_name)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                logger.info("✓ Table does not exist: %s", table_name)
            else:
                logger.error("✗ Failed to delete table %s: %s", table_name, e)
                raise


@handle_db_errors
def list_tables(
    endpoint_url: Optional[str] = None,
    region_name: Optional[str] = None,
) -> list:
    client = _make_client(endpoint_url, region_name)
    tables = client.list_tables().get("TableNames", [])
    logger.info("Found %d tables: %s", len(tables), tables)
    return tables


@handle_db_errors
def get_table_status(
    table_name: str,
    endpoint_url: Optional[str] = None,
    region_name: Optional[str] = None,
) -> dict:
    client = _make_client(endpoint_url, region_name)
    info = client.describe_table(TableName=table_name).get("Table", {})
    return {
        "name":       info.get("TableName"),
        "status":     info.get("TableStatus"),
        "item_count": info.get("ItemCount"),
        "size_bytes": info.get("TableSizeBytes"),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python db_init.py [create|delete|list]")
        sys.exit(1)

    command = sys.argv[1]
    if command == "create":
        create_tables()
    elif command == "delete":
        confirm = input("Delete all workflow DDB tables? (yes/no): ")
        if confirm.lower() == "yes":
            delete_tables()
        else:
            logger.info("Delete cancelled")
    elif command == "list":
        for t in list_tables():
            print(get_table_status(t))
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
