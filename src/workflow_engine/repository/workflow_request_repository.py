from __future__ import annotations

from typing import Optional

from workflow_engine.repository.base import ApplicantJobRepositoryBase
from workflow_engine.db.dynamodb.operations import (
    create_job,
    get_job,
    update_job_applicant_id,
    mark_job_done,
    get_job_by_applicant_id,
    get_job_by_workflow_id,
    mark_job_error,
    append_job_step,
    mark_job_hitl_pending,
    update_job_workflow_id,
)


class ApplicantJobRepository(ApplicantJobRepositoryBase):
    """Backend-agnostic applicant workflow job repository."""

    def create(self, request_id: str) -> None:
        create_job(request_id)

    def get(self, request_id: str) -> Optional[dict]:
        return get_job(request_id)

    def update_applicant_id(self, request_id: str, applicant_id: str) -> None:
        update_job_applicant_id(request_id, applicant_id)

    def mark_done(self, request_id: str, applicant_id: Optional[str], result: str, steps: list) -> None:
        mark_job_done(request_id, applicant_id, result, steps)

    def get_by_applicant_id(self, applicant_id: str) -> Optional[dict]:
        return get_job_by_applicant_id(applicant_id)

    def get_by_workflow_id(self, workflow_id: str) -> Optional[dict]:
        return get_job_by_workflow_id(workflow_id)

    def mark_error(self, request_id: str, error: str) -> None:
        mark_job_error(request_id, error)

    def append_step(self, request_id: str, step: dict) -> None:
        append_job_step(request_id, step)

    def mark_hitl_pending(self, request_id: str, step_id: str, step_label: str, error: str, orch_state: dict | None = None) -> None:
        mark_job_hitl_pending(request_id, step_id, step_label, error, orch_state=orch_state)

    def update_workflow_id(self, request_id: str, workflow_id: str) -> None:
        update_job_workflow_id(request_id, workflow_id)
