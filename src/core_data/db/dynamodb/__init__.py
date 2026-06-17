"""DynamoDB persistence layer shim for compatibility.

The implementation is now located under shared.db.dynamodb.
"""

from mltek.db.dynamodb.dynamodb_operations import DynamoDBTable
from core_data.db.dynamodb.factory import (
    get_dynamodb_resource,
    get_applicants_table_operations,
    get_policies_table_operations,
)
from .db_init import create_tables, delete_tables, list_tables, get_table_status

__all__ = [
    "DynamoDBTable",
    "get_dynamodb_resource",
    "get_applicants_table_operations",
    "get_policies_table_operations",
    "create_tables",
    "delete_tables",
    "list_tables",
    "get_table_status",
]
