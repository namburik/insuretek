"""
Workflow domain models and state machine.

Pure domain layer — no DB, no HTTP. All state transition logic lives here
so it can be unit-tested without infrastructure.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, TypedDict

from pydantic import BaseModel, Field, ConfigDict

from workflow_engine.config.gate_check_config import ESSENTIAL_GATE_CHECKS


def generate_workflow_id() -> str:
    return f"wf_{uuid.uuid4().hex[:12]}"


def generate_request_id() -> str:
    return str(uuid.uuid4())


def generate_gate_check_ref_id(gate_check: str) -> str:
    return f"ref_{gate_check}_{uuid.uuid4().hex[:8]}"


class WorkflowState(str, Enum):
    INITIALIZED        = "INITIALIZED"
    AWAITING_WEBHOOKS  = "AWAITING_WEBHOOKS"
    PARTIALLY_APPROVED = "PARTIALLY_APPROVED"
    APPROVED           = "APPROVED"
    REJECTED           = "REJECTED"
    ACTIVE             = "ACTIVE"
    FAILED             = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in (WorkflowState.ACTIVE, WorkflowState.FAILED, WorkflowState.REJECTED)

    def can_transition(self, new_state: "WorkflowState") -> bool:
        return new_state in _ALLOWED_TRANSITIONS.get(self, set())


# Explicit transition matrix — any write not in this map is an illegal state jump.
_ALLOWED_TRANSITIONS: dict["WorkflowState", set["WorkflowState"]] = {
    WorkflowState.INITIALIZED:        {WorkflowState.AWAITING_WEBHOOKS, WorkflowState.FAILED},
    WorkflowState.AWAITING_WEBHOOKS:  {WorkflowState.PARTIALLY_APPROVED, WorkflowState.APPROVED,
                                       WorkflowState.REJECTED, WorkflowState.FAILED},
    WorkflowState.PARTIALLY_APPROVED: {WorkflowState.APPROVED, WorkflowState.REJECTED, WorkflowState.FAILED},
    WorkflowState.APPROVED:           {WorkflowState.ACTIVE, WorkflowState.FAILED},
    # terminal — no outbound transitions
    WorkflowState.ACTIVE:             set(),
    WorkflowState.REJECTED:           set(),
    WorkflowState.FAILED:             set(),
}


class TaskStatus(str, Enum):
    PENDING   = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED    = "FAILED"


class WorkflowApprovalTask(BaseModel):
    task_id:          str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:12]}")
    workflow_id:      str
    gate_check:       str       # "identity" | "bank_verification" | "underwriting" | "logistics"
    reference_id:     str       # opaque ID we send to the external system; echoed back in webhook
    is_essential:     bool = True
    status:           TaskStatus = TaskStatus.PENDING
    response_payload: Dict[str, Any] = Field(default_factory=dict)
    dispatched_at:    datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at:     Optional[datetime] = None

    model_config = ConfigDict(use_enum_values=True)


class ApplicantWorkflow(BaseModel):
    """
    State schema for applicant underwriting workflow.
    Also used as LangGraph thread state — workflow_id is the thread_id.
    """
    workflow_id:               str = Field(default_factory=generate_workflow_id)
    applicant_id:              str
    current_state:             WorkflowState = WorkflowState.INITIALIZED
    approval_tasks:            List[WorkflowApprovalTask] = Field(default_factory=list)
    is_final_approval_granted: bool = False
    approved_by:               Optional[str] = None
    graph_state_blob:          Dict[str, Any] = Field(default_factory=dict)
    step_metadata:             Dict[str, Any] = Field(default_factory=dict)
    created_at:                datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at:                datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at:              Optional[datetime] = None

    model_config = ConfigDict(use_enum_values=True)

    # ── Lookups ───────────────────────────────────────────────────────────────

    def get_task_by_ref(self, reference_id: str) -> Optional[WorkflowApprovalTask]:
        return next((t for t in self.approval_tasks if t.reference_id == reference_id), None)

    def get_task_by_gate_check(self, gate_check: str) -> Optional[WorkflowApprovalTask]:
        return next((t for t in self.approval_tasks if t.gate_check == gate_check), None)

    @property
    def external_correlation_map(self) -> Dict[str, str]:
        """reference_id → gate_check — convenience for logging / legacy code."""
        return {t.reference_id: t.gate_check for t in self.approval_tasks}

    # ── State machine helpers (pure, no side effects) ─────────────────────────

    def _essential_status(self, status: TaskStatus) -> int:
        """Count of essential gate checks in a given status."""
        val = status.value if isinstance(status, TaskStatus) else status
        return sum(
            1 for p in ESSENTIAL_GATE_CHECKS
            if (t := self.get_task_by_gate_check(p)) and t.status == val
        )

    def all_essentials_approved(self) -> bool:
        return self._essential_status(TaskStatus.COMPLETED) == len(ESSENTIAL_GATE_CHECKS)

    def any_essential_rejected(self) -> bool:
        return self._essential_status(TaskStatus.FAILED) > 0

    def compute_next_state(self) -> WorkflowState:
        """
        Pure state transition function — call after mutating a task's status.
        Returns the state the workflow *should* move to; does not mutate self.
        """
        if self.any_essential_rejected():
            return WorkflowState.REJECTED
        if self.all_essentials_approved():
            return WorkflowState.APPROVED
        if self._essential_status(TaskStatus.COMPLETED) > 0:
            return WorkflowState.PARTIALLY_APPROVED
        return WorkflowState.AWAITING_WEBHOOKS


class RiskDecision(BaseModel):
    risk_score:     float = Field(description="Risk score: 0.0 = lowest risk, 1.0 = highest risk.")
    risk_reasoning: str   = Field(description="Concise explanation of the risk assessment.")
    decision:       str   = Field(description="APPROVE, REJECT, or ESCALATE.")


class WorkflowGraphState(TypedDict):
    workflow_id:             str
    applicant_id:            str
    applicant_context:       Dict[str, Any]
    current_workflow_state:  str
    pending_webhook:         Optional[Dict[str, Any]]
    risk_score:              Optional[float]
    risk_reasoning:          str
    risk_decision:           Optional[str]
    human_decision:          Optional[str]
    human_notes:             str
    error:                   Optional[str]
