"""Application-wide constants for Insuretek MCP System."""

from enum import Enum


ENTERPRISE_IDENTIFIER_DEFAULT = "ENT-ITEK-001"


class ApplicantJsonFields:
    """Field names for applicant JSON input (insurance underwriting)."""
    APPLICANT_NAME = "applicant_name"
    ENTERPRISE_IDENTIFIER = "enterprise_identifier"
    POLICY_TYPE = "policy_type"
    COVERAGE_REQUEST = "coverage_request"
    DECLARED_VALUE = "declared_value"
    LOCATION_ADDRESS = "location_address"
    CONTACT_INFO = "contact_info"
    RISK_PROFILE = "risk_profile"

    @classmethod
    def required(cls):
        """Return list of required fields that must be present and non-empty."""
        return [
            cls.APPLICANT_NAME,
            cls.POLICY_TYPE,
            cls.LOCATION_ADDRESS,
            cls.CONTACT_INFO,
        ]


class LocationAddressFields:
    """Required fields for location address."""
    LINE1 = "line1"
    CITY = "city"
    STATE_OR_PROVINCE = "state_or_province_code"
    
    @classmethod
    def required(cls):
        return [cls.LINE1, cls.CITY, cls.STATE_OR_PROVINCE]


class ContactInfoFields:
    """Required fields for contact info."""
    EMAIL = "email"
    PHONE = "phone"

    @classmethod
    def required(cls):
        return [cls.EMAIL, cls.PHONE]


class ServerDefaults:
    """MCP Server defaults and configuration."""
    CACHE_TTL_SECONDS = 3600
    REQUEST_TIMEOUT_SECONDS = 30
    MAX_APPLICANTS = 100000


class DynamoDBTableNames:
    """DynamoDB table names (single source of truth)."""
    APPLICANTS = "itek_applicants"
    INSURANCE_POLICIES = "itek_insurance_policies"
    LOGIN_ACTIVITY = "itek_login_activity"
    POLICY_AUDIT = "itek_policy_audit"
    PORTAL_ACCESS = "itek_portal_access"


class DynamoDBShadowTableNames:
    """Shadow (sandbox) DynamoDB table names — mirrors DynamoDBTableNames with _shadow suffix."""
    APPLICANTS = "itek_applicants_shadow"
    INSURANCE_POLICIES = "itek_insurance_policies_shadow"
    LOGIN_ACTIVITY = "itek_login_activity_shadow"
    POLICY_AUDIT = "itek_policy_audit_shadow"
    PORTAL_ACCESS = "itek_portal_access_shadow"


class ValidationMessages:
    """Standard validation error messages."""
    MISSING_REQUIRED_FIELD = "Missing required field: {field}"
    INVALID_ENUM_VALUE = "Invalid {field}. Must be one of: {values}"
    INVALID_NESTED_FIELD = "{parent}.{field} is required"
    APPLICANT_NOT_FOUND = "Applicant not found: {applicant_id}"
