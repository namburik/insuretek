"""InsureTek HTTP client for Applicant Workflow API."""

import httpx

from mltek.config import config
from mltek.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)


async def submit_workflow_request(draft: dict, prompt: str, env: str = "prod", session_id: str = "", correlation_id: str = "") -> tuple[str, str | None]:
    """POST /workflow/applicant. Returns (request_id, applicant_id | None)."""
    base_url = config.BFF_SERVICE_URL
    if not base_url:
        raise RuntimeError("BFF_SERVICE_URL must be configured")
    url = f"{base_url.rstrip('/')}/workflow/applicant"
    logger.info("Submitting applicant draft endpoint=%s draft_keys=%s env=%s correlation_id=%s", url, list(draft.keys()), env, correlation_id)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            json={"prompt": prompt, "applicant_json": draft, "env": env, "session_id": session_id, "correlation_id": correlation_id},
            headers={"X-Correlation-ID": correlation_id} if correlation_id else {},
        )
        response.raise_for_status()
        body = response.json()
    request_id = body.get("request_id", "")
    if not request_id:
        raise RuntimeError(f"No request_id returned from {url}: {body}")
    applicant_id = body.get("applicant_id")
    logger.info("submit_workflow_request exit request_id=%s applicant_id=%s", request_id, applicant_id)
    return request_id, applicant_id


async def poll_applicant_id(request_id: str) -> str | None:
    """GET /workflow/applicant/request/{request_id}. Returns applicant_id if available."""
    base_url = config.BFF_SERVICE_URL
    if not base_url:
        return None
    url = f"{base_url.rstrip('/')}/workflow/applicant/request/{request_id}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json().get("applicant_id")
    except Exception:
        return None


__all__ = ["submit_workflow_request", "poll_applicant_id"]
