import re

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mltek.common.log import setup_structured_logging

from workflow_engine.repository import ApplicantJobRepository
from workflow_engine.service.workflow_endpoints import (
    create_workflow_job_request,
    get_applicant_by_id,
    get_workflow,
    get_workflow_request,
    get_workflow_request_by_workflow_id,
    receive_webhook,
    resume_orchestrator_step,
    schedule_mock_gate_check,
    submit_human_review,
)
from workflow_engine.service.applicant_workflow import (
    initiate_workflow as initiate_applicant_workflow,
)

logger = setup_structured_logging(__name__)

_job_repo = ApplicantJobRepository()


def _normalize_identifier(value: str | None) -> str | None:
    if not isinstance(value, str):
        return value
    return value.strip().strip("/")


async def initiate_workflow_endpoint(request: Request) -> JSONResponse:
    """POST /workflow/executions/{applicant_id}"""
    applicant_id = request.path_params["applicant_id"]
    try:
        body = await request.json()
    except Exception:
        body = {}
    correlation_id = request.headers.get("X-Correlation-ID") or body.get("correlation_id") or None
    result = await initiate_applicant_workflow(applicant_id, context=body, correlation_id=correlation_id)
    return JSONResponse(result, status_code=202)


async def webhook_endpoint(request: Request) -> JSONResponse:
    """POST /webhooks/{gate_check}"""
    gate_check = request.path_params["gate_check"]
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    ref_id = payload.get("reference_id")
    if not ref_id:
        return JSONResponse({"error": "missing reference_id in payload"}, status_code=400)

    try:
        result = await receive_webhook(gate_check, payload)
    except ValueError:
        return JSONResponse({"error": "missing reference_id"}, status_code=400)
    return JSONResponse(result, status_code=202)


async def human_review_endpoint(request: Request) -> JSONResponse:
    """POST /workflow/{workflow_id}/review

    Body: {"decision": "APPROVE" | "REJECT", "notes": "optional free text"}

    Resumes a graph suspended at the human_review interrupt node.
    Only applicable when the LLM underwriting decision returned ESCALATE.
    """
    workflow_id = _normalize_identifier(request.path_params["workflow_id"])
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    decision = body.get("decision")
    if decision not in ("APPROVE", "REJECT"):
        return JSONResponse(
            {"error": "decision must be 'APPROVE' or 'REJECT'"}, status_code=400
        )

    result = await submit_human_review(
        workflow_id=workflow_id,
        decision=decision,
        notes=body.get("notes", ""),
    )
    return JSONResponse(result, status_code=200)


async def get_workflow_endpoint(request: Request) -> JSONResponse:
    """GET /workflow/{workflow_id}"""
    workflow_id = _normalize_identifier(request.path_params["workflow_id"])
    workflow = get_workflow(workflow_id)
    if not workflow:
        return JSONResponse({"error": "workflow not found"}, status_code=404)
    return JSONResponse(workflow)


async def mock_gate_check_endpoint(request: Request) -> JSONResponse:
    """POST /mock/gate-check/{gate_check}

    Local auto-approving stub for gate-check providers.
    Active only when BFF_SERVICE_URL is configured. Never deploy in production.
    """
    gate_check = request.path_params["gate_check"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    try:
        result = await schedule_mock_gate_check(gate_check, body)
    except ValueError:
        return JSONResponse({"error": "missing reference_id"}, status_code=400)
    return JSONResponse(result, status_code=202)


async def post_applicant(request: Request) -> JSONResponse:
    """POST /workflow/applicant — starts applicant intake workflow in background, returns applicant_id."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    result = await create_workflow_job_request(body, _job_repo)
    return JSONResponse(result, status_code=202)


def _extract_identifier_from_text(text: str) -> str | None:
    text = text.strip()
    if not text:
        return None

    # Request ID: req_xxxxxxxxxxxxxxxx
    match = re.search(r"req_[0-9a-f]{16}", text, re.IGNORECASE)
    if match:
        return match.group(0)

    # Applicant ID: UUID pattern
    match = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text, re.IGNORECASE)
    if match:
        return match.group(0)

    # Workflow ID: wf_xxxxxx
    match = re.search(r"wf_[0-9a-f]{12}", text, re.IGNORECASE)
    if match:
        return match.group(0)

    # Applicant ID: applicant_id or applicant id labels
    match = re.search(r"applicant[_\s]?id\s*[:=]?\s*([A-Za-z0-9_-]+)", text, re.IGNORECASE)
    if match:
        return match.group(1)

    # If the whole text is a single token, use it directly.
    if " " not in text and "\n" not in text:
        return text

    return None


async def get_workflow_request_endpoint(request: Request) -> JSONResponse:
    """GET /workflow/applicant/request/{applicant_id} — poll for background job result.

    The identifier may be a request_id, applicant_id, or workflow_id.
    """
    raw_identifier = request.path_params["applicant_id"]
    identifier = _extract_identifier_from_text(raw_identifier) or raw_identifier
    identifier = _normalize_identifier(identifier)
    job = get_workflow_request(identifier, _job_repo)
    if job is None:
        job = get_applicant_by_id(identifier, _job_repo)
    if job is None:
        job = get_workflow_request_by_workflow_id(identifier, _job_repo)

    if job is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(job)


async def get_applicant_endpoint(request: Request) -> JSONResponse:
    """GET /workflow/applicant/{applicant_id} — look up job record by applicant_id."""
    applicant_id = request.path_params["applicant_id"]
    job = get_applicant_by_id(applicant_id, _job_repo)
    if job is None:
        return JSONResponse({"error": "applicant not found"}, status_code=404)
    return JSONResponse(job)


async def orchestrator_resume_endpoint(request: Request) -> JSONResponse:
    """POST /webhooks/orchestrator/{any_id}/resume  or  GET ?decision=retry|skip

    Resume a HITL-suspended OrchestratorGraph job.
    ``any_id`` may be an applicant_id (UUID), request_id (req_xxx), or workflow_id (wf_xxx).

    POST body: {"decision": "retry"|"skip", "notes": "optional"}
    GET query: ?decision=retry|skip
    """
    any_id = _normalize_identifier(request.path_params["any_id"])

    if request.method == "GET":
        decision = request.query_params.get("decision", "retry")
        notes = request.query_params.get("notes", "")
    else:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        decision = body.get("decision")
        notes = body.get("notes", "")

    if decision not in ("retry", "skip"):
        return JSONResponse({"error": "decision must be 'retry' or 'skip'"}, status_code=400)

    try:
        result = await resume_orchestrator_step(any_id, decision, notes=notes)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)

    if "error" in result:
        return JSONResponse(result, status_code=400)
    return JSONResponse(result, status_code=202)


WORKFLOW_ROUTES = [
    Route("/workflow/executions/{applicant_id}", initiate_workflow_endpoint, methods=["POST"]),
    Route("/workflow/applicant/request/{applicant_id}", get_workflow_request_endpoint, methods=["GET"]),
    Route("/workflow/applicant/{applicant_id}", get_applicant_endpoint, methods=["GET"]),
    Route("/workflow/applicant", post_applicant, methods=["POST"]),
    Route("/workflow/{workflow_id}", get_workflow_endpoint, methods=["GET"]),
    Route("/workflow/{workflow_id}/review", human_review_endpoint, methods=["POST"]),
    Route("/webhooks/orchestrator/{any_id}/resume", orchestrator_resume_endpoint, methods=["GET", "POST"]),
    Route("/webhooks/{gate_check}", webhook_endpoint, methods=["POST"]),
    Route("/mock/gate-check/{gate_check}", mock_gate_check_endpoint, methods=["POST"]),
]

