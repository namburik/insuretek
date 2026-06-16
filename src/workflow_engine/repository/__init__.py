from __future__ import annotations

from workflow_engine.repository.workflow_repository import WorkflowRepository
from workflow_engine.repository.workflow_request_repository import ApplicantJobRepository
from workflow_engine.repository.workflow_checkpointer import ApplicantWorkflowSaver


def get_workflow_repository() -> WorkflowRepository:
    """Get the workflow repository implementation for the active DB_TYPE."""
    return WorkflowRepository()


def get_applicant_job_repository() -> ApplicantJobRepository:
    """Get the background applicant workflow job repository implementation."""
    return ApplicantJobRepository()


def get_workflow_checkpointer() -> ApplicantWorkflowSaver:
    """Get the workflow checkpoint saver implementation."""
    return ApplicantWorkflowSaver()


__all__ = [
    "WorkflowRepository",
    "ApplicantJobRepository",
    "ApplicantWorkflowSaver",
    "get_workflow_repository",
    "get_applicant_job_repository",
    "get_workflow_checkpointer",
]
