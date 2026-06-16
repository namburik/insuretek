"""
LangGraph state machine for applicant workflow approval.

Flow:
  START → initiate → await_webhook [interrupt]
    → process_webhook → evaluate_progress
        ├─ any essential FAILED  → handle_rejection → END
        ├─ all essentials PASSED → decision_node [DEFAULT agent]
        │     ├─ APPROVE   → activate_applicant → END
        │     ├─ ESCALATE  → human_review [interrupt]
        │     │     ├─ APPROVE → activate_applicant → END
        │     │     └─ REJECT  → handle_rejection  → END
        │     └─ REJECT   → handle_rejection → END
        └─ more pending  → back to await_webhook [loop]
"""

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from shared.common.agents.bridge import interrupt
from shared.common.tools.ddb_format import DDBJsonEncoder

from shared.common.agents.graph_builder import GraphBuilder
from workflow_engine.repository import WorkflowRepository, get_workflow_checkpointer

from workflow_engine.config import GRAPH_CONFIG_PATH as _GRAPH_CONFIG

if not _GRAPH_CONFIG.exists():
    raise FileNotFoundError(f"Workflow graph config not found: {_GRAPH_CONFIG}")

from shared.common.agents.lang_agents import call_structured_agent
from shared.common.router.resolver import call_resolver
from shared.common.infra.context import set_jit_session, with_token_phase
from shared.common.infra import extract_result_content
from workflow_engine.config import GATE_CHECK_ENDPOINTS
from workflow_engine.config.prompts import ACTIVATE_APPLICANT_PROMPT, DECISION_SYSTEM, build_decision_prompt
from workflow_engine.domain.workflow_domain import (
    WorkflowApprovalTask,
    ApplicantWorkflow,
    RiskDecision,
    TaskStatus,
    WorkflowGraphState,
    WorkflowState,
)
from workflow_engine.service.gate_check_client import dispatch_gate_check, make_ref_id

from shared.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)


# ── Graph class ───────────────────────────────────────────────────────────────

class WorkflowGraph:
    """
    Builds and owns the compiled LangGraph for applicant underwriting workflow.

    The graph suspends at two interrupt points:
      1. await_webhook  — waiting for an external gate-check callback
      2. human_review   — waiting for a human reviewer decision (escalated cases)

    Both are resumed by calling graph.ainvoke(Command(resume=payload), config).
    """

    def __init__(
        self,
        repo: Optional[WorkflowRepository] = None,
        checkpointer=None,
    ):
        self.repo = repo or WorkflowRepository()
        self._checkpointer = checkpointer or get_workflow_checkpointer()
        self.graph = self._build()

    # ── Graph construction ────────────────────────────────────────────────────

    def _build(self):
        return GraphBuilder.from_file(
            _GRAPH_CONFIG,
            state_schema=WorkflowGraphState,
            nodes={
                "initiate":           self._initiate_node,
                "await_webhook":      self._await_webhook_node,
                "process_webhook":    self._process_webhook_node,
                "decision_node":      self._decision_node,
                "human_review":       self._human_review_node,
                "activate_applicant": self._activate_applicant_node,
                "handle_rejection":   self._handle_rejection_node,
            },
            routers={
                "route_after_webhook":  self._route_after_webhook,
                "route_risk_decision":  self._route_risk_decision,
                "route_human_decision": self._route_human_decision,
            },
            checkpointer=self._checkpointer,
        ).build()

    # ── Nodes ─────────────────────────────────────────────────────────────────

    async def _initiate_node(self, state: WorkflowGraphState) -> Dict[str, Any]:
        """Create workflow in DB and dispatch gate-check requests. Idempotent on retry/replay."""
        existing = self.repo.get_by_workflow_id(state["workflow_id"])
        if existing is not None:
            logger.info(
                "Workflow already initiated — skipping duplicate init",
                extra={"workflow_id": state["workflow_id"]},
            )
            cs = existing.current_state
            return {"current_workflow_state": cs.value if isinstance(cs, WorkflowState) else cs}

        workflow = ApplicantWorkflow(
            workflow_id=state["workflow_id"],
            applicant_id=state["applicant_id"],
            current_state=WorkflowState.AWAITING_WEBHOOKS,
        )
        dispatch_args: List[tuple] = []
        for gate_check, cfg in GATE_CHECK_ENDPOINTS.items():
            ref_id = make_ref_id(gate_check)
            workflow.approval_tasks.append(
                WorkflowApprovalTask(
                    workflow_id=workflow.workflow_id,
                    gate_check=gate_check,
                    reference_id=ref_id,
                    is_essential=cfg["is_essential"],
                )
            )
            dispatch_args.append((gate_check, cfg, ref_id))

        # Persist workflow + tasks before dispatching so any fast callback
        # can find the reference_id in the DB.
        self.repo.create(workflow)

        results = await asyncio.gather(
            *[
                dispatch_gate_check(
                    gate_check=gate_check,
                    workflow_id=workflow.workflow_id,
                    applicant_id=state["applicant_id"],
                    endpoint_url=cfg["url"],
                    payload=state["applicant_context"],
                    ref_id=ref_id,
                    timeout=cfg.get("timeout", 10.0),
                )
                for gate_check, cfg, ref_id in dispatch_args
            ],
            return_exceptions=True,
        )

        for gate_check, result in zip(GATE_CHECK_ENDPOINTS, results):
            if isinstance(result, Exception):
                # Log but leave the task PENDING — a retry or re-delivery may still succeed.
                # Only an explicit negative webhook should mark a task FAILED.
                logger.error(
                    "Dispatch failed for %s — task left PENDING for retry: %s",
                    gate_check, result,
                    extra={"workflow_id": workflow.workflow_id},
                )

        dispatched = sum(1 for r in results if not isinstance(r, Exception))
        failed = len(results) - dispatched
        logger.info(
            "Workflow dispatched: workflow_id=%s applicant_id=%s gate_checks=%d dispatched=%d failed=%d status=%s",
            workflow.workflow_id, state["applicant_id"], len(results), dispatched, failed, WorkflowState.AWAITING_WEBHOOKS.value,
            extra={"workflow_id": workflow.workflow_id},
        )
        return {"current_workflow_state": WorkflowState.AWAITING_WEBHOOKS.value}

    async def _await_webhook_node(self, state: WorkflowGraphState) -> Dict[str, Any]:
        """Suspend the graph until a webhook payload arrives via Command(resume=...)."""
        webhook = interrupt({
            "status": "waiting_for_webhook",
            "workflow_id": state["workflow_id"],
        })
        return {"pending_webhook": webhook}

    async def _process_webhook_node(self, state: WorkflowGraphState) -> Dict[str, Any]:
        """Apply one incoming webhook to the task, persist, and recompute workflow state."""
        payload = state.get("pending_webhook") or {}
        reference_id = payload.get("reference_id", "")

        workflow = self.repo.get_by_reference_id(reference_id)
        if not workflow:
            logger.warning("Unknown reference_id", extra={"reference_id": reference_id})
            return {
                "error": f"unknown_reference_id:{reference_id}",
                "current_workflow_state": WorkflowState.FAILED.value,
                "pending_webhook": None,
            }

        if WorkflowState(workflow.current_state).is_terminal:
            return {"current_workflow_state": workflow.current_state, "pending_webhook": None}

        task = workflow.get_task_by_ref(reference_id)
        if not task:
            return {"current_workflow_state": workflow.current_state, "pending_webhook": None}

        task_status = TaskStatus(task.status) if not isinstance(task.status, TaskStatus) else task.status
        if task_status != TaskStatus.PENDING:
            # Duplicate webhook — idempotent no-op
            return {"current_workflow_state": workflow.current_state, "pending_webhook": None}

        approved = payload.get("approved") is True or payload.get("status") == "APPROVED"
        task.status = TaskStatus.COMPLETED if approved else TaskStatus.FAILED
        task.response_payload = payload
        task.completed_at = datetime.now(timezone.utc)
        self.repo.update_task(task)

        next_state = workflow.compute_next_state()
        prev_state = WorkflowState(workflow.current_state)
        if next_state != prev_state:
            if not prev_state.can_transition(next_state):
                logger.error(
                    "Illegal state transition blocked: %s → %s workflow_id=%s",
                    prev_state.value, next_state.value, workflow.workflow_id,
                )
                raise ValueError(f"Illegal workflow transition: {prev_state.value} → {next_state.value}")
            self.repo.update_state(
                workflow_id=workflow.workflow_id,
                new_state=WorkflowState(next_state),
                approved_by="workflow_graph",
                completed_at=datetime.now(timezone.utc) if WorkflowState(next_state).is_terminal else None,
            )

        logger.debug("[PROCESS_WEBHOOK] gate_check=%s task_status=%s next_state=%s", task.gate_check, task.status, next_state.value)
        logger.info("Webhook processed", extra={"workflow_id": workflow.workflow_id, "gate_check": task.gate_check, "task_status": task.status, "next_state": next_state.value})
        return {"current_workflow_state": next_state.value, "pending_webhook": None}

    async def _decision_node(self, state: WorkflowGraphState) -> Dict[str, Any]:
        """
        Use the DEFAULT agent to analyze gate-check payloads and produce
        a structured workflow recommendation (APPROVE / REJECT / ESCALATE).
        """
        logger.info("decision_node: starting for workflow_id=%s", state["workflow_id"])
        workflow = self.repo.get_by_workflow_id(state["workflow_id"])
        summaries = "\n\n".join(
            f"[{t.gate_check.upper()}] essential={t.is_essential} status={t.status}\n"
            f"Response: {json.dumps(t.response_payload, indent=2, cls=DDBJsonEncoder)}"
            for t in (workflow.approval_tasks if workflow else [])
        )

        prompt = build_decision_prompt(
            applicant_id=state["applicant_id"],
            applicant_context=json.dumps(state["applicant_context"], cls=DDBJsonEncoder),
            summaries=summaries,
        )

        try:
            result = await asyncio.wait_for(
                call_structured_agent(prompt, RiskDecision, system_prompt=DECISION_SYSTEM),
                timeout=180,
            )
        except asyncio.TimeoutError:
            logger.error("decision_node: timed out after 180s — defaulting to ESCALATE")
            result = RiskDecision(risk_score=0.5, risk_reasoning="Decision agent timeout", decision="ESCALATE")
        except Exception as exc:
            logger.exception("decision_node: failed: %s", exc)
            result = RiskDecision(risk_score=0.5, risk_reasoning=f"Decision agent error: {exc}", decision="ESCALATE")

        logger.debug("[DECISION] decision=%s score=%s reasoning=%s", result.decision, result.risk_score, result.risk_reasoning[:80])
        logger.info("Decision complete", extra={"workflow_id": state["workflow_id"], "risk_score": result.risk_score, "decision": result.decision})
        return {
            "risk_score": result.risk_score,
            "risk_reasoning": result.risk_reasoning,
            "risk_decision": result.decision,
        }

    async def _human_review_node(self, state: WorkflowGraphState) -> Dict[str, Any]:
        """
        Suspend the graph and surface the risk summary to a human reviewer.
        Resumes when Command(resume={"decision": "APPROVE"|"REJECT", "notes": "..."}) is submitted.
        """
        result = interrupt({
            "action": "human_review_required",
            "workflow_id": state["workflow_id"],
            "applicant_id": state["applicant_id"],
            "risk_score": state.get("risk_score"),
            "risk_reasoning": state.get("risk_reasoning"),
            "instructions": "Submit {\"decision\": \"APPROVE\" | \"REJECT\", \"notes\": \"...\"}",
        })
        return {
            "human_decision": result.get("decision"),
            "human_notes": result.get("notes", ""),
        }

    async def _activate_applicant_node(self, state: WorkflowGraphState) -> Dict[str, Any]:
        applicant_id = state["applicant_id"]
        prompt = ACTIVATE_APPLICANT_PROMPT.format(applicant_id=applicant_id)
        logger.info("Calling applicant activation agent", extra={"applicant_id": applicant_id})
        logger.debug("Applicant activation prompt: %s", prompt)

        try:
            set_jit_session("done", state["workflow_id"])
            result = await call_resolver(prompt, session_id=state["workflow_id"])
        except Exception:
            logger.exception("Activation agent failed", extra={"applicant_id": applicant_id})
            self.repo.update_state(
                workflow_id=state["workflow_id"],
                new_state=WorkflowState.FAILED,
                completed_at=datetime.now(timezone.utc),
            )
            return {"current_workflow_state": WorkflowState.FAILED.value, "error": "activation_failed"}

        logger.info("Applicant activation agent response: %s", extract_result_content(result), extra={"applicant_id": applicant_id})
        approved_by = "human_reviewer" if state.get("human_decision") else "workflow_graph"
        self.repo.update_state(
            workflow_id=state["workflow_id"],
            new_state=WorkflowState.ACTIVE,
            approved_by=approved_by,
            completed_at=datetime.now(timezone.utc),
        )
        logger.info("Applicant activated", extra={"applicant_id": applicant_id})
        return {"current_workflow_state": WorkflowState.ACTIVE.value}

    async def _handle_rejection_node(self, state: WorkflowGraphState) -> Dict[str, Any]:
        current_state = state.get("current_workflow_state")
        terminal_state = WorkflowState(current_state) if current_state in (WorkflowState.REJECTED.value, WorkflowState.FAILED.value) else WorkflowState.REJECTED
        self.repo.update_state(
            workflow_id=state["workflow_id"],
            new_state=terminal_state,
            completed_at=datetime.now(timezone.utc),
        )
        logger.info("Workflow terminal state set", extra={"workflow_id": state["workflow_id"], "terminal_state": terminal_state.value})
        return {"current_workflow_state": terminal_state.value}

    # ── Conditional edge routers ───────────────────────────────────────────────

    def _route_after_webhook(self, state: WorkflowGraphState) -> str:
        ws = state.get("current_workflow_state", "")
        logger.debug("[ROUTE] current_workflow_state=%r APPROVED=%r match=%s", ws, WorkflowState.APPROVED.value, ws == WorkflowState.APPROVED.value)
        logger.info("route_after_webhook: current_workflow_state=%r", ws)
        if ws in (WorkflowState.REJECTED.value, WorkflowState.FAILED.value):
            return "handle_rejection"
        if ws == WorkflowState.APPROVED.value:
            return "decision_node"
        return "await_webhook"

    def _route_risk_decision(self, state: WorkflowGraphState) -> str:
        decision = state.get("risk_decision") or "ESCALATE"
        if decision == "APPROVE":
            return "activate_applicant"
        if decision == "REJECT":
            return "handle_rejection"
        return "human_review"

    def _route_human_decision(self, state: WorkflowGraphState) -> str:
        return "activate_applicant" if state.get("human_decision") == "APPROVE" else "handle_rejection"


