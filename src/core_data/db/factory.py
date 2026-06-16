"""Factory functions for core_data DynamoDB table operations."""

from core_data.db.dynamodb.factory import (
    get_login_activity_table_operations,
    get_portal_access_table_operations,
    get_policy_audit_table_operations,
    get_applicants_table_operations,
    get_policies_table_operations,
)

__all__ = [
    "get_login_activity_table_operations",
    "get_portal_access_table_operations",
    "get_policy_audit_table_operations",
    "get_applicants_table_operations",
    "get_policies_table_operations",
]
