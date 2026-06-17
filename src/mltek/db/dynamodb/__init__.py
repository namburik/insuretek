"""Shared DynamoDB utilities."""
from .dynamodb_operations import DynamoDBTable
from .dynamodb_operations  import get_dynamodb_resource

__all__ = ["DynamoDBTable", "get_dynamodb_resource"]
