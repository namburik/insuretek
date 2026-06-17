from typing import Any, Dict

import httpx

from insuretek.config.config import InsureTekConfig
from mltek.common.log import setup_structured_logging
from workflow_engine.domain.workflow_domain import generate_gate_check_ref_id

logger = setup_structured_logging(__name__)


def make_ref_id(gate_check: str) -> str:
    return generate_gate_check_ref_id(gate_check)


async def dispatch_gate_check(
    gate_check: str,
    workflow_id: str,
    applicant_id: str,
    endpoint_url: str,
    payload: Dict[str, Any],
    ref_id: str,
    timeout: float = 10.0,
) -> None:
    """POST a gate-check request to the external provider."""
    callback_url = f"{InsureTekConfig.BFF_SERVICE_URL}/webhooks/{gate_check}"
    body = {
        **payload,
        "reference_id": ref_id,
        "applicant_id": applicant_id,
        "workflow_id": workflow_id,
        "callback_url": callback_url,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(endpoint_url, json=body)
        response.raise_for_status()
    logger.info("Gate check dispatched: gate_check=%s ref_id=%s status=%d endpoint=%s", gate_check, ref_id, response.status_code, endpoint_url, extra={"gate_check": gate_check, "reference_id": ref_id, "status_code": response.status_code, "endpoint": endpoint_url})
