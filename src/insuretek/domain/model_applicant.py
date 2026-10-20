"""Pydantic domain models for insurance applicant and policy data structures."""

from typing import Dict, Any, Optional, Union
from pydantic import BaseModel, Field, field_validator

from insuretek.domain.enums import PolicyType, RiskTier, PolicyStatus


_LENIENT = {"extra": "ignore", "populate_by_name": True}


def _coerce_to_str(v):
    """Accept int/float from LLM tool calls; store as str."""
    if v is None or isinstance(v, str):
        return v
    return str(v)


class CoverageRequest(BaseModel):
    coverage_amount: Optional[str] = Field(default=None, description="Requested coverage limit (e.g. '$300,000')")
    deductible: Optional[str] = Field(default=None, description="Chosen deductible (e.g. '$2,500')")

    model_config = _LENIENT

    @field_validator("coverage_amount", "deductible", mode="before")
    @classmethod
    def coerce_numeric(cls, v):
        return _coerce_to_str(v)


class RiskProfile(BaseModel):
    property_type: Optional[str] = Field(default=None, description="Property type (e.g. single_family, condo, commercial)")
    year_built: Optional[str] = Field(default=None, description="Year the property was built")
    prior_claims_count: Optional[str] = Field(default=None, description="Number of prior claims in the last 5 years")
    construction_type: Optional[str] = Field(default=None, description="Construction material (e.g. frame, masonry, steel)")
    occupancy_type: Optional[str] = Field(default=None, description="Owner-occupied, tenant-occupied, or vacant")
    square_footage: Optional[str] = Field(default=None, description="Total square footage of the insured property")

    model_config = _LENIENT

    @field_validator("year_built", "prior_claims_count", "square_footage", mode="before")
    @classmethod
    def coerce_numeric(cls, v):
        return _coerce_to_str(v)


class LocationAddress(BaseModel):
    line1: Optional[str] = Field(default=None, description="Street address line 1")
    line2: Optional[str] = Field(default=None, description="Street address line 2 (optional)")
    city: Optional[str] = Field(default=None, description="City")
    state_or_province_code: Optional[str] = Field(default=None, description="State/province code (e.g. 'TX', 'CA')")
    postal_code: Optional[str] = Field(default=None, description="ZIP/postal code")
    country_code: Optional[str] = Field(default=None, description="Country code (e.g. 'US')")

    model_config = _LENIENT


class ContactInfo(BaseModel):
    email: Optional[str] = Field(default=None, description="Contact email address")
    phone: Optional[str] = Field(default=None, description="Contact phone number (optional)")
    phone_country_code: Optional[str] = Field(default=None, description="Phone country code (optional)")

    model_config = _LENIENT


class ApplicantData(BaseModel):
    """Complete insurance applicant intake payload."""
    applicant_name: Optional[str] = Field(default=None, description="Applicant or business name")
    policy_type: Optional[str] = Field(default=None, description="Policy type: HOME | AUTO | COMMERCIAL | LIFE")
    declared_value: Optional[str] = Field(default=None, description="Declared property or asset value")
    enterprise_identifier: Optional[str] = Field(default=None, description="Enterprise / group identifier")
    coverage_request: Optional[CoverageRequest] = Field(default_factory=CoverageRequest, description="Coverage amount and deductible")
    contact_info: Optional[ContactInfo] = Field(default_factory=ContactInfo, description="Applicant contact details")
    location_address: Optional[LocationAddress] = Field(default_factory=LocationAddress, description="Property / risk address")
    risk_profile: Optional[RiskProfile] = Field(default_factory=RiskProfile, description="Property and risk details")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

    model_config = _LENIENT

    @field_validator("declared_value", mode="before")
    @classmethod
    def coerce_numeric(cls, v):
        return _coerce_to_str(v)

    @field_validator("policy_type", mode="before")
    @classmethod
    def validate_policy_type(cls, v):
        if v is None:
            return v
        valid = {e.value for e in PolicyType}
        if isinstance(v, str) and v.upper() in valid:
            return v.upper()
        return v


class ApplicantUpdateData(BaseModel):
    """Partial update payload for an existing applicant."""
    applicant_id: str = Field(..., description="Unique applicant identifier (UUID)")
    applicant_name: Optional[str] = Field(default=None)
    policy_type: Optional[str] = Field(default=None)
    declared_value: Optional[str] = Field(default=None)
    coverage_request: Optional[CoverageRequest] = Field(default=None)
    contact_info: Optional[ContactInfo] = Field(default=None)
    location_address: Optional[LocationAddress] = Field(default=None)
    risk_profile: Optional[RiskProfile] = Field(default=None)
    metadata: Optional[Dict[str, Any]] = Field(default=None)

    model_config = _LENIENT

    @field_validator("declared_value", mode="before")
    @classmethod
    def coerce_numeric(cls, v):
        return _coerce_to_str(v)


class PolicyData(BaseModel):
    """Insurance policy record."""
    applicant_id: str = Field(..., description="Owning applicant identifier")
    policy_type: str = Field(..., description="Policy type: HOME | AUTO | COMMERCIAL | LIFE")
    coverage_amount: Optional[str] = Field(default=None)
    deductible: Optional[str] = Field(default=None)
    declared_value: Optional[str] = Field(default=None)
    premium_estimate: Optional[str] = Field(default=None, description="Quoted annual premium")
    status: str = Field(default=PolicyStatus.DRAFT.value, description="Policy lifecycle status")
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = _LENIENT

    @field_validator("coverage_amount", "deductible", "declared_value", "premium_estimate", mode="before")
    @classmethod
    def coerce_numeric(cls, v):
        return _coerce_to_str(v)
