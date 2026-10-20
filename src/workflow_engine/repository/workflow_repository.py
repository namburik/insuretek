from __future__ import annotations

from datetime import datetime
from typing import Optional

from workflow_engine.domain.workflow_domain import ApplicantWorkflow, WorkflowApprovalTask, WorkflowState
from workflow_engine.repository.base import WorkflowRepositoryBase
from workflow_engine.db.dynamodb.operations import (
    create_workflow,
    get_workflow_item,
    get_tasks_for_workflow,
    get_workflow_id_by_reference_id,
    get_active_workflow_by_applicant_id,
    update_workflow_task,
    query_workflows_by_applicant,
    query_tasks_by_workflow,
    update_workflow_state,
    append_guardian_event,
)


class WorkflowRepository(WorkflowRepositoryBase):
    """Backend-agnostic workflow repository implementation."""

    def create(self, workflow: ApplicantWorkflow) -> None:
        create_workflow(workflow)

    def get_by_workflow_id(self, workflow_id: str) -> Optional[ApplicantWorkflow]:
        workflow_item = get_workflow_item(workflow_id)
        if workflow_item is None:
            return None

        task_items = get_tasks_for_workflow(workflow_id)
        workflow = ApplicantWorkflow(**workflow_item)
        workflow.approval_tasks = [WorkflowApprovalTask(**task) for task in task_items]
        return workflow

    def get_active_by_applicant_id(self, applicant_id: str) -> Optional[ApplicantWorkflow]:
        item = get_active_workflow_by_applicant_id(applicant_id)
        if item is None:
            return None
        return ApplicantWorkflow(**item)

    def get_by_reference_id(self, reference_id: str) -> Optional[ApplicantWorkflow]:
        workflow_id = get_workflow_id_by_reference_id(reference_id)
        if not workflow_id:
            return None
        return self.get_by_workflow_id(workflow_id)

    def update_task(self, task: WorkflowApprovalTask) -> None:
        update_workflow_task(task)

    def get_workflows_by_applicant_id(self, applicant_id: str) -> list[dict]:
        return query_workflows_by_applicant(applicant_id)

    def get_tasks_by_workflow_id(self, workflow_id: str) -> list[dict]:
        return query_tasks_by_workflow(workflow_id)

    def update_state(
        self,
        workflow_id: str,
        new_state: WorkflowState,
        approved_by: Optional[str] = None,
        completed_at: Optional[datetime | str] = None,
    ) -> None:
        update_workflow_state(workflow_id, new_state, approved_by=approved_by, completed_at=completed_at)

    def append_guardian_event(self, workflow_id: str, event: dict) -> None:
        append_guardian_event(workflow_id, event)
