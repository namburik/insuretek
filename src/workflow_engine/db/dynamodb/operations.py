import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional, Sequence, Tuple

from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Attr, Key
from concurrent.futures import ThreadPoolExecutor
from shared.common.log import setup_structured_logging
from shared.db.dynamodb.dynamodb_operations import DynamoDBTable, get_dynamodb_resource
from shared.common.agents.bridge import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    ChannelVersions,
    JsonPlusSerializer,
    get_thread_id,
    RunnableConfig,
)

from workflow_engine.domain.workflow_domain import ApplicantWorkflow, WorkflowApprovalTask, WorkflowState
from shared.common.tools.ddb_format import normalize_dynamodb_value, sanitize_dynamodb_map_keys as _sanitize_dynamodb_map_keys

# ── Table names (DynamoDB physical schema — do not rename) ────────────────────
WORKFLOW_TABLE_NAME         = "itek_workflow_applicant"
APPROVAL_TASKS_TABLE_NAME   = "itek_workflow_approval_tasks"
CHECKPOINTS_TABLE_NAME      = "itek_workflow_checkpoints"
CHECKPOINT_WRITES_TABLE_NAME = "itek_workflow_checkpoint_writes"
WORKFLOW_REQUEST_TABLE_NAME = "itek_workflow_applicant_request"

logger = setup_structured_logging(__name__)

# ── Module-level table handles ────────────────────────────────────────────────
dynamodb = get_dynamodb_resource()
workflow_table         = DynamoDBTable(dynamodb.Table(WORKFLOW_TABLE_NAME))
approval_tasks_table   = DynamoDBTable(dynamodb.Table(APPROVAL_TASKS_TABLE_NAME))
checkpoints_table      = DynamoDBTable(dynamodb.Table(CHECKPOINTS_TABLE_NAME))
checkpoint_writes_table = DynamoDBTable(dynamodb.Table(CHECKPOINT_WRITES_TABLE_NAME))
workflow_request_table = DynamoDBTable(dynamodb.Table(WORKFLOW_REQUEST_TABLE_NAME))

CHECKPOINT_WRITE_SORT_KEY = "sk"  # matches workflow_ddb_schema.json itek_workflow_checkpoint_writes

# Terminal states: no outbound transitions exist from these states.
_TERMINAL_STATES = frozenset({
    WorkflowState.ACTIVE.value,
    WorkflowState.FAILED.value,
    WorkflowState.REJECTED.value,
})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize_workflow_id(value: str | Any) -> Any:
    if not isinstance(value, str):
        return value
    return value.strip().strip("/")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Workflow table ops ────────────────────────────────────────────────────────

def task_items_for_reference(reference_id: str) -> List[Dict[str, Any]]:
    for index_name in ("reference_id-index", "reference-id-index", "ReferenceIdIndex"):
        try:
            return approval_tasks_table.query(
                Key("reference_id").eq(reference_id),
                index_name=index_name,
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ValidationException":
                raise
    return approval_tasks_table.scan(filter_expression=Attr("reference_id").eq(reference_id))


def create_workflow(workflow: ApplicantWorkflow) -> None:
    workflow_item = workflow.model_dump(mode="json", exclude={"approval_tasks"})
    workflow_table.put_item(workflow_item)
    for task in workflow.approval_tasks:
        approval_tasks_table.put_item(task.model_dump(mode="json"))
    logger.info("Inserted workflow workflow_id=%s task_count=%d", workflow.workflow_id, len(workflow.approval_tasks))


def get_workflow_item(workflow_id: str) -> Optional[Dict[str, Any]]:
    return workflow_table.get_item({"workflow_id": _normalize_workflow_id(workflow_id)})


def get_tasks_for_workflow(workflow_id: str) -> List[Dict[str, Any]]:
    try:
        return approval_tasks_table.query(Key("workflow_id").eq(workflow_id))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ValidationException":
            return approval_tasks_table.scan(filter_expression=Attr("workflow_id").eq(workflow_id))
        raise


def get_workflow_id_by_reference_id(reference_id: str) -> Optional[str]:
    items = task_items_for_reference(reference_id)
    return items[0].get("workflow_id") if items else None


def update_workflow_task(task: WorkflowApprovalTask) -> None:
    fields: Dict[str, Any] = {
        "status": task.status.value if hasattr(task.status, "value") else task.status,
        "response_payload": task.response_payload,
    }
    if task.completed_at is not None:
        fields["completed_at"] = task.completed_at.isoformat()
    task_key = {"workflow_id": task.workflow_id, "task_id": task.task_id}
    try:
        if "workflow_id" not in {k["AttributeName"] for k in approval_tasks_table.table.key_schema}:
            task_key = {"task_id": task.task_id}
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise
    approval_tasks_table.update_item(task_key, fields)
    logger.info("Updated workflow task workflow_id=%s task_id=%s status=%s", task.workflow_id, task.task_id, task.status)


def update_workflow_state(
    workflow_id: str,
    new_state: WorkflowState,
    approved_by: Optional[str] = None,
    completed_at: Optional[str] = None,
) -> None:
    fields: Dict[str, Any] = {
        "current_state": new_state.value,
        "is_final_approval_granted": new_state == WorkflowState.APPROVED,
        "updated_at": iso_now(),
    }
    if approved_by is not None:
        fields["approved_by"] = approved_by
    if completed_at is not None:
        fields["completed_at"] = completed_at.isoformat() if hasattr(completed_at, "isoformat") else completed_at
    workflow_table.update_item({"workflow_id": workflow_id}, fields)
    logger.info("Updated workflow state workflow_id=%s state=%s approved_by=%s", workflow_id, new_state, approved_by or "none")


def append_guardian_event(workflow_id: str, event: dict) -> None:
    item = workflow_table.get_item({"workflow_id": workflow_id})
    if not item:
        return
    metadata = item.get("step_metadata") or {}
    events = metadata.get("guardian_events", [])
    events.append(event)
    metadata["guardian_events"] = events
    workflow_table.update_item({"workflow_id": workflow_id}, {"step_metadata": metadata})
    logger.info("Guardian event appended workflow_id=%s allowed=%s", workflow_id, event.get("allowed"))


def get_active_workflow_by_applicant_id(applicant_id: str) -> Optional[Dict[str, Any]]:
    """Return the most recent in-flight (non-terminal) workflow for this applicant, or None."""
    _proj = ["workflow_id", "applicant_id", "current_state"]
    try:
        items = workflow_table.query(
            Key("applicant_id").eq(applicant_id),
            index_name="applicant-created-index",
            scan_index_forward=False,
            projection=_proj,
        )
    except ClientError:
        items = workflow_table.scan(
            filter_expression=Attr("applicant_id").eq(applicant_id),
            projection=_proj,
        )
    for item in items:
        if item.get("current_state") not in _TERMINAL_STATES:
            return normalize_dynamodb_value(item)
    return None


def get_job_by_workflow_id(workflow_id: str) -> Optional[Dict[str, Any]]:
    item = workflow_table.get_item({"workflow_id": _normalize_workflow_id(workflow_id)})
    return normalize_dynamodb_value(item) if item is not None else None


# ── Decision traces ───────────────────────────────────────────────────────────

def get_prior_decision_traces(enterprise_id: str) -> List[Dict[str, Any]]:
    from core_data.repositories.applicant_repository import ApplicantRepository
    applicants = ApplicantRepository().list_by_enterprise_identifier(enterprise_id)
    applicant_ids = [a.applicant_id for a in applicants if getattr(a, "applicant_id", None)]
    if not applicant_ids:
        return []

    def _query_workflows(applicant_id: str) -> List[Dict[str, Any]]:
        return workflow_table.query(
            Key("applicant_id").eq(applicant_id),
            index_name="applicant-created-index",
        )

    with ThreadPoolExecutor(max_workers=min(10, len(applicant_ids))) as pool:
        workflow_results = list(pool.map(_query_workflows, applicant_ids))

    workflows = [wf for sublist in workflow_results for wf in sublist]

    def _query_tasks(wf: Dict[str, Any]) -> List[Dict[str, Any]]:
        wid = wf.get("workflow_id")
        return approval_tasks_table.query(Key("workflow_id").eq(wid)) if wid else []

    task_results: List[List[Dict[str, Any]]] = []
    if workflows:
        with ThreadPoolExecutor(max_workers=min(10, len(workflows))) as pool:
            task_results = list(pool.map(_query_tasks, workflows))

    traces: List[Dict[str, Any]] = []
    for wf, tasks in zip(workflows, task_results):
        workflow_id = wf.get("workflow_id")
        if not workflow_id:
            continue
        for task in tasks:
            if task.get("status") == "COMPLETED":
                continue
            traces.append({
                "workflow_id":      workflow_id,
                "gate_check":       task.get("gate_check"),
                "response_payload": task.get("response_payload", {}),
                "current_state":    wf.get("current_state"),
            })
    logger.info("get_prior_decision_traces enterprise_id=%s workflow_count=%d trace_count=%d", enterprise_id, len(workflows), len(traces))
    return traces


# ── Orchestrator job ops (itek_workflow_applicant_request table) ──────────────

def create_job(request_id: str) -> None:
    now = iso_now()
    try:
        workflow_request_table.put_item({
            "request_id":  request_id,
            "status":      "pending",
            "applicant_id": None,
            "result":      None,
            "steps":       [],
            "error":       None,
            "created_at":  now,
            "updated_at":  now,
        })
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            logger.warning("Workflow tables do not exist. Attempting to initialize...")
            from workflow_engine.db.dynamodb.db_init import create_tables
            create_tables()
            workflow_request_table.put_item({
                "request_id":  request_id,
                "status":      "pending",
                "applicant_id": None,
                "result":      None,
                "steps":       [],
                "error":       None,
                "created_at":  now,
                "updated_at":  now,
            })
        else:
            raise
    logger.info("Inserted workflow job request_id=%s", request_id)


def get_job(request_id: str) -> Optional[Dict[str, Any]]:
    _proj = ["request_id", "status", "applicant_id", "result", "steps", "error", "workflow_id", "hitl_step"]
    item = workflow_request_table.get_item({"request_id": request_id}, projection=_proj)
    return normalize_dynamodb_value(item) if item is not None else None


def update_job_applicant_id(request_id: str, applicant_id: str) -> None:
    workflow_request_table.update_item(
        {"request_id": request_id},
        {"applicant_id": applicant_id, "updated_at": iso_now()},
    )
    logger.info("Updated workflow job applicant_id request_id=%s applicant_id=%s", request_id, applicant_id)


def mark_job_done(request_id: str, applicant_id: Optional[str], result: str, steps: list) -> None:
    workflow_request_table.update_item(
        {"request_id": request_id},
        {
            "status":       "done",
            "applicant_id": applicant_id,
            "result":       result,
            "steps":       steps,
            "updated_at":  iso_now(),
        },
    )
    logger.info("Updated workflow job done request_id=%s applicant_id=%s", request_id, applicant_id or "none")


def get_job_by_applicant_id(applicant_id: str) -> Optional[Dict[str, Any]]:
    if not applicant_id:
        return None
    applicant_id = applicant_id.strip() if isinstance(applicant_id, str) else applicant_id
    _proj = ["request_id", "status", "applicant_id", "workflow_id", "result", "steps", "error", "hitl_step"]
    try:
        items = workflow_request_table.query(
            Key("applicant_id").eq(applicant_id),
            index_name="applicant-created-index",
            limit=1,
            scan_index_forward=False,
            projection=_proj,
        )
    except ClientError:
        items = workflow_request_table.scan(
            filter_expression=Attr("applicant_id").eq(applicant_id),
            projection=_proj,
        )
    return normalize_dynamodb_value(items[0]) if items else None


def append_job_step(request_id: str, step: dict) -> None:
    item = workflow_request_table.get_item({"request_id": request_id})
    if not item:
        return
    steps = item.get("steps") or []
    steps.append(step)
    workflow_request_table.update_item(
        {"request_id": request_id},
        {"steps": steps, "updated_at": iso_now()},
    )
    logger.debug("Appended step request_id=%s status=%s", request_id, step.get("status"))


def mark_job_error(request_id: str, error: str) -> None:
    workflow_request_table.update_item(
        {"request_id": request_id},
        {"status": "error", "error": error, "updated_at": iso_now()},
    )
    logger.info("Updated workflow job error request_id=%s", request_id)


def mark_job_hitl_pending(
    request_id: str,
    step_id: str,
    step_label: str,
    error: str,
    orch_state: dict | None = None,
) -> None:
    workflow_request_table.update_item(
        {"request_id": request_id},
        {
            "status": "hitl_pending",
            "hitl_step": {
                "request_id": request_id,
                "step_id":    step_id,
                "label":      step_label,
                "error":      error,
                "orch_state": orch_state or {},
            },
            "updated_at": iso_now(),
        },
    )
    logger.info("Marked workflow job hitl_pending request_id=%s step_id=%s", request_id, step_id)


def update_job_workflow_id(request_id: str, workflow_id: str) -> None:
    workflow_request_table.update_item(
        {"request_id": request_id},
        {"workflow_id": workflow_id, "updated_at": iso_now()},
    )
    logger.info("Updated workflow job workflow_id request_id=%s workflow_id=%s", request_id, workflow_id)


# ── Checkpoint nodes that require persistence for interrupt/resume ────────────
_INTERRUPT_NODES = frozenset({"await_webhook", "human_review"})
_MAX_SKIPPED_CHECKPOINT_CACHE = 10_000


class WorkflowCheckpointSaver(BaseCheckpointSaver):
    """DynamoDB-backed LangGraph checkpoint saver for workflow threads."""

    def __init__(self) -> None:
        super().__init__(serde=JsonPlusSerializer())
        self._skipped_checkpoints: set[str] = set()

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        thread_id     = get_thread_id(config)
        checkpoint_id = config["configurable"].get("checkpoint_id")

        if checkpoint_id:
            row = checkpoints_table.get_item({"thread_id": thread_id, "checkpoint_id": checkpoint_id})
        else:
            rows = checkpoints_table.query(
                Key("thread_id").eq(thread_id),
                index_name="thread-time-index",
                scan_index_forward=False,
                limit=1,
            )
            if not rows:
                return None
            row = rows[0]
            checkpoint_id = row.get("checkpoint_id")

        if not row:
            return None

        write_rows = sorted(
            checkpoint_writes_table.query(
                Key("thread_id").eq(thread_id)
                & Key(CHECKPOINT_WRITE_SORT_KEY).begins_with(f"{checkpoint_id}#")
            ),
            key=lambda wr: wr.get("idx", 0),
        )

        checkpoint: Checkpoint = self.serde.loads_typed(
            (row["checkpoint_type"], bytes(row["checkpoint_data"]))
        )
        metadata: CheckpointMetadata = normalize_dynamodb_value(
            row.get("metadata", {}) if isinstance(row.get("metadata", {}), dict) else {}
        )
        config_out: RunnableConfig = {
            **config,
            "configurable": {
                **config.get("configurable", {}),
                "thread_id":     thread_id,
                "checkpoint_id": row["checkpoint_id"],
                "checkpoint_ns": "",
            },
        }
        parent_config: Optional[RunnableConfig] = None
        if row.get("parent_checkpoint_id"):
            parent_config = {
                **config,
                "configurable": {
                    **config.get("configurable", {}),
                    "thread_id":     thread_id,
                    "checkpoint_id": row["parent_checkpoint_id"],
                    "checkpoint_ns": "",
                },
            }
        pending_writes = [
            (wr["task_id"], wr["channel"], self.serde.loads_typed((wr["value_type"], bytes(wr["value"]))))
            for wr in write_rows
        ]
        return CheckpointTuple(
            config=config_out,
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending_writes,
        )

    def list(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        if not config:
            return
        thread_id = get_thread_id(config)
        rows = checkpoints_table.query(
            Key("thread_id").eq(thread_id),
            index_name="thread-time-index",
            scan_index_forward=False,
            **({"limit": limit} if limit is not None else {}),
        )
        for row in rows:
            checkpoint = self.serde.loads_typed((row["checkpoint_type"], bytes(row["checkpoint_data"])))
            metadata = normalize_dynamodb_value(
                row.get("metadata", {}) if isinstance(row.get("metadata", {}), dict) else {}
            )
            config_out: RunnableConfig = {
                **config,
                "configurable": {
                    **config.get("configurable", {}),
                    "thread_id":     thread_id,
                    "checkpoint_id": row["checkpoint_id"],
                    "checkpoint_ns": "",
                },
            }
            parent_config = None
            if row.get("parent_checkpoint_id"):
                parent_config = {
                    **config,
                    "configurable": {
                        **config.get("configurable", {}),
                        "thread_id":     thread_id,
                        "checkpoint_id": row["parent_checkpoint_id"],
                        "checkpoint_ns": "",
                    },
                }
            yield CheckpointTuple(
                config=config_out,
                checkpoint=checkpoint,
                metadata=metadata,
                parent_config=parent_config,
            )

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id            = get_thread_id(config)
        checkpoint_id        = checkpoint["id"]
        parent_checkpoint_id = config["configurable"].get("checkpoint_id")

        config_out: RunnableConfig = {
            **config,
            "configurable": {
                **config.get("configurable", {}),
                "thread_id":     thread_id,
                "checkpoint_id": checkpoint_id,
                "checkpoint_ns": "",
            },
        }

        # Skip non-interrupt loop checkpoints for approval-workflow threads.
        # OrchestratorGraph (orch_*) threads always persist — any node may interrupt.
        if metadata.get("source") == "loop" and not thread_id.startswith("orch_"):
            if not (set(metadata.get("writes") or {}) & _INTERRUPT_NODES):
                if len(self._skipped_checkpoints) >= _MAX_SKIPPED_CHECKPOINT_CACHE:
                    self._skipped_checkpoints.clear()
                self._skipped_checkpoints.add(checkpoint_id)
                logger.debug("Skipping intermediate checkpoint id=%s", checkpoint_id)
                return config_out

        checkpoint_type, checkpoint_data = self.serde.dumps_typed(checkpoint)
        item = {
            "thread_id":      thread_id,
            "checkpoint_id":  checkpoint_id,
            "checkpoint_data": checkpoint_data,
            "checkpoint_type": checkpoint_type,
            "metadata":       _sanitize_dynamodb_map_keys(normalize_dynamodb_value(metadata)),
            "created_at":     iso_now(),
        }
        if parent_checkpoint_id is not None:
            item["parent_checkpoint_id"] = parent_checkpoint_id
        checkpoints_table.put_item(_sanitize_dynamodb_map_keys(normalize_dynamodb_value(item)))
        return config_out

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
    ) -> None:
        checkpoint_id = config["configurable"]["checkpoint_id"]
        if checkpoint_id in self._skipped_checkpoints:
            return
        thread_id = get_thread_id(config)
        items = [
            {
                "thread_id":    thread_id,
                "checkpoint_id": checkpoint_id,
                "task_id":      task_id,
                "idx":          idx,
                "channel":      channel,
                "value":        serialized,
                "value_type":   value_type,
                CHECKPOINT_WRITE_SORT_KEY: f"{checkpoint_id}#{idx:06d}",
            }
            for idx, (channel, value) in enumerate(writes)
            for value_type, serialized in [self.serde.dumps_typed(value)]
        ]
        checkpoint_writes_table.batch_put_items(items)

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        return await asyncio.to_thread(self.get_tuple, config)

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
    ) -> None:
        return await asyncio.to_thread(self.put_writes, config, writes, task_id)

    async def alist(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        tuples = await asyncio.to_thread(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for t in tuples:
            yield t
