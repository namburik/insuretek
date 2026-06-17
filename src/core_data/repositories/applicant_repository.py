"""Applicant and insurance policy repositories."""

from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Attr

from mltek.config.config_common import Config
from mltek.common.infra.error_helpers import handle_repo_errors
from mltek.common.log.logging_config import setup_structured_logging
from core_data.repositories.base_repository import BaseRepository
from core_data.db.models.models_applicant import Applicant, InsurancePolicy
from core_data.db.factory import get_applicants_table_operations, get_policies_table_operations
from core_data.domain.enums import PolicyStatus

logger = setup_structured_logging(__name__)


class ApplicantRepository(BaseRepository[Applicant]):

    def __init__(self):
        self.applicants = get_applicants_table_operations()
        logger.debug("ApplicantRepository initialized DB_TYPE=%s", Config.DB_TYPE)

    @handle_repo_errors
    def create(self, applicant: Applicant) -> str:
        item = applicant.to_backend_item()
        try:
            if Config.is_using_dynamodb():
                self.applicants.put_item(item, condition=Attr("applicant_id").not_exists())
            else:
                self.applicants.put_item(item)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                logger.warning("Applicant already exists — idempotent create applicant_id=%s", applicant.applicant_id)
                return applicant.applicant_id
            raise
        logger.info("Applicant created applicant_id=%s", applicant.applicant_id)
        return applicant.applicant_id

    @handle_repo_errors
    def get_by_id(self, applicant_id: str) -> Optional[Applicant]:
        item = self.applicants.get_by_id("applicant_id", applicant_id)
        if item:
            logger.debug("Applicant fetched applicant_id=%s", applicant_id)
            return Applicant.from_backend_item(item)
        logger.info("Applicant not found applicant_id=%s", applicant_id)
        return None

    @handle_repo_errors
    def update(self, applicant: Applicant) -> None:
        applicant.updated_at = int(datetime.now(timezone.utc).timestamp() * 1000)
        self.applicants.put_item(applicant.to_backend_item())
        logger.info("Applicant updated applicant_id=%s", applicant.applicant_id)

    @handle_repo_errors
    def partial_update(self, applicant_id: str, fields: Dict[str, Any]) -> None:
        self.applicants.update_item({"applicant_id": applicant_id}, fields)
        logger.info("Applicant partial-updated applicant_id=%s fields=%s", applicant_id, list(fields))

    @handle_repo_errors
    def delete(self, applicant_id: str) -> None:
        applicant = self.get_by_id(applicant_id)
        if applicant:
            applicant.metadata["deleted"] = True
            applicant.metadata["deleted_at"] = int(datetime.now(timezone.utc).timestamp() * 1000)
            self.update(applicant)
            logger.info("Applicant soft-deleted applicant_id=%s", applicant_id)

    @handle_repo_errors
    def list_all(self, limit: int = 10, before_created_at: Optional[int] = None) -> List[Applicant]:
        if Config.is_using_dynamodb():
            if before_created_at is not None:
                items = self.applicants.scan(
                    filter_expression=Attr("created_at").lt(before_created_at),
                    limit=limit,
                )
            else:
                items = self.applicants.scan(limit=limit)
        else:
            items = self.applicants.scan(limit=limit)
        logger.info("Applicants listed count=%d", len(items))
        return [Applicant.from_backend_item(i) for i in items]

    @handle_repo_errors
    def search(self, **filters) -> List[Applicant]:
        policy_type = filters.get("policy_type")
        if policy_type:
            items = self.applicants.scan(
                filter_expression=Attr("policy_type").eq(policy_type)
            )
            logger.info("Applicants searched by policy_type=%s count=%d", policy_type, len(items))
            return [Applicant.from_backend_item(i) for i in items]
        return self.list_all()

    @handle_repo_errors
    def list_by_risk_tiers(self, risk_tiers: List[str], limit: int = 50) -> List[Applicant]:
        filter_expr = None
        for tier in risk_tiers:
            cond = Attr("risk_tier").eq(tier)
            filter_expr = cond if filter_expr is None else filter_expr | cond
        items = self.applicants.scan(filter_expression=filter_expr, limit=limit)
        logger.info("Applicants listed by risk_tiers=%s count=%d", risk_tiers, len(items))
        return [Applicant.from_backend_item(i) for i in items]

    @handle_repo_errors
    def list_by_enterprise_identifier(self, enterprise_id: str) -> List[Applicant]:
        items = self.applicants.scan(
            filter_expression=Attr("enterprise_identifier").eq(enterprise_id)
        )
        logger.info("Applicants listed by enterprise_identifier=%s count=%d", enterprise_id, len(items))
        return [Applicant.from_backend_item(i) for i in items]


class PolicyRepository(BaseRepository[InsurancePolicy]):

    def __init__(self):
        self.policies = get_policies_table_operations()
        logger.debug("PolicyRepository initialized DB_TYPE=%s", Config.DB_TYPE)

    @handle_repo_errors
    def create(self, policy: InsurancePolicy) -> str:
        self.policies.put_item(policy.to_backend_item())
        logger.info("Policy created policy_id=%s applicant_id=%s", policy.policy_id, policy.applicant_id)
        return policy.policy_id

    @handle_repo_errors
    def get_by_id(self, policy_id: str) -> Optional[InsurancePolicy]:
        item = self.policies.get_by_id("policy_id", policy_id)
        if item:
            return InsurancePolicy.from_backend_item(item)
        return None

    @handle_repo_errors
    def update(self, policy: InsurancePolicy) -> None:
        policy.updated_at = int(datetime.now(timezone.utc).timestamp() * 1000)
        self.policies.put_item(policy.to_backend_item())
        logger.info("Policy updated policy_id=%s", policy.policy_id)

    @handle_repo_errors
    def delete(self, policy_id: str) -> None:
        policy = self.get_by_id(policy_id)
        if policy:
            policy.status = PolicyStatus.CANCELLED
            self.update(policy)
            logger.info("Policy cancelled policy_id=%s", policy_id)

    @handle_repo_errors
    def list_all(self, limit: int = 100, before_created_at: Optional[int] = None) -> List[InsurancePolicy]:
        items = self.policies.scan(limit=limit)
        return [InsurancePolicy.from_backend_item(i) for i in items]

    @handle_repo_errors
    def search(self, **filters) -> List[InsurancePolicy]:
        applicant_id = filters.get("applicant_id")
        if applicant_id:
            items = self.policies.scan(
                filter_expression=Attr("applicant_id").eq(applicant_id)
            )
            return [InsurancePolicy.from_backend_item(i) for i in items]
        return self.list_all()

    @handle_repo_errors
    def get_active_policy(self, applicant_id: str) -> Optional[InsurancePolicy]:
        active_statuses = {PolicyStatus.QUOTED.value, PolicyStatus.BOUND.value, PolicyStatus.ACTIVE.value}
        items = self.policies.scan(
            filter_expression=Attr("applicant_id").eq(applicant_id)
        )
        policies = [InsurancePolicy.from_backend_item(i) for i in items]
        active = [p for p in policies if (p.status.value if hasattr(p.status, "value") else p.status) in active_statuses]
        if not active:
            return None
        return sorted(active, key=lambda p: p.created_at, reverse=True)[0]

    @handle_repo_errors
    def get_history(self, applicant_id: str) -> List[InsurancePolicy]:
        items = self.policies.scan(
            filter_expression=Attr("applicant_id").eq(applicant_id)
        )
        policies = [InsurancePolicy.from_backend_item(i) for i in items]
        logger.info("Policy history fetched applicant_id=%s count=%d", applicant_id, len(policies))
        return sorted(policies, key=lambda p: p.created_at, reverse=True)
