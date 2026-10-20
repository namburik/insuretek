"""Semantic synthesis policy — post-agent LLM narrative from tool output in the semantic layer."""

from __future__ import annotations

import asyncio
from pathlib import Path

from mltek.common.log import setup_structured_logging
from mltek.policy.output.synthesis_policy import SynthesisPolicyBase

logger = setup_structured_logging(__name__)

_ISAR_PATH = Path(__file__).parent.parent / "context" / "insuretek_isar.md"


def _load_isar_reference() -> str:
    try:
        return _ISAR_PATH.read_text().strip()
    except FileNotFoundError:
        logger.warning("insuretek_isar.md not found — synthesis will run without ISAR domain rules")
        return ""


def _parse_isar_version(isar_text: str) -> str:
    for line in isar_text.splitlines():
        if line.startswith("ISAR-VERSION:"):
            return line.split(":", 1)[1].strip()
    return "unknown"


def _build_registries() -> tuple[dict[str, str], dict[str, str], str]:
    from insuretek.common.agents.prompts import ISAR_SYSTEM_PROMPT
    isar_ref = _load_isar_reference()
    narrative_prompt = ISAR_SYSTEM_PROMPT.format(isar_reference=isar_ref)
    evidence_prompt = narrative_prompt + "\n\nEXTRACT EVIDENCE CHAIN ONLY — do not write narrative prose."
    return (
        {"isar_explain_applicant": narrative_prompt},
        {"isar_explain_applicant": evidence_prompt},
        _parse_isar_version(isar_ref),
    )


_NARRATIVE_REGISTRY, _EVIDENCE_REGISTRY, _isar_version = _build_registries()

_SUMMARY_FIELDS = ("applicant_id", "applicant_name", "policy_type", "policy_status")


def _extract_applicant_id(raw_context: str) -> str | None:
    for line in raw_context.splitlines():
        stripped = line.strip()
        if stripped.startswith("applicant_id:"):
            return stripped.split(":", 1)[1].strip() or None
    return None


def _emit_isar_decision(evidence_dict: dict, applicant_id: str | None) -> None:
    try:
        from core_data.db.models.models_policy_audit import PolicyAuditRecord
        from mltek.common.infra.audit_decorator import emit_audit_record
        from mltek.common.infra.context import (
            CorrelationContext, get_jit_session, get_audit_session_id, get_request_id,
        )
        phase, _, env = get_jit_session()
        subject = evidence_dict.get("subject", "")
        conclusion = evidence_dict.get("conclusion", "")
        record = PolicyAuditRecord.create(
            policy_type="isar_decision",
            allowed=True,
            reason=f"subject={subject} conclusion={conclusion}",
            session_id=get_audit_session_id(),
            correlation_id=CorrelationContext.get() or "",
            request_id=get_request_id() or None,
            operation="isar_explain_applicant",
            env=env or "prod",
            phase=phase or "collecting",
            metadata=evidence_dict,
            entity_id=applicant_id,
            entity_type="applicant" if applicant_id else None,
        )
        emit_audit_record(record)
        logger.info("_emit_isar_decision: emitted audit record subject=%s applicant_id=%s", subject, applicant_id)
    except Exception as exc:
        logger.warning("_emit_isar_decision: failed to emit audit record: %s", exc)


class SemanticSynthesisPolicy(SynthesisPolicyBase):
    """Post-agent output policy: synthesizes isar_explain_applicant tool output into a narrative via LLM."""

    def __init__(self) -> None:
        super().__init__(_NARRATIVE_REGISTRY, _EVIDENCE_REGISTRY)

    async def _run_synthesis(
        self,
        result: dict,
        tool_name: str,
        user_request: str,
        raw_context: str,
    ) -> dict:
        from mltek.agents.lang_agents import call_chat_agent, call_structured_agent
        from insuretek.policy.output.isar_evidence_chain import IsarDecisionOutput

        narrative_prompt = self._narrative_registry[tool_name]
        evidence_prompt = self._evidence_registry[tool_name]
        task_input = f"USER REQUEST:\n{user_request}\n\nAPPLICANT CONTEXT:\n{raw_context}"

        logger.info("SemanticSynthesisPolicy.synthesize: tool=%s chars=%d", tool_name, len(task_input))

        try:
            narrative, evidence = await asyncio.gather(
                call_chat_agent(task_input, system_prompt=narrative_prompt),
                call_structured_agent(task_input, IsarDecisionOutput, system_prompt=evidence_prompt),
            )
        except Exception as exc:
            logger.warning("SemanticSynthesisPolicy.synthesize: LLM calls failed: %s — skipping", exc)
            return result

        applicant_id = _extract_applicant_id(raw_context)
        from mltek.db.models.base import current_timestamp_ms
        evidence_dict: dict = {
            "isar_version": _isar_version,
            "evaluated_at": current_timestamp_ms(),
            "applicant_id": applicant_id,
            **evidence.model_dump(),
        }
        logger.info("SemanticSynthesisPolicy: evidence chain parsed subject=%s rules=%d",
                    evidence.subject, len(evidence.evidence_chain))

        _emit_isar_decision(evidence_dict, applicant_id=applicant_id)

        indented = "\n".join("  " + line for line in narrative.strip().splitlines() if line.strip())
        block = f"\n\nsemantic_analysis:\n{indented}"
        summary_lines = [l for l in raw_context.splitlines() if any(l.startswith(f"{k}:") for k in _SUMMARY_FIELDS)]
        synthesis_text = "\n".join(summary_lines) + block

        logger.info("SemanticSynthesisPolicy.synthesize: completed tool=%s isar_version=%s evidence_rules=%d",
                    tool_name, _isar_version, len(evidence_dict.get("evidence_chain", [])))
        return {**result, "isar_synthesis": synthesis_text}
