import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

_LENIENT = {"extra": "ignore", "populate_by_name": True}


class _ContactInfo(BaseModel):
    model_config = _LENIENT
    email: Optional[str] = None
    phone: Optional[str] = None


class _LocationAddress(BaseModel):
    model_config = _LENIENT
    line1: Optional[str] = None
    city: Optional[str] = None
    state_or_province_code: Optional[str] = None
    postal_code: Optional[str] = None
    country_code: Optional[str] = None


class _CoverageRequest(BaseModel):
    model_config = _LENIENT
    coverage_amount: Optional[str] = None
    deductible: Optional[str] = None


class _RiskProfile(BaseModel):
    model_config = _LENIENT
    property_type: Optional[str] = None
    year_built: Optional[str] = None
    prior_claims_count: Optional[str] = None


class DraftUpdates(BaseModel):
    model_config = _LENIENT
    applicant_name: Optional[str] = None
    policy_type: Optional[str] = None
    coverage_request: Optional[_CoverageRequest] = None
    declared_value: Optional[str] = None
    contact_info: Optional[_ContactInfo] = None
    location_address: Optional[_LocationAddress] = None
    risk_profile: Optional[_RiskProfile] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.model_dump(exclude_none=True).items() if v}


class ExtractionResult(BaseModel):
    updates: DraftUpdates = Field(default_factory=DraftUpdates)
    user_confirmed: bool = Field(default=False)


@dataclass
class IntakeSession:
    # Acquired for the full duration of each turn so concurrent requests on the
    # same session_id queue up rather than racing on draft/history/phase.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    draft: dict = field(default_factory=dict)
    phase: str = "collecting"
    history: list = field(default_factory=list)  # [HumanMessage | AIMessage, ...]
    request_id: Optional[str] = None
    applicant_id: Optional[str] = None
    last_active: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    env: str = "prod"  # "prod" | "shadow" — controls which DynamoDB tables are targeted
