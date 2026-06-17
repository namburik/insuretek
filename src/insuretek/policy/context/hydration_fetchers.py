"""InsureTek domain entity fetchers for LiveHydrationPolicy."""

from __future__ import annotations

from mltek.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)


def fetch_applicant_context(applicant_id: str) -> str:
    try:
        from core_data.service.applicant_service import ApplicantService
        from mltek.common.tools.format import format_json_as_text
        svc = ApplicantService()
        data = svc.get_applicant(applicant_id).get("applicant", {})
        policy = svc.get_active_policy(applicant_id)
        if policy:
            data["active_policy"] = policy
        return format_json_as_text(data)
    except Exception:
        logger.warning("fetch_applicant_context failed applicant_id=%s", applicant_id, exc_info=True)
        return ""


def fetch_workflow_context(workflow_id: str) -> str:
    try:
        from workflow_engine.service.workflow_endpoints import get_workflow
        from mltek.common.tools.format import format_json_as_text
        data = get_workflow(workflow_id)
        if not data:
            return ""
        return format_json_as_text(data)
    except Exception:
        logger.warning("fetch_workflow_context failed workflow_id=%s", workflow_id, exc_info=True)
        return ""


INSURETEK_FETCHERS: dict[str, object] = {
    "applicant": fetch_applicant_context,
    "workflow":  fetch_workflow_context,
}
