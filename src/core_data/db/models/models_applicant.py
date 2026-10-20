"""Database models for insurance applicant and policy records."""

from pydantic import Field, field_serializer, ConfigDict
from typing import Dict, Any, Optional

from mltek.db.models.base import DatabaseModel, current_timestamp_ms
from insuretek.domain.enums import PolicyType, RiskTier, PolicyStatus
from insuretek.domain.model_applicant import CoverageRequest, RiskProfile, LocationAddress, ContactInfo


class Applicant(DatabaseModel):
    """Insurance applicant record — single schema for DynamoDB and PostgreSQL."""

    applicant_id: str = Field(..., description="Unique applicant identifier (UUID)", min_length=1)
    applicant_name: Optional[str] = Field(default="", description="Applicant or business name")
    policy_type: Optional[PolicyType] = Field(default=None, description="Policy type")
    declared_value: Optional[str] = Field(default=None, description="Declared asset value")
    coverage_request: Optional[CoverageRequest] = Field(default_factory=CoverageRequest)
    contact_info: Optional[ContactInfo] = Field(default_factory=ContactInfo)
    location_address: Optional[LocationAddress] = Field(default_factory=LocationAddress)
    risk_profile: Optional[RiskProfile] = Field(default_factory=RiskProfile)
    risk_tier: Optional[RiskTier] = Field(default=None, description="Underwriting risk tier assigned after gate checks")
    enterprise_identifier: Optional[str] = Field(default=None)
    created_at: int = Field(default_factory=current_timestamp_ms, gt=0)
    updated_at: int = Field(default_factory=current_timestamp_ms, gt=0)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(validate_assignment=True, populate_by_name=True)

    @field_serializer("policy_type", "risk_tier", when_used="json")
    def serialize_enums(self, value):
        return value.value if hasattr(value, "value") else value


class InsurancePolicy(DatabaseModel):
    """Insurance policy record — one per applicant/policy_type lifecycle."""

    policy_id: str = Field(..., description="Unique policy identifier (pol_xxx)", min_length=1)
    applicant_id: str = Field(..., description="Owning applicant identifier", min_length=1)
    policy_type: Optional[PolicyType] = Field(default=None)
    coverage_amount: Optional[str] = Field(default=None)
    deductible: Optional[str] = Field(default=None)
    declared_value: Optional[str] = Field(default=None)
    premium_estimate: Optional[str] = Field(default=None, description="Quoted annual premium")
    status: PolicyStatus = Field(default=PolicyStatus.DRAFT)
    created_at: int = Field(default_factory=current_timestamp_ms, gt=0)
    updated_at: int = Field(default_factory=current_timestamp_ms, gt=0)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(validate_assignment=True, populate_by_name=True)

    @field_serializer("policy_type", "status", when_used="json")
    def serialize_enums(self, value):
        return value.value if hasattr(value, "value") else value


__all__ = ["Applicant", "InsurancePolicy"]
