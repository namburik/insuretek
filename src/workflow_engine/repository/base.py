from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

from workflow_engine.domain.workflow_domain import (
    ApplicantWorkflow,
    WorkflowApprovalTask,
    WorkflowState,
)


class WorkflowRepositoryBase(ABC):
    """Shared workflow repository interface for any backend implementation."""

    @abstractmethod
    def create(self, workflow: ApplicantWorkflow) -> None:
        pass

    @abstractmethod
    def get_by_workflow_id(self, workflow_id: str) -> Optional[ApplicantWorkflow]:
        pass

    @abstractmethod
    def get_by_reference_id(self, reference_id: str) -> Optional[ApplicantWorkflow]:
        pass

    @abstractmethod
    def get_active_by_applicant_id(self, applicant_id: str) -> Optional[ApplicantWorkflow]:
        pass

    @abstractmethod
    def update_task(self, task: WorkflowApprovalTask) -> None:
        pass

    @abstractmethod
    def get_prior_decision_traces(self, enterprise_id: str) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def update_state(
        self,
        workflow_id: str,
        new_state: WorkflowState,
        approved_by: Optional[str] = None,
        completed_at: Optional[datetime | str] = None,
    ) -> None:
        pass

    @abstractmethod
    def append_guardian_event(self, workflow_id: str, event: dict) -> None:
        pass


class ApplicantJobRepositoryBase(ABC):
    """Shared background applicant workflow job repository interface."""

    @abstractmethod
    def create(self, request_id: str) -> None:
        pass

    @abstractmethod
    def get(self, request_id: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def update_applicant_id(self, request_id: str, applicant_id: str) -> None:
        pass

    @abstractmethod
    def mark_done(self, request_id: str, applicant_id: Optional[str], result: str, steps: list) -> None:
        pass

    @abstractmethod
    def get_by_applicant_id(self, applicant_id: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_by_workflow_id(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def mark_error(self, request_id: str, error: str) -> None:
        pass

    @abstractmethod
    def append_step(self, request_id: str, step: dict) -> None:
        pass
