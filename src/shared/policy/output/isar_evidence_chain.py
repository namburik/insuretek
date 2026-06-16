"""ISAR evidence chain models — structured output for any ISAR-grounded semantic analysis."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class RuleEvidence(BaseModel):
    rule_id: str = Field(description=(
        "ISAR section + rule identifier, e.g. 'HOME.UNDERWRITING.YEAR_BUILT', "
        "'PREMIUM.CLAIMS_MULTIPLIER', 'LIFECYCLE.STATUS.BOUND'"
    ))
    field: str | None = Field(default=None, description=(
        "Applicant field evaluated, e.g. 'prior_claims_count'; "
        "null for summary-level rules with no single field"
    ))
    actual_value: str | None = Field(default=None, description=(
        "String representation of the observed field value; "
        "null if the field was absent from applicant context"
    ))
    threshold: str | None = Field(default=None, description=(
        "ISAR expectation or threshold compared against, e.g. '>3 claims/5yrs', "
        "'year_built >2000 → PREFERRED', 'status BOUND → payment pending'"
    ))
    finding: str | None = Field(default=None, description=(
        "What this rule contributes to the conclusion for the user's specific question — "
        "one sentence, no data restatement"
    ))

    @field_validator("field", "actual_value", "threshold", "finding", mode="before")
    @classmethod
    def _coerce_to_str(cls, v):
        """Accept null (absent data) or coerce non-string scalars the LLM may emit."""
        if v is None:
            return None
        return str(v)


class IsarDecisionOutput(BaseModel):
    subject: str = Field(description=(
        "The analysis aspect this response addresses: one of "
        "'risk_tier', 'coverage_adequacy', 'premium_factors', 'cross_sell', "
        "'churn_risk', 'lifecycle_phase', 'underwriting_gap', or a free label if none fit"
    ))
    conclusion: str = Field(description=(
        "Principal finding for this subject — one concise sentence, "
        "e.g. 'HIGH_RISK — elevated claims history drives a +35% premium load'"
    ))
    evidence_chain: list[RuleEvidence] = Field(description=(
        "Ordered list of ISAR rules/facts evaluated to reach the conclusion. "
        "Include only rules actually relevant to the user's request."
    ))
    narrative: str = Field(description=(
        "Full human-readable semantic analysis — same content as before, "
        "derived insights only, no data restatement"
    ))


