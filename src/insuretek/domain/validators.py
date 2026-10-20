"""Validation utilities for applicant data and configurations."""

from typing import Dict, Any, Tuple, Optional, List

from insuretek.domain.enums import PolicyType
from insuretek.domain.constants import (
    ApplicantJsonFields,
    LocationAddressFields,
    ContactInfoFields,
    ValidationMessages,
)


class ApplicantValidator:
    """Validates applicant JSON and related data structures."""

    @staticmethod
    def validate_applicant_json(applicant_json: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate applicant JSON against required schema.

        Args:
            applicant_json: Raw applicant data from client

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check required top-level fields
        required_fields = ApplicantJsonFields.required()
        for field in required_fields:
            if field not in applicant_json:
                return False, ValidationMessages.MISSING_REQUIRED_FIELD.format(field=field)

            # For string fields, ensure they are non-empty
            value = applicant_json.get(field)
            if isinstance(value, str) and not value.strip():
                return False, ValidationMessages.MISSING_REQUIRED_FIELD.format(field=field)

        # Validate policyType enum
        policy_type = applicant_json.get(ApplicantJsonFields.POLICY_TYPE)
        valid_types = [t.value for t in PolicyType]
        if policy_type not in valid_types:
            return False, ValidationMessages.INVALID_ENUM_VALUE.format(
                field="policy_type",
                values=", ".join(valid_types)
            )

        # Validate nested structures (only if provided and non-None - optional)
        location = applicant_json.get(ApplicantJsonFields.LOCATION_ADDRESS)
        if location is not None:
            is_valid, error = ApplicantValidator.validate_location_address(location)
            if not is_valid:
                return False, error

        contact = applicant_json.get(ApplicantJsonFields.CONTACT_INFO)
        if contact is not None:
            is_valid, error = ApplicantValidator.validate_contact_info(contact)
            if not is_valid:
                return False, error

        return True, None

    @staticmethod
    def _validate_optional_subobject(data: Dict[str, Any], parent: str) -> Tuple[bool, Optional[str]]:
        """Validate a partial nested optional object without requiring all subfields."""
        if not isinstance(data, dict) or not data:
            return True, None

        for key, value in data.items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                return False, ValidationMessages.INVALID_NESTED_FIELD.format(
                    parent=parent,
                    field=key,
                )
            if not isinstance(value, (str, int, float, bool)):
                return False, ValidationMessages.INVALID_NESTED_FIELD.format(
                    parent=parent,
                    field=key,
                )

        return True, None

    @staticmethod
    def validate_location_address(location: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate location address structure."""
        return ApplicantValidator._validate_optional_subobject(location, "location_address")

    @staticmethod
    def validate_contact_info(contact: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate contact info structure."""
        return ApplicantValidator._validate_optional_subobject(contact, "contact_info")

    @staticmethod
    def validate_string_not_empty(value: str, field_name: str) -> Tuple[bool, Optional[str]]:
        """Validate that a string field is not empty."""
        if not value or not isinstance(value, str) or not value.strip():
            return False, f"{field_name} must be a non-empty string"
        return True, None

    @staticmethod
    def validate_enum_value(value: str, valid_values: List[str], field_name: str) -> Tuple[bool, Optional[str]]:
        """Validate that value is in the list of valid values."""
        if value not in valid_values:
            return False, ValidationMessages.INVALID_ENUM_VALUE.format(
                field=field_name,
                values=", ".join(valid_values)
            )
        return True, None
