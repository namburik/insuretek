"""Initialize DynamoDB tables from schema definition."""

import sys
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from shared.config.config_common import Config
from shared.common.infra import load_config
from shared.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)



def load_schema(schema_path: Optional[str] = None) -> dict:
    """Load DynamoDB schema from JSON file.
    
    Args:
        schema_path: Path to schema JSON file. If None, uses default location.
        
    Returns:
        Schema dictionary
    """
    if schema_path is None:
        schema_path = Path(__file__).parent / "dynamodb_schema.json"
    
    return load_config(schema_path)


def create_tables(
    endpoint_url: Optional[str] = None,
    region_name: Optional[str] = None,
    schema_path: Optional[str] = None,
) -> None:
    """Create DynamoDB tables from schema.
    
    Args:
        endpoint_url: DynamoDB endpoint URL (for local development)
        region_name: AWS region name
        schema_path: Path to schema JSON file
    """
    endpoint_url = endpoint_url or Config.DYNAMODB_ENDPOINT
    region_name = region_name or Config.AWS_REGION
    
    # Create DynamoDB client
    dynamodb = boto3.client(
        "dynamodb",
        endpoint_url=endpoint_url,
        region_name=region_name,
        aws_access_key_id=Config.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=Config.AWS_SECRET_ACCESS_KEY,
    )
    
    logger.info(f"Using DynamoDB endpoint: {endpoint_url} in region: {region_name}")
    # Load schema — create both prod and shadow tables
    schema = load_schema(schema_path)
    tables = schema.get("tables", []) + schema.get("shadow_tables", [])

    for table_def in tables:
        table_name = table_def["TableName"]
        logger.debug("Creating DynamoDB table with definition: %s", table_def)
        try:
            logger.info(f"Creating table: {table_name}")
            dynamodb.create_table(**table_def)
            logger.info(f"✓ Table created: {table_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                logger.info(f"✓ Table already exists: {table_name}")
            else:
                logger.error(f"✗ Failed to create table {table_name}: {e}")
                raise


def delete_tables(
    endpoint_url: Optional[str] = None,
    region_name: Optional[str] = None,
) -> None:
    """Delete all DynamoDB tables.
    
    Args:
        endpoint_url: DynamoDB endpoint URL (for local development)
        region_name: AWS region name
    """
    endpoint_url = endpoint_url or Config.DYNAMODB_ENDPOINT
    region_name = region_name or Config.AWS_REGION
    
    dynamodb = boto3.client(
        "dynamodb",
        endpoint_url=endpoint_url,
        region_name=region_name,
        aws_access_key_id=Config.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=Config.AWS_SECRET_ACCESS_KEY,
    )
    
    table_names = [
        "itek_login_activity", "itek_policy_audit", "itek_portal_access",
        "itek_applicants", "itek_insurance_policies",
        "itek_login_activity_shadow", "itek_policy_audit_shadow",
        "itek_portal_access_shadow", "itek_applicants_shadow", "itek_insurance_policies_shadow",
    ]
    
    for table_name in table_names:
        try:
            logger.info(f"Deleting table: {table_name}")
            dynamodb.delete_table(TableName=table_name)
            logger.info(f"✓ Table deleted: {table_name}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                logger.info(f"✓ Table does not exist: {table_name}")
            else:
                logger.error(f"✗ Failed to delete table {table_name}: {e}")
                raise


def list_tables(
    endpoint_url: Optional[str] = None,
    region_name: Optional[str] = None,
) -> list:
    """List all DynamoDB tables.
    
    Args:
        endpoint_url: DynamoDB endpoint URL (for local development)
        region_name: AWS region name
        
    Returns:
        List of table names
    """
    endpoint_url = endpoint_url or Config.DYNAMODB_ENDPOINT
    region_name = region_name or Config.AWS_REGION
    
    dynamodb = boto3.client(
        "dynamodb",
        endpoint_url=endpoint_url,
        region_name=region_name,
        aws_access_key_id=Config.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=Config.AWS_SECRET_ACCESS_KEY,
    )
    
    try:
        response = dynamodb.list_tables()
        tables = response.get("TableNames", [])
        logger.info(f"Found {len(tables)} tables: {tables}")
        return tables
    except ClientError as e:
        logger.error(f"Failed to list tables: {e}")
        raise


def get_table_status(
    table_name: str,
    endpoint_url: Optional[str] = None,
    region_name: Optional[str] = None,
) -> dict:
    """Get status of a DynamoDB table.
    
    Args:
        table_name: Name of the table
        endpoint_url: DynamoDB endpoint URL (for local development)
        region_name: AWS region name
        
    Returns:
        Table status information
    """
    endpoint_url = endpoint_url or Config.DYNAMODB_ENDPOINT
    region_name = region_name or Config.AWS_REGION
    
    dynamodb = boto3.client(
        "dynamodb",
        endpoint_url=endpoint_url,
        region_name=region_name,
        aws_access_key_id=Config.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=Config.AWS_SECRET_ACCESS_KEY,
    )
    
    try:
        response = dynamodb.describe_table(TableName=table_name)
        table_info = response.get("Table", {})
        return {
            "name": table_info.get("TableName"),
            "status": table_info.get("TableStatus"),
            "item_count": table_info.get("ItemCount"),
            "size_bytes": table_info.get("TableSizeBytes"),
        }
    except ClientError as e:
        logger.error(f"Failed to get table status: {e}")
        raise


if __name__ == "__main__":
    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == "create":
            create_tables()
        elif command == "delete":
            confirm = input("Are you sure you want to delete all tables? (yes/no): ")
            if confirm.lower() == "yes":
                delete_tables()
            else:
                logger.info("Delete cancelled")
        elif command == "list":
            tables = list_tables()
            for table in tables:
                status = get_table_status(table)
                logger.debug(f"{table}: {status}")
        else:
            logger.debug(f"Unknown command: {command}")
            logger.debug("Usage: python db_init.py [create|delete|list]")
    else:
        logger.debug("Usage: python db_init.py [create|delete|list]")
