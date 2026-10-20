"""High-level service for insurance applicant and policy operations."""

from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from insuretek.domain.ids import generate_applicant_id, generate_policy_id
from insuretek.domain.enums import PolicyType, PolicyStatus, RiskTier
from insuretek.domain.constants import ENTERPRISE_IDENTIFIER_DEFAULT
from insuretek.domain.model_applicant import ApplicantData, CoverageRequest, RiskProfile, LocationAddress, ContactInfo
from core_data.db.models.models_applicant import Applicant, InsurancePolicy
from core_data.repositories.applicant_repository import ApplicantRepository, PolicyRepository
from insuretek.domain.validators import ApplicantValidator
from mltek.common.infra.setup import deep_merge
from mltek.common.log.logging_config import setup_structured_logging

logger = setup_structured_logging(__name__)


class ApplicantService:

    def __init__(
        self,
        applicant_repo: Optional[ApplicantRepository] = None,
        policy_repo: Optional[PolicyRepository] = None,
    ):
        self.applicant_repo = applicant_repo or ApplicantRepository()
        self.policy_repo = policy_repo or PolicyRepository()

    def create_applicant(self, applicant_json: Dict[str, Any]) -> Dict[str, Any]:
        is_valid, error = ApplicantValidator.validate_applicant_json(applicant_json)
        if not is_valid:
            raise ValueError(error)
        applicant_id = generate_applicant_id()
        data = ApplicantData.model_validate(applicant_json)

        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        applicant = Applicant(
            applicant_id=applicant_id,
            applicant_name=data.applicant_name or "",
            policy_type=PolicyType(data.policy_type) if data.policy_type else None,
            declared_value=data.declared_value,
            coverage_request=data.coverage_request,
            contact_info=data.contact_info,
            location_address=data.location_address,
            risk_profile=data.risk_profile,
            enterprise_identifier=data.enterprise_identifier or ENTERPRISE_IDENTIFIER_DEFAULT,
            metadata=data.metadata,
            created_at=now,
            updated_at=now,
        )
        self.applicant_repo.create(applicant)
        logger.info("ApplicantService.create_applicant applicant_id=%s", applicant_id)
        return {"applicant_id": applicant_id, "applicant": applicant.to_response()}

    def get_applicant(self, applicant_id: str) -> Dict[str, Any]:
        applicant = self.applicant_repo.get_by_id(applicant_id)
        if not applicant:
            raise ValueError(f"Applicant not found: {applicant_id}")
        logger.info("ApplicantService.get_applicant applicant_id=%s", applicant_id)
        return {"applicant_id": applicant_id, "applicant": applicant.to_response()}

    def search_applicants(self, policy_type: str) -> List[Dict[str, Any]]:
        applicants = self.applicant_repo.search(policy_type=policy_type.upper())
        logger.info("ApplicantService.search_applicants policy_type=%s count=%d", policy_type, len(applicants))
        return [{"applicant_id": a.applicant_id, "applicant": a.to_response()} for a in applicants]

    def get_all_applicants(self, limit: int = 10, before_created_at: Optional[int] = None) -> List[Dict[str, Any]]:
        applicants = self.applicant_repo.list_all(limit=limit, before_created_at=before_created_at)
        logger.info("ApplicantService.get_all_applicants count=%d", len(applicants))
        return [{"applicant_id": a.applicant_id, "applicant": a.to_response()} for a in applicants]

    def update_applicant(self, applicant_id: str, **patch_fields) -> Dict[str, Any]:
        applicant = self.applicant_repo.get_by_id(applicant_id)
        if not applicant:
            raise ValueError(f"Applicant not found: {applicant_id}")

        updated_fields = []
        scalar_map = {
            "applicant_name": str,
            "declared_value": str,
            "enterprise_identifier": str,
        }
        nested_map = {
            "coverage_request": CoverageRequest,
            "contact_info": ContactInfo,
            "location_address": LocationAddress,
            "risk_profile": RiskProfile,
        }

        for key, val in patch_fields.items():
            if key == "policy_type" and val:
                applicant.policy_type = PolicyType(val.upper())
                updated_fields.append(key)
            elif key in scalar_map and val is not None:
                setattr(applicant, key, val)
                updated_fields.append(key)
            elif key in nested_map and val is not None:
                existing = getattr(applicant, key) or nested_map[key]()
                merged = deep_merge(existing.model_dump(), val if isinstance(val, dict) else val.model_dump())
                setattr(applicant, key, nested_map[key].model_validate(merged))
                updated_fields.append(key)
            elif key == "metadata" and isinstance(val, dict):
                applicant.metadata = deep_merge(applicant.metadata or {}, val)
                updated_fields.append(key)

        self.applicant_repo.update(applicant)
        logger.info("ApplicantService.update_applicant applicant_id=%s fields=%s", applicant_id, updated_fields)
        return {"success": True, "applicant_id": applicant_id, "updated_fields": updated_fields}

    def delete_applicant(self, applicant_id: str) -> None:
        self.applicant_repo.delete(applicant_id)
        logger.info("ApplicantService.delete_applicant applicant_id=%s", applicant_id)

    def generate_quote(self, applicant_id: str) -> Dict[str, Any]:
        applicant = self.applicant_repo.get_by_id(applicant_id)
        if not applicant:
            raise ValueError(f"Applicant not found: {applicant_id}")
        active = self.policy_repo.get_active_policy(applicant_id)
        if active:
            logger.info("ApplicantService.generate_quote existing policy_id=%s", active.policy_id)
            return {"applicant_id": applicant_id, "policy": active.to_response()}
        logger.info("ApplicantService.generate_quote no active policy applicant_id=%s", applicant_id)
        return {"applicant_id": applicant_id, "policy": None, "message": "No active quote — submit intake to initiate underwriting"}

    # ── Policy operations ──────────────────────────────────────────────────────

    def create_policy(
        self,
        applicant_id: str,
        policy_type: str,
        coverage_amount: Optional[str] = None,
        deductible: Optional[str] = None,
        declared_value: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.applicant_repo.get_by_id(applicant_id):
            raise ValueError(f"Applicant not found: {applicant_id}")
        policy_id = generate_policy_id()
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        policy = InsurancePolicy(
            policy_id=policy_id,
            applicant_id=applicant_id,
            policy_type=PolicyType(policy_type.upper()),
            coverage_amount=coverage_amount,
            deductible=deductible,
            declared_value=declared_value,
            status=PolicyStatus.DRAFT,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        self.policy_repo.create(policy)
        logger.info("ApplicantService.create_policy policy_id=%s applicant_id=%s", policy_id, applicant_id)
        return {"policy_id": policy_id, "applicant_id": applicant_id, "status": PolicyStatus.DRAFT.value}

    def get_active_policy(self, applicant_id: str) -> Optional[Dict[str, Any]]:
        policy = self.policy_repo.get_active_policy(applicant_id)
        logger.info("ApplicantService.get_active_policy applicant_id=%s found=%s", applicant_id, policy is not None)
        return policy.to_response() if policy else None

    def get_policy_history(self, applicant_id: str) -> List[Dict[str, Any]]:
        policies = self.policy_repo.get_history(applicant_id)
        logger.info("ApplicantService.get_policy_history applicant_id=%s count=%d", applicant_id, len(policies))
        return [p.to_response() for p in policies]

    def bind_policy(self, applicant_id: str, policy_id: str) -> None:
        policy = self.policy_repo.get_by_id(policy_id)
        if not policy or policy.applicant_id != applicant_id:
            raise ValueError(f"Policy {policy_id} not found for applicant {applicant_id}")
        policy.status = PolicyStatus.BOUND
        self.policy_repo.update(policy)
        logger.info("ApplicantService.bind_policy policy_id=%s applicant_id=%s", policy_id, applicant_id)

    def cancel_policy(self, applicant_id: str) -> None:
        policy = self.policy_repo.get_active_policy(applicant_id)
        if not policy:
            raise ValueError(f"No active policy for applicant {applicant_id}")
        policy.status = PolicyStatus.CANCELLED
        self.policy_repo.update(policy)
        logger.info("ApplicantService.cancel_policy applicant_id=%s policy_id=%s", applicant_id, policy.policy_id)

    def isar_explain_applicant(self, applicant_id: str) -> str:
        applicant = self.applicant_repo.get_by_id(applicant_id)
        if not applicant:
            return f"Applicant {applicant_id} not found."
        policy = self.policy_repo.get_active_policy(applicant_id)
        lines = [f"applicant_id: {applicant_id}"]
        for attr in ("policy_type", "risk_tier", "status"):
            v = getattr(applicant, attr, None)
            if v:
                lines.append(f"{attr}: {v.value if hasattr(v, 'value') else v}")
        contact = getattr(applicant, "contact_info", None)
        if contact:
            if getattr(contact, "full_name", None):  lines.append(f"applicant_name: {contact.full_name}")
            if getattr(contact, "email", None):       lines.append(f"email: {contact.email}")
            if getattr(contact, "phone", None):       lines.append(f"phone: {contact.phone}")
        loc = getattr(applicant, "location_address", None)
        if loc:
            parts = [getattr(loc, f, None) for f in ("city", "state", "zip_code")]
            loc_str = ", ".join(p for p in parts if p)
            if loc_str: lines.append(f"location: {loc_str}")
        cov = getattr(applicant, "coverage_request", None)
        if cov:
            if getattr(cov, "coverage_amount", None):  lines.append(f"coverage_amount: {cov.coverage_amount}")
            if getattr(cov, "deductible", None):        lines.append(f"deductible: {cov.deductible}")
        if policy:
            lines.append(f"policy_id: {getattr(policy, 'policy_id', '')}")
            lines.append(f"policy_status: {getattr(policy, 'status', '')}")
        logger.info("ApplicantService.isar_explain_applicant applicant_id=%s", applicant_id)
        return "\n".join(lines)

    def get_high_risk_applicants(self) -> Dict[str, Any]:
        tiers = [RiskTier.HIGH_RISK.value, RiskTier.DECLINED.value]
        applicants = self.applicant_repo.list_by_risk_tiers(tiers)
        result = [
            {
                "applicant_id":  a.applicant_id,
                "applicant_name": a.applicant_name,
                "policy_type":   a.policy_type.value if a.policy_type else None,
                "risk_tier":     a.risk_tier.value if a.risk_tier else None,
            }
            for a in applicants
        ]
        logger.info("ApplicantService.get_high_risk_applicants count=%d", len(result))
        return {"total": len(result), "applicants": result}
