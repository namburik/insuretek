"""Workflow orchestrator — loads a YAML or JSON workflow definition and executes
steps sequentially. Each step is routed to the right specialized agent by the
Intent resolver — no agent: declaration needed in the YAML.

Step schema (YAML):
  id:             unique step identifier
  label:          display name for results table
  category:       optional section header (printed when it changes)
  prompt:         prompt template; use {{ state_key }} to interpolate state
  data:           optional dict merged with state before template rendering
  data_file:      optional path to a JSON file (relative to the workflow file) merged like data:
  skip_if_missing: list of state keys; step is skipped if any are absent/None
  extract_state:  dict of  state_key -> extraction spec:
                    "uuid"             — first UUID found in response
                    "grp_pattern"      — first grp_<12hex> token
                    "price_pattern"    — first price_<16hex> token
                    "client_pattern"   — first client_<16hex> token
                    {"regex": "<pat>"} — first capture group of custom regex
                    {"json_key": "k"}  — value of key k in first JSON object
  eval_type:      "create" | "query" | "update" | None  (passed to evaluate_agent_response)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal
from mltek.common.infra import load_config, extract_state_value
from mltek.common.log import setup_structured_logging
from mltek.policy.metrics.evaluation import eval_step, quality_label

logger = setup_structured_logging(__name__)

_STATUS_PASS = "PASS"
_STATUS_FAIL = "FAIL"
_STATUS_SKIP = "SKIP"

_PATTERNS = {
    "uuid":           r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    "price_pattern":  r"price_[0-9a-fA-F]{16}",
    "client_pattern": r"client_[0-9a-fA-F]{16}",
}


def _load_workflow(path: str | Path) -> dict:
    return load_config(path)


def _render(template: str, context: dict) -> str:
    """Replace {{ key }} placeholders; unknown keys are left unchanged."""
    def replace(m):
        key = m.group(1).strip()
        val = context.get(key)
        if val is None:
            return m.group(0)
        return json.dumps(val, indent=2) if isinstance(val, (dict, list)) else str(val)
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", replace, template)




class WorkflowOrchestrator:
    """Execute a YAML/JSON workflow definition step-by-step via the resolver.

    Generic workflow executor: loads workflow definitions, renders prompts with state,
    routes each step to agents, extracts state from responses, evaluates quality.
    Halts on critical state key missing (domain-specific key provided by caller).

    Args:
        workflow_path: Path to the YAML/JSON workflow definition (relative to caller's directory).
        prompts: Optional dict mapping step id → prompt string. Overrides workflow prompts.
        step_data: Optional dict mapping step id → data dict. Merges with step data.
        only: Optional set of step IDs to execute; skip others.
        on_state_update: Optional async callback(state_key, value) on state change.
        on_step_complete: Optional async callback(step_result) after each step.
        source: Source identifier for first step (e.g. "react", "api"); subsequent steps are "internal".
        env: Environment ("prod", "dev", etc.) passed to resolver.
        critical_state_key: If set, halt workflow if this key is in extract_spec but missing after a step.
    """

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
        critical_state_key: str | None = None,
    ):
        if workflow_path is None:
            raise ValueError("workflow_path is required")
        workflow_path = Path(workflow_path)
        if not workflow_path.exists():
            raise FileNotFoundError(f"Workflow file not found: {workflow_path}")
        raw = _load_workflow(workflow_path)
        wf = raw.get("workflow", raw)
        self.name: str = wf.get("name", workflow_path.stem)
        self.steps: list[dict] = wf.get("steps", [])
        self._workflow_dir: Path = workflow_path.parent
        self.prompts: dict[str, str] = prompts or {}
        self.step_data: dict[str, dict] = step_data or {}
        self.only: set[str] | None = set(only) if only is not None else None
        self.env: str = env
        self.on_state_update = on_state_update
        self.on_step_complete = on_step_complete
        self.source = source
        self.state: dict[str, Any] = {}
        self.results: list[dict] = []
        self.last_response: str = ""
        self.critical_state_key: str | None = critical_state_key

    async def run(self, request_id: str | None = None, session_id: str | None = None, correlation_id: str | None = None) -> bool:
        """Execute all steps in order. Returns True when no step failed."""
        from mltek.common.router.resolver import call_resolver
        from mltek.common.infra import extract_result_content
        from mltek.config import config as common_config
        from mltek.common.infra.context import set_jit_session, set_request_id, set_audit_session_id, CorrelationContext
        set_jit_session("done", self.name)
        if request_id:
            set_request_id(request_id)
        if session_id:
            set_audit_session_id(session_id)
        if correlation_id:
            CorrelationContext.set(correlation_id)
        else:
            correlation_id = CorrelationContext.get_or_generate()
        logger.info("Starting workflow: %s | model=%s base_url=%s mcp=%s", self.name, common_config.LLM_MODEL, common_config.OLLAMA_BASE_URL, common_config.MCP_CORE_URL)

        _last_category = None

        step_index = 0
        for step in self.steps:
            step_id      = step.get("id", "?")
            if self.only is not None and step_id not in self.only:
                continue
            label        = step.get("label", step_id)
            category     = step.get("category")
            eval_type    = step.get("eval_type")
            forced_agent = step.get("agent")
            skip_keys    = step.get("skip_if_missing", [])
            extract_spec = step.get("extract_state", {})
            step_data    = step.get("data", {})
            data_file    = step.get("data_file")
            if data_file:
                file_data = json.loads((self._workflow_dir / data_file).read_text())
                step_data = {**file_data, **step_data}
            if step_id in self.step_data:
                step_data = {**self.step_data[step_id], **step_data}

            if category and category != _last_category:
                logger.info("Processing category: %s", category)
                _last_category = category

            missing = [k for k in skip_keys if not self.state.get(k)]
            if missing:
                await self._record(label, _STATUS_SKIP, f"missing state: {', '.join(missing)}", logger.warning, None)
                continue

            prompt = _render(
                self.prompts.get(step_id) or step.get("prompt", ""),
                {**self.state, **step_data},
            )

            step_source = self.source if step_index == 0 else "internal"
            step_index += 1
            try:
                result = await call_resolver(prompt, forced_agent=forced_agent, source=step_source, env=self.env, session_id=session_id or "", correlation_id=correlation_id or "")
                answer = extract_result_content(result) if isinstance(result, dict) else str(result)
                self.last_response = answer

                eval_score = eval_step(answer, eval_type)

                answer_str = str(answer)
                for state_key, spec in extract_spec.items():
                    val = extract_state_value(answer_str, spec)
                    if val and state_key == "child_group_id":
                        parent = self.state.get("parent_group_id")
                        pattern = _PATTERNS.get(spec, spec) if isinstance(spec, str) else spec.get("regex", "")
                        for candidate in re.findall(pattern, answer_str, re.IGNORECASE):
                            if candidate != parent:
                                val = candidate
                                break
                    if val:
                        self.state[state_key] = val
                        if self.on_state_update:
                            await self.on_state_update(state_key, val)

                # If a critical state key was expected but not produced, halt workflow.
                if self.critical_state_key and self.critical_state_key in extract_spec and not self.state.get(self.critical_state_key):
                    note = answer_str[:300].replace("\n", " ") if answer_str else f"{self.critical_state_key} not found in response"
                    await self._record(label, _STATUS_FAIL, f"Workflow Halted — {note}", logger.error, None)
                    logger.warning("Halting orchestrator: %s unavailable after step %r: %s", self.critical_state_key, step_id, note)
                    return self._summary()

                _grd = result.get("guardian") if isinstance(result, dict) else None
                _ref = result.get("reflection") if isinstance(result, dict) else None
                if _grd and _grd.get("allowed") is False and not (_ref and _ref.get("allowed")):
                    await self._record(label, _STATUS_FAIL, answer[:100].replace("\n", " "), logger.error, eval_score)
                    continue

                if eval_score is not None and eval_score < 0.5:
                    await self._record(label, _STATUS_FAIL, answer[:100].replace("\n", " "), logger.warning, eval_score)
                    continue

                await self._record(label, _STATUS_PASS, answer[:100].replace("\n", " "), logger.info, eval_score)

            except Exception as e:
                logger.error("Step %r raised an exception", step_id, exc_info=True)
                await self._record(label, _STATUS_FAIL, str(e), logger.error, None)

        return self._summary()

    async def _record(
        self,
        label: str,
        status: Literal["PASS", "FAIL", "SKIP"],
        note: str,
        log_fn: Callable,
        eval_score: float | None,
    ) -> None:
        if eval_score is not None:
            log_fn("%-4s  %s  [%.0f%%]  %s", status, label, eval_score * 100, note,
                   extra={"step": label, "status": status, "eval_score": eval_score})
        else:
            log_fn("%-4s  %s  %s", status, label, note,
                   extra={"step": label, "status": status, "eval_score": None})
        record = {"label": label, "status": status, "note": note, "eval_score": eval_score}
        self.results.append(record)
        if self.on_step_complete:
            try:
                await self.on_step_complete(record)
            except Exception:
                logger.warning("on_step_complete failed for step %r", label, exc_info=True)

    def _summary(self) -> bool:
        passed = failed = skipped = 0
        eval_scores: list[float] = []
        for r in self.results:
            s = r["status"]
            if s == _STATUS_PASS:
                passed += 1
            elif s == _STATUS_FAIL:
                failed += 1
            elif s == _STATUS_SKIP:
                skipped += 1
            if r.get("eval_score") is not None:
                eval_scores.append(r["eval_score"])
        total = len(self.results)

        logger.info("Workflow complete: %d passed | %d failed | %d skipped (total %d)", passed, failed, skipped, total, extra={"passed": passed, "failed": failed, "skipped": skipped, "total": total})

        if eval_scores:
            avg_score = sum(eval_scores) / len(eval_scores)
            quality = quality_label(avg_score)
            logger.info("Response quality: %s  avg=%.0f%%", quality, avg_score * 100, extra={"quality": quality, "avg_eval_score": avg_score})

        if failed:
            logger.error("Failed steps:")
            for r in self.results:
                if r["status"] == _STATUS_FAIL:
                    logger.error("  %s: %s", r["label"], r["note"])

        return failed == 0
