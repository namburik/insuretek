"""Insurance applicant CRUD tool utilities."""

from typing import Optional

from mltek.common.log.logging_config import setup_structured_logging
from mltek.common.infra.error_helpers import handle_tool_errors as _handle_tool_errors

logger = setup_structured_logging(__name__)


class ApplicantHandlers:
    def __init__(self, service):
        self.service = service

    @_handle_tool_errors("Failed to save applicant")
    def create_applicant(self, arguments):
        """Create a new insurance applicant and initiate underwriting intake."""
        logger.info("create_applicant tool invoked", extra_fields={"received_arguments": list(arguments.keys())})
        applicant_json = arguments.get("applicant_json")
        if not isinstance(applicant_json, dict) or not applicant_json:
            raise ValueError("applicant_json must be a non-empty object")
        result = self.service.create_applicant(applicant_json)
        logger.info("create_applicant tool completed", extra_fields={"applicant_id": result.get("applicant_id")})
        return {
            "status": "created",
            "applicant_id": result["applicant_id"],
            "applicant_name": result["applicant"].get("applicant_name", ""),
        }

    @_handle_tool_errors()
    def get_applicant(self, applicant_id: str):
        """Retrieve an insurance applicant by ID."""
        if not applicant_id or not isinstance(applicant_id, str):
            raise ValueError("applicant_id must be a non-empty string")
        logger.debug(f"Retrieving applicant: {applicant_id}")
        result = self.service.get_applicant(applicant_id)
        logger.debug(f"get_applicant: applicant_id={applicant_id} found")
        return {"applicant": result["applicant"]}

    @_handle_tool_errors()
    def search_applicants_by_policy_type(self, policy_type: str):
        """Search applicants by policy type (HOME | AUTO | COMMERCIAL | LIFE)."""
        if not policy_type or not isinstance(policy_type, str):
            raise ValueError("policy_type must be a non-empty string")
        logger.info(f"Searching applicants by policy_type: {policy_type}")
        applicants = self.service.search_applicants(policy_type=policy_type)
        logger.info(f"Found {len(applicants)} applicants of type {policy_type}")
        return {
            "policy_type": policy_type,
            "count": len(applicants),
            "applicants": applicants,
        }

    @_handle_tool_errors()
    def get_all_applicants(self, before_created_at: Optional[int] = None):
        """Get all applicants with their current underwriting status."""
        logger.debug("Retrieving all applicants")
        all_applicants = self.service.get_all_applicants(limit=10, before_created_at=before_created_at)
        result = [
            {
                "applicant_id": a["applicant"].get("applicant_id", ""),
                "applicant_name": a["applicant"].get("applicant_name", ""),
                "policy_type": a["applicant"].get("policy_type"),
                "risk_tier": a["applicant"].get("risk_tier"),
            }
            for a in all_applicants
        ]
        logger.debug(f"Retrieved {len(result)} applicants")
        return {"total": len(result), "applicants": result}

    @_handle_tool_errors()
    def update_applicant(self, applicant_id: str, **patch_fields):
        """Apply a partial update to an existing applicant record."""
        logger.info("update_applicant tool invoked", extra_fields={"applicant_id": applicant_id, "patch_fields": list(patch_fields.keys())})
        patch_fields.pop("applicant_id", None)
        normalized = {k: v for k, v in patch_fields.items() if v is not None}
        if not normalized:
            return {"success": False, "applicant_id": applicant_id, "updated_fields": [], "error": "No non-null fields provided."}
        result = self.service.update_applicant(applicant_id, **normalized)
        logger.info("update_applicant tool completed", extra_fields={"applicant_id": applicant_id, "updated_fields": result.get("updated_fields", [])})
        return result

    @_handle_tool_errors()
    def quote_applicant(self, applicant_id: str):
        """Generate a premium quote for an applicant based on their risk profile."""
        if not applicant_id or not isinstance(applicant_id, str):
            raise ValueError("applicant_id must be a non-empty string")
        logger.info(f"Quoting applicant: {applicant_id}")
        result = self.service.generate_quote(applicant_id)
        logger.info(f"quote_applicant completed: applicant_id={applicant_id}")
        return result

    @_handle_tool_errors()
    def get_high_risk_applicants(self):
        """Return applicants with HIGH_RISK or DECLINED risk tier."""
        return self.service.get_high_risk_applicants()

    @_handle_tool_errors()
    def delete_applicant(self, applicant_id: str):
        """Soft-delete an applicant record."""
        logger.info(f"Deleting applicant: {applicant_id}")
        self.service.delete_applicant(applicant_id)
        logger.info(f"Applicant deleted successfully: {applicant_id}")
        return {"success": True, "message": f"Applicant {applicant_id} deleted successfully"}
