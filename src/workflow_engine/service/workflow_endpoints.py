import asyncio

from shared.common.infra.context import CorrelationContext, set_request_id, set_audit_session_id
from shared.common.log import setup_structured_logging
from workflow_engine.domain.workflow_domain import generate_request_id
from workflow_engine.repository import ApplicantJobRepository, WorkflowRepository
from workflow_engine.service.applicant_workflow import (
    initiate_workflow as initiate_workflow_service,
    receive_webhook as receive_webhook_service,
    submit_human_decision as submit_human_decision_service,
    run_applicant_orchestrator,
    run_applicant_orchestrator_graph ,
    resume_orchestrator as resume_orchestrator_service,
)

logger = setup_structured_logging(__name__)

_background_tasks: set = set()


def _log_task_error(t: asyncio.Task) -> None:
    if not t.cancelled() and t.exception():
        logger.error("Background task %s failed: %s", t.get_name(), t.exception())
    _background_tasks.discard(t)

_MOCK_APPROVAL_DELAY: dict = {
    "identity":          0.5,
    "bank_verification": 2.0,
    "underwriting":      3.5,
    "logistics":         5.0,
    "fraud":             1.5,
}


async def initiate_workflow(applicant_id: str, context: dict, correlation_id: str | None = None) -> dict:
    return await initiate_workflow_service(applicant_id, context=context, correlation_id=correlation_id)


async def receive_webhook(gate_check: str, payload: dict) -> dict:
    ref_id = payload.get("reference_id")
    if not ref_id:
        raise ValueError("missing reference_id")

    async def _process():
        try:
            await receive_webhook_service(
                reference_id=ref_id,
                gate_check=gate_check,
                payload=payload,
            )
        except Exception as exc:
            logger.exception(
                "Webhook processing failed: %s",
                exc,
                extra={"reference_id": ref_id},
            )

    task = asyncio.create_task(_process())
    task.add_done_callback(_log_task_error)
    _background_tasks.add(task)
    return {"status": "accepted", "reference_id": ref_id}


def get_workflow(workflow_id: str, repo: WorkflowRepository | None = None) -> dict | None:
    workflow_id = workflow_id.strip().strip("/") if isinstance(workflow_id, str) else workflow_id
    repository = repo or WorkflowRepository()
    workflow = repository.get_by_workflow_id(workflow_id)
    if workflow:
        workflow_json = workflow.model_dump(
            mode="json",
            include={
                "workflow_id",
                "applicant_id",
                "current_state",
                "is_final_approval_granted",
                "approved_by",
                "created_at",
                "updated_at",
                "completed_at",
                "approval_tasks",
            },
        )
        task_items = workflow_json.get("approval_tasks", [])
        workflow_json["approval_tasks"] = [
            {
                "task_id": task.get("task_id"),
                "gate_check": task.get("gate_check"),
                "status": task.get("status"),
                "is_essential": task.get("is_essential"),
                "completed_at": task.get("completed_at"),
            }
            for task in task_items
        ]
        logger.debug(workflow_json)
        return workflow_json
    return None


def get_workflow_by_applicant_id(applicant_id: str, repo: WorkflowRepository | None = None) -> dict | None:
    applicant_id = applicant_id.strip().strip("/") if isinstance(applicant_id, str) else applicant_id
    repository = repo or WorkflowRepository()
    active = repository.get_active_by_applicant_id(applicant_id)
    if active is None:
        logger.info("get_workflow_by_applicant_id: applicant_id=%r no active workflow found", applicant_id)
        return None
    logger.info("get_workflow_by_applicant_id: applicant_id=%r resolved workflow_id=%s", applicant_id, active.workflow_id)
    return get_workflow(active.workflow_id, repository)



async def submit_human_review(workflow_id: str, decision: str, notes: str) -> dict:
    return await submit_human_decision_service(
        workflow_id=workflow_id,
        decision=decision,
        notes=notes,
    )


async def schedule_mock_gate_check(gate_check: str, payload: dict) -> dict:
    ref_id = payload.get("reference_id")
    if not ref_id:
        raise ValueError("missing reference_id")

    delay = _MOCK_APPROVAL_DELAY.get(gate_check, 1.0)

    async def _auto_approve() -> None:
        await asyncio.sleep(delay)
        try:
            await receive_webhook_service(
                reference_id=ref_id,
                gate_check=gate_check,
                payload={"approved": True, "status": "APPROVED", "reference_id": ref_id},
            )
            logger.info("Mock auto-approved %s ref=%s", gate_check, ref_id)
        except Exception as exc:
            logger.error("Mock auto-approval failed for %s: %s", gate_check, exc)

    task = asyncio.create_task(_auto_approve())
    task.add_done_callback(_log_task_error)
    _background_tasks.add(task)
    return {"status": "accepted", "reference_id": ref_id}


async def create_workflow_job_request(body: dict, job_repo: ApplicantJobRepository) -> dict:
    from shared.config import config as app_config
    request_id = generate_request_id()
    set_request_id(request_id)
    session_id = body.get("session_id", "")
    # Middleware already set context from X-Correlation-ID header; body value overrides for legacy callers.
    correlation_id = body.get("correlation_id") or CorrelationContext.get_or_generate()
    CorrelationContext.set(correlation_id)
    if session_id:
        set_audit_session_id(session_id)
    await asyncio.to_thread(job_repo.create, request_id)

    prompt = body.get("prompt")
    applicant_json = body.get("applicant_json")
    env = body.get("env", "prod")
    if env not in ("prod", "shadow"):
        env = "prod"

    run_fn = run_applicant_orchestrator_graph if app_config.USE_ORCHESTRATOR_GRAPH else run_applicant_orchestrator
    t = asyncio.create_task(
        run_fn(
            request_id,
            prompt=prompt,
            applicant_json=applicant_json,
            job_repo=job_repo,
            env=env,
            session_id=session_id or None,
            correlation_id=correlation_id,
        )
    )
    t.add_done_callback(_log_task_error)
    _background_tasks.add(t)

    return {"request_id": request_id, "status": "pending"}


def get_workflow_request(request_id: str, job_repo: ApplicantJobRepository) -> dict | None:
    return job_repo.get(request_id)


def get_workflow_request_by_workflow_id(workflow_id: str, job_repo: ApplicantJobRepository) -> dict | None:
    return job_repo.get_by_workflow_id(workflow_id)


def get_applicant_by_id(applicant_id: str, job_repo: ApplicantJobRepository) -> dict | None:
    return job_repo.get_by_applicant_id(applicant_id)


def _resolve_orchestrator_request_id(any_id: str, job_repo: ApplicantJobRepository) -> str | None:
    """Resolve any_id to an orchestrator request_id.

    Tries: direct request_id → applicant_id → approval workflow_id (via applicant_id lookup).
    """
    job = job_repo.get(any_id)
    if job:
        return any_id

    job = job_repo.get_by_applicant_id(any_id)
    if job:
        return job.get("request_id")

    # Try resolving as approval workflow_id → fetch applicant_id → orchestrator job
    wf = get_workflow(any_id)
    if wf:
        app_id = wf.get("applicant_id")
        if app_id:
            job = job_repo.get_by_applicant_id(app_id)
            if job:
                return job.get("request_id")

    return None


async def resume_orchestrator_step(any_id: str, decision: str, notes: str = "") -> dict:
    """Resume a HITL-suspended OrchestratorGraph job.

    ``any_id`` may be an orchestrator request_id (UUID), applicant_id, or approval workflow_id.
    ``decision`` must be ``"retry"`` or ``"skip"``.
    """
    from shared.config import config as app_config
    if not app_config.USE_ORCHESTRATOR_GRAPH:
        return {"error": "OrchestratorGraph not enabled (USE_ORCHESTRATOR_GRAPH=false)"}

    if decision not in ("retry", "skip"):
        raise ValueError(f"decision must be 'retry' or 'skip', got {decision!r}")

    job_repo   = ApplicantJobRepository()
    request_id = _resolve_orchestrator_request_id(any_id, job_repo)
    if not request_id:
        raise ValueError(f"No orchestrator job found for identifier: {any_id!r}")

    logger.info("resume_orchestrator_step request_id=%s decision=%s notes=%s", request_id, decision, notes[:80] if notes else "")
    return await resume_orchestrator_service(request_id, decision)


def get_prior_decision_traces(enterprise_id: str, repo: WorkflowRepository | None = None) -> dict:
    """Return pending workflow issues for a specific enterprise_identifier."""
    repository = repo or WorkflowRepository()
    rows = repository.get_prior_decision_traces(enterprise_id)
    unique_states: dict[str, dict] = {}
    for r in rows:
        state = r.get("current_state")
        if not state:
            continue
        if state not in unique_states:
            unique_states[state] = {"current_state": state}
    issues = list(unique_states.values())
    response = {
        "enterprise_identifier": enterprise_id,
        "issue_count": len(rows),
        "issues": issues,
        "at_risk": len(rows) > 0,
    }
    logger.info(
        "get_prior_decision_traces: enterprise_id=%r at_risk=%s issue_count=%d unique_state_count=%d",
        enterprise_id,
        response.get("at_risk"),
        response.get("issue_count", 0),
        len(issues),
    )
    logger.debug("get_prior_decision_traces response=%s", response)
    return response
