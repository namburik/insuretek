"""LangGraph StateGraph wrapper around workflow orchestrator with checkpointing and HITL.

Domain-specific integration: requires checkpointer, BFF_SERVICE_URL, and critical_state_key
provided by the domain application (e.g., ApplicantWorkflowSaver, InsureTekConfig, "applicant_id").
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from typing_extensions import TypedDict

from mltek.agents.bridge import (
    Command, END, START, StateGraph,
    GraphInterrupt, interrupt, make_thread_config, resume_graph,
)
from mltek.policy.metrics.evaluation import eval_step
from mltek.common.infra import extract_state_value
from mltek.common.log import setup_structured_logging
from insuretek.config.config import InsureTekConfig
from mltek.orchestration.workflow_orchestrator import (
    _load_workflow, _render, _PATTERNS,
    _STATUS_FAIL, _STATUS_PASS, _STATUS_SKIP,
)

logger = setup_structured_logging(__name__)

_ABORT_KEY = "__abort__"


class OrchestratorState(TypedDict, total=False):
    request_id: str
    session_id: str
    correlation_id: str
    state: dict
    results: list
    last_response: str
    __abort__: bool


def _make_step_node(
    step_cfg: dict,
    *,
    workflow_dir: Path,
    prompts: dict[str, str],
    step_data_overrides: dict[str, dict],
    on_state_update: Callable[[str, str], Awaitable[None]] | None,
    on_step_complete: Callable[[dict], Awaitable[None]] | None,
    source: str,
    env: str,
    job_repo,
) -> Callable:
    step_id      = step_cfg.get("id", "?")
    label        = step_cfg.get("label", step_id)
    eval_type    = step_cfg.get("eval_type")
    forced_agent = step_cfg.get("agent")
    skip_keys    = step_cfg.get("skip_if_missing", [])
    extract_spec = step_cfg.get("extract_state", {})
    base_data    = step_cfg.get("data", {})
    data_file    = step_cfg.get("data_file")

    async def _node(orch_state: dict) -> dict:
        from mltek.common.router.resolver import call_resolver
        from mltek.common.infra import extract_result_content
        from mltek.common.infra.context import (
            set_jit_session, set_request_id, set_audit_session_id, CorrelationContext,
        )

        if orch_state.get(_ABORT_KEY):
            return {}

        request_id     = orch_state.get("request_id", "")
        session_id     = orch_state.get("session_id", "")
        correlation_id = orch_state.get("correlation_id", "")
        current_state  = dict(orch_state.get("state") or {})
        results        = list(orch_state.get("results") or [])

        set_jit_session("done", "orchestrator_graph")
        if request_id:
            set_request_id(request_id)
        if session_id:
            set_audit_session_id(session_id)
        if correlation_id:
            CorrelationContext.set(correlation_id)

        missing = [k for k in skip_keys if not current_state.get(k)]
        if missing:
            rec = {"label": label, "status": _STATUS_SKIP, "note": f"missing state: {', '.join(missing)}", "eval_score": None}
            results.append(rec)
            if on_step_complete:
                try:
                    await on_step_complete(rec)
                except Exception:
                    logger.warning("on_step_complete failed step=%r", label, exc_info=True)
            logger.info("SKIP %s missing=%s request_id=%s", label, missing, request_id)
            return {"results": results}

        # runtime overrides win over data_file over YAML base
        step_data = dict(base_data)
        if data_file:
            file_data = json.loads((workflow_dir / data_file).read_text())
            step_data = {**file_data, **step_data}
        if step_id in step_data_overrides:
            step_data = {**step_data, **step_data_overrides[step_id]}

        prompt = _render(
            prompts.get(step_id) or step_cfg.get("prompt", ""),
            {**current_state, **step_data},
        )

        async def _execute():
            result = await call_resolver(
                prompt,
                forced_agent=forced_agent,
                source="internal",
                env=env,
                session_id=session_id,
                correlation_id=correlation_id,
            )
            answer = extract_result_content(result) if isinstance(result, dict) else str(result)
            score  = eval_step(answer, eval_type)
            return answer, score, result if isinstance(result, dict) else {}

        # interrupt on failure for HITL; resume accepts "retry" or "skip"
        try:
            answer, eval_score, raw_result = await _execute()
        except Exception as exc:
            logger.error("Step %r exception request_id=%s", step_id, request_id, exc_info=True)
            if job_repo and request_id:
                try:
                    await asyncio.to_thread(job_repo.mark_hitl_pending, request_id, step_id, label, str(exc), orch_state=current_state)
                except Exception:
                    logger.warning("mark_hitl_pending failed step=%r", step_id, exc_info=True)
            _base = f"{InsureTekConfig.BFF_SERVICE_URL}/webhooks/orchestrator/{request_id}/resume"
            logger.warning("HITL suspended step=%r request_id=%s retry_url=%s skip_url=%s", step_id, request_id, f"{_base}?decision=retry", f"{_base}?decision=skip")
            hitl = interrupt({"step_id": step_id, "label": label, "error": str(exc)})
            decision = (hitl or {}).get("decision", "retry")
            if decision == "skip":
                rec = {"label": label, "status": _STATUS_SKIP, "note": f"HITL skip: {exc}", "eval_score": None}
                results.append(rec)
                if on_step_complete:
                    try:
                        await on_step_complete(rec)
                    except Exception:
                        pass
                logger.info("SKIP %s HITL skip decision request_id=%s", label, request_id)
                return {"results": results}
            try:
                answer, eval_score, raw_result = await _execute()
            except Exception as exc2:
                logger.error("Retry of step %r also failed request_id=%s", step_id, request_id, exc_info=True)
                if job_repo and request_id:
                    try:
                        await asyncio.to_thread(job_repo.mark_hitl_pending, request_id, step_id, label, str(exc2), orch_state=current_state)
                    except Exception:
                        pass
                from mltek.config import config as app_config
                _base = f"{app_config.BFF_SERVICE_URL}/webhooks/orchestrator/{request_id}/resume"
                logger.warning("HITL suspended (retry 2) step=%r request_id=%s retry_url=%s skip_url=%s", step_id, request_id, f"{_base}?decision=retry", f"{_base}?decision=skip")
                hitl2 = interrupt({"step_id": step_id, "label": label, "error": str(exc2), "retry_count": 2})
                decision2 = (hitl2 or {}).get("decision", "skip")
                if decision2 == "skip":
                    rec = {"label": label, "status": _STATUS_SKIP, "note": f"HITL skip after 2nd failure: {exc2}", "eval_score": None}
                    results.append(rec)
                    if on_step_complete:
                        try:
                            await on_step_complete(rec)
                        except Exception:
                            pass
                    return {"results": results}
                try:
                    answer, eval_score, raw_result = await _execute()
                except Exception as exc3:
                    rec = {"label": label, "status": _STATUS_FAIL, "note": str(exc3), "eval_score": None}
                    results.append(rec)
                    if on_step_complete:
                        try:
                            await on_step_complete(rec)
                        except Exception:
                            pass
                    logger.error("Step %r permanently failed request_id=%s", step_id, request_id)
                    return {"results": results, _ABORT_KEY: True}

        # interrupt here so resume retries only this node, not the full graph
        if eval_score is not None and eval_score < 0.5:
            error_msg = f"low eval score {eval_score:.2f}"
            logger.warning("Step %r low eval score=%.2f request_id=%s", step_id, eval_score, request_id)
            if job_repo:
                try:
                    await asyncio.to_thread(job_repo.mark_hitl_pending, request_id, step_id, label, error_msg, orch_state=current_state)
                except Exception:
                    logger.warning("mark_hitl_pending failed step=%r", step_id, exc_info=True)
            from mltek.config import config as app_config
            base = f"{app_config.BFF_SERVICE_URL}/webhooks/orchestrator/{request_id}/resume"
            logger.warning("HITL suspended step=%r request_id=%s retry_url=%s skip_url=%s", step_id, request_id, f"{base}?decision=retry", f"{base}?decision=skip")
            hitl = interrupt({"step_id": step_id, "label": label, "error": error_msg, "eval_score": eval_score})
            decision = (hitl or {}).get("decision", "retry")
            if decision == "skip":
                rec = {"label": label, "status": _STATUS_SKIP, "note": f"HITL skip: {error_msg}", "eval_score": eval_score}
                results.append(rec)
                if on_step_complete:
                    try:
                        await on_step_complete(rec)
                    except Exception:
                        pass
                logger.info("SKIP %s HITL skip (low eval score) request_id=%s", label, request_id)
                return {"results": results}
            try:
                answer, eval_score, raw_result = await _execute()
            except Exception as exc_r:
                rec = {"label": label, "status": _STATUS_FAIL, "note": str(exc_r)[:100], "eval_score": None}
                results.append(rec)
                if on_step_complete:
                    try:
                        await on_step_complete(rec)
                    except Exception:
                        pass
                logger.error("Step %r retry failed after low eval score request_id=%s", step_id, request_id)
                return {"results": results, _ABORT_KEY: True}

        answer_str = str(answer)

        for state_key, spec in extract_spec.items():
            val = extract_state_value(answer_str, spec)
            if val and state_key == "child_group_id":
                parent = current_state.get("parent_group_id")
                pattern = _PATTERNS.get(spec, spec) if isinstance(spec, str) else spec.get("regex", "")
                for candidate in re.findall(pattern, answer_str, re.IGNORECASE):
                    if candidate != parent:
                        val = candidate
                        break
            if val:
                current_state[state_key] = val
                if on_state_update:
                    try:
                        await on_state_update(state_key, val)
                    except Exception:
                        logger.warning("on_state_update failed key=%r", state_key, exc_info=True)

        # halt if applicant_id wasn't extracted — this step was expected to produce it
        if "applicant_id" in extract_spec and not current_state.get("applicant_id"):
            note = answer_str[:300].replace("\n", " ")
            rec  = {"label": label, "status": _STATUS_FAIL, "note": f"Workflow Halted — {note}", "eval_score": None}
            results.append(rec)
            if on_step_complete:
                try:
                    await on_step_complete(rec)
                except Exception:
                    pass
            logger.warning("Halting orchestrator: applicant_id unavailable after step %r request_id=%s", step_id, request_id)
            return {"results": results, "state": current_state, "last_response": answer_str, _ABORT_KEY: True}

        # fail only if guardian blocked and reflection didn't override
        _grd = raw_result.get("guardian") if raw_result else None
        _ref = raw_result.get("reflection") if raw_result else None
        if _grd and _grd.get("allowed") is False and not (_ref and _ref.get("allowed")):
            rec = {"label": label, "status": _STATUS_FAIL, "note": answer_str[:100].replace("\n", " "), "eval_score": eval_score}
        else:
            rec = {"label": label, "status": _STATUS_PASS, "note": answer_str[:100].replace("\n", " "), "eval_score": eval_score}

        results.append(rec)
        if on_step_complete:
            try:
                await on_step_complete(rec)
            except Exception:
                logger.warning("on_step_complete failed step=%r", label, exc_info=True)

        logger.info("%-4s %s request_id=%s eval_score=%s", rec["status"], label, request_id, eval_score)
        return {"results": results, "state": current_state, "last_response": answer_str}

    _node.__name__ = step_id
    return _node


class OrchestratorGraph:
    """LangGraph wrapper for workflow orchestration with checkpointing and HITL. Thread IDs use ``orch_`` prefix to avoid checkpoint collisions."""

    def __init__(
        self,
        workflow_path: str | Path | None = None,
        prompts: dict[str, str] | None = None,
        step_data: dict[str, dict] | None = None,
        only: set[str] | list[str] | None = None,
        on_state_update: Callable[[str, str], Awaitable[None]] | None = None,
        on_step_complete: Callable[[dict], Awaitable[None]] | None = None,
        source: str = "unknown",
        env: str = "prod",
        job_repo=None,
    ) -> None:
        if workflow_path is None:
            workflow_path = Path(__file__).resolve().parents[1] / "config" / "orchestrator_config.yaml"
        workflow_path = Path(workflow_path)
        raw = _load_workflow(workflow_path)
        wf = raw.get("workflow", raw)
        self.name: str = wf.get("name", workflow_path.stem)
        all_steps: list[dict] = wf.get("steps", [])
        self._workflow_dir = workflow_path.parent
        self._only: set[str] | None = set(only) if only is not None else None
        self._prompts = prompts or {}
        self._step_data = step_data or {}
        self._on_state_update = on_state_update
        self._on_step_complete = on_step_complete
        self.source = source
        self.env = env
        self._job_repo = job_repo

        self._steps = [s for s in all_steps if self._only is None or s.get("id") in self._only]
        self._graph = self._build_graph()

    def _build_graph(self):
        from workflow_engine.repository.workflow_checkpointer import ApplicantWorkflowSaver
        builder = StateGraph(OrchestratorState)
        node_names: list[str] = []

        for step_cfg in self._steps:
            step_id = step_cfg.get("id", "?")
            node_fn = _make_step_node(
                step_cfg,
                workflow_dir=self._workflow_dir,
                prompts=self._prompts,
                step_data_overrides=self._step_data,
                on_state_update=self._on_state_update,
                on_step_complete=self._on_step_complete,
                source=self.source,
                env=self.env,
                job_repo=self._job_repo,
            )
            builder.add_node(step_id, node_fn)
            node_names.append(step_id)

        if not node_names:
            builder.add_node("__noop__", lambda s: s)
            builder.add_edge(START, "__noop__")
            builder.add_edge("__noop__", END)
        else:
            builder.add_edge(START, node_names[0])
            for a, b in zip(node_names, node_names[1:]):
                builder.add_edge(a, b)
            builder.add_edge(node_names[-1], END)

        return builder.compile(checkpointer=ApplicantWorkflowSaver())

    def _thread_id(self, request_id: str) -> str:
        return f"orch_{request_id}"

    async def run(self, request_id: str, session_id: str = "", correlation_id: str = "", initial_state: dict | None = None) -> bool:
        config = make_thread_config(self._thread_id(request_id))
        initial: dict = {
            "state":          initial_state or {},
            "results":        [],
            "last_response":  "",
            "request_id":     request_id,
            "session_id":     session_id,
            "correlation_id": correlation_id,
            _ABORT_KEY:       False,
        }
        try:
            final = await self._graph.ainvoke(initial, config=config)
        except GraphInterrupt:
            # return False (not raise) so caller can still trigger initiate_workflow if applicant_id was captured
            logger.info("OrchestratorGraph suspended for HITL request_id=%s", request_id)
            return False
        results = final.get("results") or []
        failed  = sum(1 for r in results if r.get("status") == _STATUS_FAIL)
        logger.info("OrchestratorGraph run exit request_id=%s failed=%d total=%d", request_id, failed, len(results))
        return failed == 0

    async def resume(self, request_id: str, decision: str) -> bool:
        config = make_thread_config(self._thread_id(request_id))
        try:
            final = await resume_graph(self._graph, {"decision": decision}, config)
        except GraphInterrupt:
            logger.info("OrchestratorGraph re-suspended for HITL request_id=%s decision=%s", request_id, decision)
            return False
        results = final.get("results") or []
        failed  = sum(1 for r in results if r.get("status") == _STATUS_FAIL)
        logger.info("OrchestratorGraph resume exit request_id=%s decision=%s failed=%d", request_id, decision, failed)
        return failed == 0
