"""Factory functions for DynamoDB table operations initialization."""

from mltek.common.infra.context import get_env
from mltek.db.dynamodb.dynamodb_operations import DynamoDBTable, get_dynamodb_resource


def env_ddb():
    if get_env() == 'prod':
        from insuretek.domain.constants import DynamoDBTableNames
    elif get_env() == 'shadow':
        from insuretek.domain.constants import DynamoDBShadowTableNames as DynamoDBTableNames
    return DynamoDBTableNames



def get_login_activity_table_operations():
    return DynamoDBTable(get_dynamodb_resource().Table(env_ddb().LOGIN_ACTIVITY))


def get_policy_audit_table_operations():
    return DynamoDBTable(get_dynamodb_resource().Table(env_ddb().POLICY_AUDIT))


def get_portal_access_table_operations():
    return DynamoDBTable(get_dynamodb_resource().Table(env_ddb().PORTAL_ACCESS))


def get_applicants_table_operations():
    return DynamoDBTable(get_dynamodb_resource().Table(env_ddb().APPLICANTS))


def get_policies_table_operations():
    return DynamoDBTable(get_dynamodb_resource().Table(env_ddb().INSURANCE_POLICIES))
