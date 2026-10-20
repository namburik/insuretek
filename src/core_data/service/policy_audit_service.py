"""Policy audit services — base queries, forensic analysis, and quality summary helpers.

  PolicyAuditService    — scoped reads: by session, correlation, request, policy type
  ForensicAuditService  — read-only: entity timelines, causal chains, cross-session history
  aggregate_quality_metrics   — aggregates routing confidence and judge verdict audit records
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core_data.db.models.models_policy_audit import PolicyAuditRecord
from core_data.repositories.policy_audit_repository import PolicyAuditRepository
from mltek.common.infra.error_helpers import handle_repo_errors
from mltek.common.log.logging_config import setup_structured_logging

logger = setup_structured_logging(__name__)


def _record_to_dict(record: PolicyAuditRecord) -> Dict[str, Any]:
    """Single canonical serialisation for a PolicyAuditRecord → dict.

    Always emits all fields (including None values) so downstream code can
    use `"key" in record_dict` reliably regardless of which service produced it.
    """
    return {
        "audit_id":       record.audit_id,
        "session_id":     record.session_id,
        "correlation_id": record.correlation_id,
        "request_id":     record.request_id,
        "policy_type":    record.policy_type,
        "operation":      record.operation,
        "allowed":        record.allowed,
        "reason":         record.reason,
        "env":            record.env,
        "phase":          record.phase,
        "created_at":     record.created_at,
        "metadata":       record.metadata,
        "entity_id":      record.entity_id,
        "entity_type":    record.entity_type,
    }


class PolicyAuditService:
    """Read-only service for querying persisted policy decisions."""

    def __init__(self) -> None:
        self._repo = PolicyAuditRepository()

    @handle_repo_errors
    def get_by_session(self, session_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        records = self._repo.get_by_session(session_id, limit=limit)
        result = [_record_to_dict(r) for r in records]
        logger.info("policy_audit get_by_session session_id=%s count=%d", session_id, len(result))
        return result

    @handle_repo_errors
    def get_by_correlation(self, correlation_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        records = self._repo.get_by_correlation(correlation_id, limit=limit)
        result = [_record_to_dict(r) for r in records]
        logger.info("policy_audit get_by_correlation correlation_id=%s count=%d", correlation_id, len(result))
        return result

    @handle_repo_errors
    def get_by_request_id(self, request_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        records = self._repo.get_by_request_id(request_id, limit=limit)
        result = [_record_to_dict(r) for r in records]
        logger.info("policy_audit get_by_request_id request_id=%s count=%d", request_id, len(result))
        return result

    @handle_repo_errors
    def get_by_policy_type(self, policy_type: str, limit: int = 100) -> List[Dict[str, Any]]:
        records = self._repo.get_by_policy_type(policy_type, limit=limit)
        result = [_record_to_dict(r) for r in records]
        logger.info("policy_audit get_by_policy_type policy_type=%s count=%d", policy_type, len(result))
        return result

    @handle_repo_errors
    def get_summary(
        self,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Aggregate policy decisions into a summary: counts per policy_type + overall allow/deny.

        Provide either session_id or correlation_id to scope the summary.
        If neither is supplied, returns an empty summary.
        """
        if session_id:
            records = self._repo.get_by_session(session_id, limit=200)
            scope_key, scope_val = "session_id", session_id
        elif correlation_id:
            records = self._repo.get_by_correlation(correlation_id, limit=200)
            scope_key, scope_val = "correlation_id", correlation_id
        else:
            logger.info("policy_audit get_summary called with no scope — returning empty")
            return {"total": 0, "allowed": 0, "denied": 0, "by_policy_type": {}}

        total = len(records)
        allowed_count = sum(1 for r in records if r.allowed)
        by_type: Dict[str, Dict[str, int]] = {}
        for r in records:
            entry = by_type.setdefault(r.policy_type, {"allowed": 0, "denied": 0})
            if r.allowed:
                entry["allowed"] += 1
            else:
                entry["denied"] += 1

        summary = {
            scope_key:      scope_val,
            "total":        total,
            "allowed":      allowed_count,
            "denied":       total - allowed_count,
            "by_policy_type": by_type,
        }
        logger.info("policy_audit get_summary %s=%s total=%d allowed=%d denied=%d", scope_key, scope_val, total, allowed_count, total - allowed_count)
        return summary


def get_audit_writer():
    """Return (write_fn, emit_fn) backed by PolicyAuditRepository for use by the domain provider."""

    def write_fn(**kwargs):
        PolicyAuditRepository().create(PolicyAuditRecord.create(**kwargs))

    def emit_fn(record):
        PolicyAuditRepository().create(record)

    return write_fn, emit_fn


# ── ForensicAuditService ──────────────────────────────────────────────────────

class ForensicAuditService:

    def __init__(self):
        self._repo = PolicyAuditRepository()

    def get_entity_timeline(self, entity_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        records = self._repo.get_by_entity_id(entity_id, limit=limit)
        # repo already returns oldest-first; use _record_to_dict for consistent field set
        result = [_record_to_dict(r) for r in records]
        logger.info("ForensicAuditService get_entity_timeline exit entity_id=%s count=%d", entity_id, len(result))
        return result

    def get_causal_chain(self, correlation_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        # repo returns most-recent-first; re-sort ascending for causal ordering
        records = self._repo.get_by_correlation(correlation_id, limit=limit)
        records.sort(key=lambda r: r.created_at)
        result = [_record_to_dict(r) for r in records]
        logger.info("ForensicAuditService get_causal_chain exit correlation_id=%s count=%d", correlation_id, len(result))
        return result

    def get_cross_session_history(self, applicant_id: str, limit: int = 50) -> Dict[str, Any]:
        records = self._repo.get_by_entity_id(applicant_id, limit=limit)
        sessions: Dict[str, Dict[str, Any]] = {}
        for r in records:
            sid = r.session_id or "unknown"
            if sid not in sessions:
                sessions[sid] = {"session_id": sid, "first_seen": r.created_at, "last_seen": r.created_at,
                                 "decision_count": 0, "denied_count": 0, "policy_types": []}
            s = sessions[sid]
            s["decision_count"] += 1
            if not r.allowed:
                s["denied_count"] += 1
            s["last_seen"] = max(s["last_seen"], r.created_at)
            s["first_seen"] = min(s["first_seen"], r.created_at)
            if r.policy_type not in s["policy_types"]:
                s["policy_types"].append(r.policy_type)
        session_list = sorted(sessions.values(), key=lambda s: s["first_seen"])
        result = {"applicant_id": applicant_id, "total_decisions": len(records),
                  "session_count": len(session_list), "sessions": session_list}
        logger.info("ForensicAuditService get_cross_session_history exit applicant_id=%s sessions=%d", applicant_id, len(session_list))
        return result

    def get_isar_evidence(self, applicant_id: str = "", correlation_id: str = "", limit: int = 2) -> str:
        def _filter(rows):
            return sorted([r for r in rows if r.policy_type == "isar_decision"], key=lambda r: r.created_at or 0, reverse=True)[:limit]

        applicant_id = applicant_id.removeprefix("applicant:").strip()
        if applicant_id:
            records = _filter(self._repo.get_by_entity_id(applicant_id, limit=200))
            label = f"applicant={applicant_id}"
        elif correlation_id:
            records = _filter(self._repo.get_by_correlation(correlation_id, limit=200))
            label = f"correlation={correlation_id}"
        else:
            records = self._repo.get_by_policy_type("isar_decision", limit=limit)
            label = "recent"
        dicts = [_record_to_dict(r) for r in records]
        logger.info("ForensicAuditService.get_isar_evidence label=%s count=%d", label, len(dicts))
        return self.format_records(dicts, label=f"isar_evidence · {label}")

    @staticmethod
    def _fmt_ts(created_at: int | None) -> str:
        if created_at is None:
            return "—"
        return datetime.fromtimestamp(created_at / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _fmt_entry(seq: int, r: dict) -> list[str]:
        policy     = r.get("policy_type") or "?"
        op         = r.get("operation") or "—"
        reason     = (r.get("reason") or "—")[:120]
        meta       = r.get("metadata") or {}
        ts         = ForensicAuditService._fmt_ts(r.get("created_at"))
        audit_id   = (r.get("audit_id") or "")[-12:] or "—"
        outcome_md = "✅" if r.get("allowed") else "❌"
        env        = r.get("env") or meta.get("env") or "prod"
        phase      = r.get("phase") or ""
        entity_id  = r.get("entity_id") or ""
        entity_type = r.get("entity_type") or ""

        ctx_parts: list[str] = []
        if env and env != "prod":
            ctx_parts.append(f"env={env}")
        if phase and phase not in ("", "collecting"):
            ctx_parts.append(f"phase={phase}")
        if entity_id:
            label = f"{entity_type}:{entity_id}" if entity_type else entity_id
            ctx_parts.append(f"entity={label}")
        ctx_str = (" · " + " · ".join(ctx_parts)) if ctx_parts else ""

        lines: list[str] = []
        if policy == "pipeline_summary":
            controls   = meta.get("controls") or meta.get("policy_checks") or meta.get("gates") or []
            domain     = meta.get("intent_domain") or ""
            domain_str = f" · domain={domain}" if domain else ""
            lines.append(f"{seq}. `{ts}` `{audit_id}` **[pipeline_summary]** {outcome_md} · `{op}`{domain_str}{ctx_str}")
            if controls:
                lines.append("")
                lines.append("   | # | Control | Operation | Result |")
                lines.append("   |---|---------|-----------|--------|")
                for i, c in enumerate(controls, start=1):
                    c_result = "✅" if c.get("allowed", True) else f"❌ {(c.get('reason') or '')[:80]}"
                    lines.append(f"   | {i} | {c.get('policy_type', '?')} | `{c.get('operation', '—')}` | {c_result} |")
            else:
                lines.append(f"   Controls: {(reason.split('ALLOWED:')[-1].strip()) or '—'}")
        elif policy == "isar_decision":
            subject    = meta.get("subject") or "—"
            conclusion = meta.get("conclusion") or (reason.split("conclusion=", 1)[-1] if "conclusion=" in reason else "—")
            isar_ver   = meta.get("isar_version") or "—"
            chain      = meta.get("evidence_chain") or []
            parse_note = " ⚠ evidence parse failed" if not chain and subject == "—" else ""
            lines.append(f"{seq}. `{ts}` `{audit_id}` **[isar_decision]** {outcome_md} · subject=`{subject}` · isar=`{isar_ver}`{ctx_str}{parse_note}")
            lines.append(f"   **Conclusion:** {conclusion}")
            if chain:
                lines.append("")
                lines.append("   | Rule | Field | Value | Threshold | Finding |")
                lines.append("   |------|-------|-------|-----------|---------|")
                for ev in chain:
                    lines.append(
                        f"   | `{ev.get('rule_id', '?')}` | `{ev.get('field', '—')}` "
                        f"| {ev.get('actual_value', '—')} | {ev.get('threshold', '—')} "
                        f"| {ev.get('finding', '—')} |"
                    )
        else:
            badges: list[str] = []
            if meta.get("risk_level"):       badges.append(f"risk={meta['risk_level']}")
            if meta.get("source"):           badges.append(f"src={meta['source']}")
            if meta.get("intent_snippet"):   badges.append(f'intent="{meta["intent_snippet"][:60]}"')
            badge_str = (" · " + " · ".join(badges)) if badges else ""
            lines.append(f"{seq}. `{ts}` `{audit_id}` **[{policy}]** {outcome_md} · `{op}`{badge_str}{ctx_str}")
            lines.append(f"   {reason}")
        lines.append("")
        return lines

    @staticmethod
    def _group_context(group: list[dict]) -> dict[str, str]:
        """Extract the dominant session, agent, domain, and group/entity from a correlation group."""
        sessions: dict[str, int] = {}
        agents:   dict[str, int] = {}
        domains:  dict[str, int] = {}
        entities: dict[str, int] = {}

        for r in group:
            sid = r.get("session_id") or ""
            if sid:
                sessions[sid] = sessions.get(sid, 0) + 1

            # Agent: prefer pipeline_summary operation (most specific agent name), else operation field.
            op = r.get("operation") or ""
            if op and op != "pipeline_summary":
                agents[op] = agents.get(op, 0) + 1

            meta = r.get("metadata") or {}

            # Domain: from intent_domain (pipeline_summary) or entity_type.
            domain = meta.get("intent_domain") or r.get("entity_type") or ""
            if domain:
                domains[domain] = domains.get(domain, 0) + 1

            # Group/entity: entity_id scoped by entity_type.
            eid   = r.get("entity_id") or meta.get("group_id") or meta.get("applicant_id") or ""
            etype = r.get("entity_type") or ""
            if eid:
                label = f"{etype}:{eid}" if etype else eid
                entities[label] = entities.get(label, 0) + 1

        def _top(d: dict[str, int]) -> str:
            if not d:
                return "—"
            top = max(d, key=lambda k: d[k])
            extra = len(d) - 1
            return f"{top} (+{extra} more)" if extra else top

        return {
            "session": _top(sessions),
            "agent":   _top(agents),
            "domain":  _top(domains),
            "entity":  _top(entities),
        }

    @staticmethod
    def format_records(records: list | None, label: str = "") -> str:
        if not records:
            return f"No policy audit records found{' for ' + label if label else ''}."

        # Sort by timestamp then correlation_id; group by correlation preserving first-seen order.
        sorted_records = sorted(records, key=lambda r: (r.get("created_at") or 0, r.get("correlation_id") or ""))
        groups: dict[str, list] = defaultdict(list)
        order: list[str] = []
        for r in sorted_records:
            cid = r.get("correlation_id") or "—"
            if cid not in groups:
                order.append(cid)
            groups[cid].append(r)

        lines = [f"**Policy Audit** ({len(records)} records){' — ' + label if label else ''}", ""]
        seq = 1
        for cid in order:
            group    = groups[cid]
            first_ts = ForensicAuditService._fmt_ts(group[0].get("created_at"))
            last_ts  = ForensicAuditService._fmt_ts(group[-1].get("created_at"))
            ts_range = first_ts if first_ts == last_ts else f"{first_ts} → {last_ts}"
            ctx      = ForensicAuditService._group_context(group)
            n        = len(group)
            lines.append("---")
            lines.append(f"**Correlation** `{cid}` · {n} event{'s' if n != 1 else ''} · {ts_range}")
            lines.append(
                f"session=`{ctx['session']}` · agent=`{ctx['agent']}` · "
                f"domain=`{ctx['domain']}` · entity=`{ctx['entity']}`"
            )
            lines.append("")
            for r in group:
                lines.extend(ForensicAuditService._fmt_entry(seq, r))
                seq += 1
        return "\n".join(lines).rstrip()

    @staticmethod
    def format_entity_timeline(records: list | None, entity_id: str = "") -> str:
        if not records:
            return f"No audit records found for entity {entity_id}."
        sorted_records = sorted(records, key=lambda r: r.get("created_at") or 0)
        lines = [f"**Entity Timeline** — `{entity_id}` ({len(sorted_records)} records)", ""]
        for i, r in enumerate(sorted_records, start=1):
            lines.extend(ForensicAuditService._fmt_entry(i, r))
        return "\n".join(lines).rstrip()

    @staticmethod
    def format_causal_chain(records: list | None, correlation_id: str = "") -> str:
        if not records:
            return f"No audit records found for correlation `{correlation_id}`."
        sorted_records = sorted(records, key=lambda r: r.get("created_at") or 0)
        lines = [f"**Causal Chain** — correlation `{correlation_id}` ({len(sorted_records)} steps)", ""]
        for pos, r in enumerate(sorted_records, start=1):
            lines.extend(ForensicAuditService._fmt_entry(pos, r))
        return "\n".join(lines).rstrip()

    @staticmethod
    def format_cross_session_history(data: dict | None) -> str:
        if not data or data.get("session_count", 0) == 0:
            return f"No session history found for applicant `{data.get('applicant_id', '?') if data else '?'}`."
        mid   = data["applicant_id"]
        total = data["total_decisions"]
        lines = [
            f"**Cross-Session History** — applicant `{mid}` · {data['session_count']} sessions · {total} total decisions",
            "",
            "| Session | Decisions | Denied | First → Last (UTC) | Policies |",
            "|---------|-----------|--------|--------------------|----------|",
        ]
        for s in data.get("sessions") or []:
            sid      = str(s.get("session_id") or "—")[:8]
            policies = ", ".join(s.get("policy_types") or []) or "—"
            first    = ForensicAuditService._fmt_ts(s.get("first_seen"))
            last     = ForensicAuditService._fmt_ts(s.get("last_seen"))
            lines.append(f"| `{sid}` | {s.get('decision_count', 0)} | {s.get('denied_count', 0)} | {first} → {last} | {policies} |")
        return "\n".join(lines)

    @staticmethod
    def format_summary(data: dict | None) -> str:
        if not data or data.get("total", 0) == 0:
            return "No policy audit records found for the given scope."
        total   = data["total"]
        allowed = data["allowed"]
        denied  = data["denied"]
        lines = [
            f"**Policy Audit Summary** — {allowed} allowed / {denied} denied / {total} total",
            "",
            "| Policy Type | Allowed | Denied |",
            "|-------------|---------|--------|",
        ]
        for ptype, counts in (data.get("by_policy_type") or {}).items():
            lines.append(f"| {ptype} | {counts.get('allowed', 0)} | {counts.get('denied', 0)} |")
        return "\n".join(lines)


# ── Quality summary helpers ───────────────────────────────────────────────────


def _empty_quality_summary(agent_id: Optional[str], window_hours: int) -> Dict[str, Any]:
    return {"window_hours": window_hours, "agent_id": agent_id,
            "routing": {"total": 0, "avg_confidence": 0.0, "below_threshold": 0},
            "judge":   {"total": 0, "pass_rate": None, "avg_confidence": 0.0}}


def aggregate_quality_metrics(agent_id: Optional[str] = None, window_hours: int = 24) -> Dict[str, Any]:
    cutoff_ms = int((time.time() - window_hours * 3600) * 1000)
    try:
        records = PolicyAuditRepository().get_by_policy_type("quality_metric", limit=10_000, since_ms=cutoff_ms)
    except Exception:
        logger.warning("aggregate_quality_metrics fetch failed", exc_info=True)
        return _empty_quality_summary(agent_id, window_hours)

    routing_confidences: List[float] = []
    routing_below = 0
    judge_passed: List[bool] = []
    judge_confidences: List[float] = []

    for rec in records:
        meta = rec.metadata or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        if agent_id and meta.get("agent_id") and meta["agent_id"] != agent_id:
            continue
        mtype = meta.get("metric_type", "")
        if mtype == "routing_confidence":
            c = float(meta.get("confidence", 0.0))
            routing_confidences.append(c)
            if c < 0.35:
                routing_below += 1
        elif mtype == "judge_verdict":
            judge_passed.append(bool(meta.get("passed", True)))
            judge_confidences.append(float(meta.get("confidence", 0.5)))

    avg_routing = (sum(routing_confidences) / len(routing_confidences)) if routing_confidences else 0.0
    pass_rate   = (sum(judge_passed) / len(judge_passed)) if judge_passed else None
    avg_judge   = (sum(judge_confidences) / len(judge_confidences)) if judge_confidences else 0.0
    logger.info("aggregate_quality_metrics agent=%s routing=%d judge=%d",
                agent_id or "all", len(routing_confidences), len(judge_passed))
    return {
        "window_hours": window_hours, "agent_id": agent_id,
        "routing": {"total": len(routing_confidences), "avg_confidence": round(avg_routing, 4), "below_threshold": routing_below},
        "judge":   {"total": len(judge_passed), "pass_rate": round(pass_rate, 4) if pass_rate is not None else None, "avg_confidence": round(avg_judge, 4)},
    }


