import asyncio
import json as _json
from collections.abc import Callable
from typing import Any, Dict

from mltek.common.agents.bridge import make_thread_config, resume_graph
from mltek.common.log import setup_structured_logging
from workflow_engine.service.workflow_orchestrator import WorkflowOrchestrator
from workflow_engine.domain.workflow_domain import WorkflowState, generate_workflow_id
from workflow_engine.repository import ApplicantJobRepository, WorkflowRepository
from workflow_engine.service.workflow_graph import WorkflowGraph

logger = setup_structured_logging(__name__)

# This module owns two related flows:
# 1) LangGraph-based applicant workflow approval state machine helpers
# 2) applicant intake orchestration via WorkflowOrchestrator
# They are intentionally colocated here for convenience; if this grows further,
# split into dedicated workflow orchestration and LangGraph service modules.

workflow_coordinator: WorkflowGraph | None = None


def _get_workflow_coordinator() -> WorkflowGraph:
    global workflow_coordinator
    if workflow_coordinator is None:
        workflow_coordinator = WorkflowGraph()
    return workflow_coordinator


def _build_initial_state(workflow_id: str, applicant_id: str, context: Dict[str, Any]) -> dict:
    return {
        "workflow_id":            workflow_id,
        "applicant_id":           applicant_id,
        "applicant_context":      context,
        "current_workflow_state": WorkflowState.INITIALIZED.value,
        "pending_webhook":        None,
        "risk_score":             None,
        "risk_reasoning":         "",
        "risk_decision":          None,
        "human_decision":         None,
        "human_notes":            "",
        "error":                  None,
    }


async def initiate_workflow(applicant_id: str, context: Dict[str, Any], correlation_id: str | None = None) -> Dict[str, Any]:
    from mltek.common.infra.context import CorrelationContext
    if correlation_id:
        CorrelationContext.set(correlation_id)
    else:
        correlation_id = CorrelationContext.get_or_generate()

    existing = await asyncio.to_thread(WorkflowRepository().get_active_by_applicant_id, applicant_id)
    if existing:
        logger.warning(
            "initiate_workflow skipped — active workflow already exists: "
            "applicant_id=%s workflow_id=%s state=%s",
            applicant_id, existing.workflow_id, existing.current_state,
        )
        return {
            "workflow_id": existing.workflow_id,
            "applicant_id": applicant_id,
            "status":      existing.current_state,
        }

    workflow_id = generate_workflow_id()
    config = make_thread_config(workflow_id)
    initial_state = _build_initial_state(workflow_id, applicant_id, context)
    await _get_workflow_coordinator().graph.ainvoke(initial_state, config=config)
    logger.info("Workflow started: applicant_id=%s workflow_id=%s status=%s", applicant_id, workflow_id, WorkflowState.AWAITING_WEBHOOKS.value, extra={"workflow_id": workflow_id, "applicant_id": applicant_id})
    return {
        "workflow_id": workflow_id,
        "applicant_id": applicant_id,
        "status":      WorkflowState.AWAITING_WEBHOOKS.value,
    }


async def receive_webhook(reference_id: str, gate_check: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    workflow = _get_workflow_coordinator().repo.get_by_reference_id(reference_id)
    if not workflow:
        return {"status": "unknown_reference_id", "reference_id": reference_id}
    if WorkflowState(workflow.current_state).is_terminal:
        return {"status": "already_terminal", "workflow_id": workflow.workflow_id}
    result = await resume_graph(
        _get_workflow_coordinator().graph,
        {**payload, "reference_id": reference_id, "gate_check": gate_check},
        make_thread_config(workflow.workflow_id),
    )
    workflow_state = result.get("current_workflow_state")
    if workflow_state is None:
        logger.warning(
            "receive_webhook resumed graph without current_workflow_state",
            extra={"reference_id": reference_id, "workflow_id": workflow.workflow_id},
        )
    return {"workflow_id": workflow.workflow_id, "workflow_state": workflow_state}


async def submit_human_decision(workflow_id: str, decision: str, notes: str = "") -> Dict[str, Any]:
    result = await resume_graph(
        _get_workflow_coordinator().graph,
        {"decision": decision, "notes": notes},
        make_thread_config(workflow_id),
    )
    workflow_state = result.get("current_workflow_state")
    if workflow_state is None:
        logger.warning(
            "submit_human_decision resumed graph without current_workflow_state",
            extra={"workflow_id": workflow_id},
        )
    return {"workflow_id": workflow_id, "workflow_state": workflow_state}


def _render_step_metadata(step_result: dict[str, Any]) -> str:
    metadata_lines: list[str] = []
    if step_result.get("guardian") is not None:
        guardian_blob = _json.dumps(step_result["guardian"], indent=2)
        metadata_lines.append("  Guardian:")
        metadata_lines.extend([f"    {line}" for line in guardian_blob.splitlines()])
    if step_result.get("reflection") is not None:
        reflection_blob = _json.dumps(step_result["reflection"], indent=2)
        metadata_lines.append("  Reflection:")
        metadata_lines.extend([f"    {line}" for line in reflection_blob.splitlines()])
    return "\n".join(metadata_lines)


async def run_applicant_orchestrator(
    request_id: str,
    prompt: str | None = None,
    applicant_json: dict | None = None,
    job_repo: ApplicantJobRepository | None = None,
    on_error: Callable[[], None] | None = None,
    env: str = "prod",
    session_id: str | None = None,
    correlation_id: str | None = None,
) -> None:
    logger.info(
        "Starting applicant workflow for request_id=%s", request_id)
    if job_repo is None:
        job_repo = ApplicantJobRepository()

    state_updates: dict[str, str] = {}

    async def _on_state_update(key: str, value: str) -> None:
        state_updates[key] = value
        if key == "applicant_id" and value:
            try:
                await asyncio.to_thread(job_repo.update_applicant_id, request_id, value)
                logger.info(
                    "Updated workflow job applicant_id for request_id=%s applicant_id=%s",
                    request_id,
                    value,
                )
            except Exception:
                logger.exception(
                    "Failed to update workflow job applicant_id for request_id=%s",
                    request_id,
                )

    async def _on_step_complete(step: dict) -> None:
        await asyncio.to_thread(job_repo.append_step, request_id, step)

    try:
        workflow_orchestrator = WorkflowOrchestrator(
            prompts={"create_applicant": prompt} if prompt else None,
            step_data={"create_applicant": {"applicant_json": applicant_json}} if applicant_json else None,
            on_state_update=_on_state_update,
            on_step_complete=_on_step_complete,
            source="react",
            env=env,
        )
        success = await workflow_orchestrator.run(request_id=request_id, session_id=session_id, correlation_id=correlation_id)

        summary_lines = []
        for r in workflow_orchestrator.results:
            score = f"  [{r['eval_score']:.0%}]" if r.get("eval_score") is not None else ""
            base_line = f"[{r['status']}]{score} {r['label']}: {r.get('note', '')}".strip()
            summary_lines.append(base_line)
            metadata = _render_step_metadata(r)
            if metadata:
                summary_lines.append(metadata)

        result_text = (
            f"Workflow: {workflow_orchestrator.name}\n"
            f"Overall: {'SUCCESS' if success else 'FAILED'}\n"
            + "\n".join(summary_lines)
        )
        if state_updates:
            result_text += "\n\nExtracted state:\n" + _json.dumps(state_updates, indent=2)

        applicant_id = state_updates.get("applicant_id") or workflow_orchestrator.state.get("applicant_id")

        await asyncio.to_thread(
            job_repo.mark_done,
            request_id,
            applicant_id,
            result_text,
            workflow_orchestrator.results,
        )
        logger.debug("Applicant workflow result: %s", result_text)
        logger.info("Completed applicant workflow for request_id=%s success=%s applicant_id=%s", request_id, success, applicant_id or "none")

        if applicant_id:
            context = {k: v for k, v in {"prompt": prompt, "applicant_json": applicant_json}.items() if v is not None}
            logger.info("Triggering gate checks for request_id=%s applicant_id=%s", request_id, applicant_id)
            await initiate_workflow(applicant_id, context=context)
        else:
            if on_error:
                on_error()
    except Exception as exc:
        logger.exception("Applicant workflow agent call failed for request_id=%s", request_id)
        await asyncio.to_thread(job_repo.mark_error, request_id, str(exc))
        if on_error:
            on_error()


async def run_applicant_orchestrator_graph(
    request_id: str,
    prompt: str | None = None,
    applicant_json: dict | None = None,
    job_repo: ApplicantJobRepository | None = None,
    on_error: Callable[[], None] | None = None,
    env: str = "prod",
    session_id: str | None = None,
    correlation_id: str | None = None,
) -> None:
    from workflow_engine.service.orchestrator_graph import OrchestratorGraph
    logger.info("Starting OrchestratorGraph workflow for request_id=%s", request_id)
    if job_repo is None:
        job_repo = ApplicantJobRepository()

    state_updates: dict[str, str] = {}

    async def _on_state_update(key: str, value: str) -> None:
        state_updates[key] = value
        if key == "applicant_id" and value:
            try:
                await asyncio.to_thread(job_repo.update_applicant_id, request_id, value)
            except Exception:
                logger.exception("Failed to update applicant_id for request_id=%s", request_id)

    async def _on_step_complete(step: dict) -> None:
        await asyncio.to_thread(job_repo.append_step, request_id, step)

    try:
        graph = OrchestratorGraph(
            prompts={"create_applicant": prompt} if prompt else None,
            step_data={"create_applicant": {"applicant_json": applicant_json}} if applicant_json else None,
            on_state_update=_on_state_update,
            on_step_complete=_on_step_complete,
            source="react",
            env=env,
            job_repo=job_repo,
        )
        success = await graph.run(
            request_id=request_id,
            session_id=session_id or "",
            correlation_id=correlation_id or "",
        )

        job         = await asyncio.to_thread(job_repo.get, request_id)
        job_status  = (job or {}).get("status")
        steps       = (job or {}).get("steps") or []
        applicant_id = state_updates.get("applicant_id") or (job or {}).get("applicant_id")

        if job_status == "hitl_pending":
            logger.info("OrchestratorGraph suspended (hitl_pending) request_id=%s applicant_id=%s", request_id, applicant_id or "none")
        elif not success:
            failed_step = next((s for s in steps if s.get("status") == "FAIL"), None)
            step_id = (failed_step or {}).get("label") or (failed_step or {}).get("step_id") or "unknown"
            error   = (failed_step or {}).get("note") or "step failed"
            await asyncio.to_thread(job_repo.mark_hitl_pending, request_id, step_id, step_id, error)
            from mltek.config import config as app_config
            resume_url = f"{app_config.BFF_SERVICE_URL}/webhooks/orchestrator/{request_id}/resume?decision=retry"
            logger.warning("OrchestratorGraph FAIL → hitl_pending request_id=%s step=%s resume_url=%s", request_id, step_id, resume_url)
        else:
            await asyncio.to_thread(
                job_repo.mark_done,
                request_id,
                applicant_id,
                f"OrchestratorGraph {graph.name}: SUCCESS",
                steps,
            )
            logger.info("Completed OrchestratorGraph workflow for request_id=%s success=%s applicant_id=%s", request_id, success, applicant_id or "none")

        gate_ready = success and job_status != "hitl_pending"
        if applicant_id and gate_ready:
            context = {k: v for k, v in {"prompt": prompt, "applicant_json": applicant_json}.items() if v is not None}
            logger.info("Triggering gate checks for request_id=%s applicant_id=%s", request_id, applicant_id)
            wf_result = await initiate_workflow(applicant_id, context=context)
            workflow_id = wf_result.get("workflow_id")
            if workflow_id:
                try:
                    await asyncio.to_thread(job_repo.update_workflow_id, request_id, workflow_id)
                except Exception:
                    logger.warning("Failed to store workflow_id on orchestrator job request_id=%s", request_id, exc_info=True)
        elif on_error:
            on_error()
    except Exception as exc:
        logger.exception("OrchestratorGraph workflow failed for request_id=%s", request_id)
        await asyncio.to_thread(job_repo.mark_error, request_id, str(exc))
        if on_error:
            on_error()


async def resume_orchestrator(request_id: str, decision: str) -> dict:
    """Resume a suspended OrchestratorGraph job after a HITL decision."""
    from workflow_engine.service.orchestrator_graph import OrchestratorGraph
    from workflow_engine.service.workflow_orchestrator import _DEFAULT_WORKFLOW, _load_workflow
    job_repo = ApplicantJobRepository()

    job = await asyncio.to_thread(job_repo.get, request_id)
    if not job:
        raise ValueError(f"No orchestrator job found for request_id={request_id!r}")

    hitl = job.get("hitl_step") or {}
    if isinstance(hitl, str):
        import json as _json
        hitl = _json.loads(hitl)
    failed_id  = hitl.get("step_id") or hitl.get("label") or ""
    saved_state: dict = hitl.get("orch_state") or {}

    raw = _load_workflow(_DEFAULT_WORKFLOW)
    all_step_ids: list[str] = [s.get("id", "") for s in (raw.get("workflow", raw).get("steps", []))]

    try:
        fail_idx = all_step_ids.index(failed_id)
    except ValueError:
        fail_idx = 0

    steps_to_run = all_step_ids[fail_idx:] if decision != "skip" else all_step_ids[fail_idx + 1:]
    logger.info("resume_orchestrator request_id=%s decision=%s failed=%s remaining=%s", request_id, decision, failed_id, steps_to_run)

    state_updates: dict[str, str] = {}

    async def _on_state_update(key: str, value: str) -> None:
        state_updates[key] = value
        if key == "applicant_id" and value:
            try:
                await asyncio.to_thread(job_repo.update_applicant_id, request_id, value)
            except Exception:
                logger.warning("Failed to update applicant_id on resume request_id=%s", request_id, exc_info=True)

    async def _on_step_complete(step: dict) -> None:
        await asyncio.to_thread(job_repo.append_step, request_id, step)

    graph = OrchestratorGraph(
        only=steps_to_run,
        on_state_update=_on_state_update,
        on_step_complete=_on_step_complete,
        job_repo=job_repo,
    )
    success = await graph.run(request_id, initial_state=saved_state)

    job = await asyncio.to_thread(job_repo.get, request_id)
    if not success and (job or {}).get("status") == "hitl_pending":
        logger.info("resume_orchestrator: graph re-suspended request_id=%s", request_id)
        return {"request_id": request_id, "decision": decision, "success": False, "status": "hitl_pending"}

    steps        = (job or {}).get("steps") or []
    applicant_id = state_updates.get("applicant_id") or (job or {}).get("applicant_id")
    await asyncio.to_thread(
        job_repo.mark_done,
        request_id,
        applicant_id,
        f"OrchestratorGraph resumed: {'SUCCESS' if success else 'PARTIAL'}",
        steps,
    )

    if applicant_id and success:
        wf_result = await initiate_workflow(applicant_id, context={"resumed": True})
        workflow_id = wf_result.get("workflow_id")
        if workflow_id:
            try:
                await asyncio.to_thread(job_repo.update_workflow_id, request_id, workflow_id)
            except Exception:
                logger.warning("Failed to store workflow_id after resume request_id=%s", request_id, exc_info=True)

    logger.info("resume_orchestrator exit request_id=%s decision=%s success=%s applicant_id=%s", request_id, decision, success, applicant_id or "none")
    return {"request_id": request_id, "decision": decision, "success": success}
