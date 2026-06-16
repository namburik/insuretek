"""Insurance policy lifecycle tool utilities."""

from typing import Any, Dict, Optional

from shared.common.log.logging_config import setup_structured_logging
from shared.common.infra.error_helpers import handle_tool_errors as _handle_tool_errors

logger = setup_structured_logging(__name__)


class PolicyHandlers:
    def __init__(self, service):
        self.service = service

    @_handle_tool_errors()
    def create_policy(
        self,
        applicant_id: str,
        policy_type: str,
        coverage_amount: Optional[str] = None,
        deductible: Optional[str] = None,
        declared_value: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Create or replace the active insurance policy for an applicant."""
        logger.info("create_policy tool invoked", extra_fields={"applicant_id": applicant_id, "policy_type": policy_type})
        if not applicant_id:
            raise ValueError("applicant_id is required")
        if not policy_type:
            raise ValueError("policy_type is required")
        policy = self.service.create_policy(
            applicant_id=applicant_id,
            policy_type=policy_type,
            coverage_amount=coverage_amount,
            deductible=deductible,
            declared_value=declared_value,
            metadata=metadata,
        )
        logger.info("create_policy tool completed", extra_fields={"policy_id": policy.get("policy_id"), "applicant_id": applicant_id})
        return policy

    @_handle_tool_errors()
    def get_active_policy(self, applicant_id: str) -> dict:
        """Retrieve the current active policy for an applicant."""
        if not applicant_id:
            raise ValueError("applicant_id is required")
        logger.debug("get_active_policy tool invoked", extra_fields={"applicant_id": applicant_id})
        policy = self.service.get_active_policy(applicant_id)
        if not policy:
            logger.debug("get_active_policy: no active policy found", extra_fields={"applicant_id": applicant_id})
            return {"applicant_id": applicant_id, "policy": None, "message": "No active policy found"}
        logger.debug("get_active_policy: active policy found", extra_fields={"applicant_id": applicant_id})
        return {"applicant_id": applicant_id, "policy": policy}

    @_handle_tool_errors()
    def get_policy_history(self, applicant_id: str) -> dict:
        """List all historical policy records for an applicant, newest first."""
        if not applicant_id:
            raise ValueError("applicant_id is required")
        logger.debug("get_policy_history tool invoked", extra_fields={"applicant_id": applicant_id})
        history = self.service.get_policy_history(applicant_id)
        logger.debug("get_policy_history tool completed", extra_fields={"applicant_id": applicant_id, "count": len(history)})
        return {"applicant_id": applicant_id, "count": len(history), "policy_history": history}

    @_handle_tool_errors()
    def bind_policy(self, applicant_id: str, policy_id: str) -> dict:
        """Bind (issue) a quoted policy, transitioning it to BOUND status."""
        if not applicant_id or not policy_id:
            raise ValueError("applicant_id and policy_id are required")
        logger.info("bind_policy tool invoked", extra_fields={"applicant_id": applicant_id, "policy_id": policy_id})
        self.service.bind_policy(applicant_id, policy_id)
        logger.info("bind_policy tool completed", extra_fields={"applicant_id": applicant_id, "policy_id": policy_id})
        return {"applicant_id": applicant_id, "policy_id": policy_id, "status": "bound"}

    @_handle_tool_errors()
    def cancel_policy(self, applicant_id: str) -> dict:
        """Cancel the active policy for an applicant."""
        if not applicant_id:
            raise ValueError("applicant_id is required")
        logger.info("cancel_policy tool invoked", extra_fields={"applicant_id": applicant_id})
        self.service.cancel_policy(applicant_id)
        logger.info("cancel_policy tool completed", extra_fields={"applicant_id": applicant_id})
        return {"applicant_id": applicant_id, "status": "cancelled"}
