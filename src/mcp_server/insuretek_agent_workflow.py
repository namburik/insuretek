from __future__ import annotations

from typing import Sequence

from mcp.server.lowlevel import Server
from mcp.types import Tool, TextContent, EmbeddedResource
from mltek.common.starlette_app import StreamableStarletteApp, run_starlette_app

from mltek.config.config_common import Config
from mltek.common.infra.context import set_app_name
from mltek.common.router.resolver import register_domain_provider
from mltek.common.log.logging_config import setup_structured_logging
from insuretek.domain_provider import InsureTekDomainProvider

set_app_name("insuretek")
register_domain_provider(InsureTekDomainProvider())
from mltek.common.infra.error_helpers import handle_mcp_tool_errors
from mltek.common.tools.format import format_tool_result
from mcp_server.utilities.transform import require_arg, unwrap_object_argument
from mcp_server.utilities.constants import WorkflowMcpTools
from mcp_server.utilities.tools_schemas import get_tool_description, get_tool_schema
from core_data.service.policy_audit_service import ForensicAuditService, aggregate_quality_metrics
from mcp_server.utilities.transform import format_quality_summary
from workflow_engine.service.workflow_endpoints import (
    get_workflow_by_applicant_id,
    get_workflow_request,
    get_applicant_by_id,
    get_prior_decision_traces,
)

logger = setup_structured_logging(__name__)

_forensic_svc: ForensicAuditService | None = None


def _get_forensic_svc() -> ForensicAuditService:
    global _forensic_svc
    if _forensic_svc is None:
        _forensic_svc = ForensicAuditService()
    return _forensic_svc


def _label(key: str) -> str:
    return " ".join("ID" if w == "id" else w.capitalize() for w in key.split("_"))


def _kv(key: str, value) -> str:
    if value is None:
        return f"{_label(key)}: —"
    if isinstance(value, bool):
        return f"{_label(key)}: {'Yes' if value else 'No'}"
    return f"{_label(key)}: {value}"


def _null_data_hint(message: str) -> str:
    return (
        f"{message} "
        "Would you like to initiate a new workflow, try a different identifier, "
        "or review related applicant information instead?"
    )



def _format_workflow(data: dict | None) -> str:
    if not data:
        return _null_data_hint("I couldn't find an active workflow with that workflow ID.")
    lines = [
        _kv("workflow_id",               data.get("workflow_id")),
        _kv("applicant_id",              data.get("applicant_id")),
        _kv("current_state",             data.get("current_state")),
        _kv("is_final_approval_granted", data.get("is_final_approval_granted")),
        _kv("approved_by",               data.get("approved_by")),
    ]
    tasks = data.get("approval_tasks") or []
    if tasks:
        lines.append("\nApproval Tasks:")
        for t in tasks:
            gate = _label(t.get("gate_check", "unknown"))
            status = t.get("status", "—")
            tag = " (essential)" if t.get("is_essential") else ""
            lines.append(f"  {gate}: {status}{tag}")
    return "\n".join(lines)


def _format_orchestrator_job(data: dict | None, *, include_request_id: bool = True) -> str:
    if not data:
        return _null_data_hint("I couldn't find an orchestrator onboarding job for that identifier.")
    applicant_id = data.get("applicant_id")
    lines = [
        *([_kv("request_id", data.get("request_id"))] if include_request_id else []),
        _kv("status",       data.get("status")),
        _kv("applicant_id", applicant_id),
        _kv("workflow_id",  data.get("workflow_id")),
    ]
    steps = data.get("steps") or []
    if steps:
        counts: dict[str, int] = {}
        for s in steps:
            st = (s.get("status") or "unknown").upper()
            counts[st] = counts.get(st, 0) + 1
        summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
        lines.append(f"\nSteps ({len(steps)} total — {summary}):")
        for s in steps:
            label     = s.get("label") or s.get("step_id") or "?"
            status    = (s.get("status") or "?").upper()
            score     = s.get("eval_score")
            score_str = f" score={score}" if score is not None else ""
            note      = s.get("note") or ""
            note_str  = f" | {note}" if note else ""
            lines.append(f"  {label}: {status}{score_str}{note_str}")
    hitl = data.get("hitl_step")
    if hitl:
        lines.append("\nHITL Suspension:")
        lines.append(f"  Step:  {hitl.get('step_id') or hitl.get('label') or '?'}")
        lines.append(f"  Error: {hitl.get('error') or '—'}")
        lines.append(f"  Resume via: POST /webhooks/orchestrator/{data.get('request_id')}/resume")
    return "\n".join(lines)


def _format_issues(data: dict | None) -> str:
    if not data:
        return _null_data_hint("I couldn't find any pending enterprise workflow issues for that identifier.")
    lines = [
        _kv("enterprise_identifier", data.get("enterprise_identifier")),
        _kv("issue_count",           data.get("issue_count")),
        _kv("at_risk",               data.get("at_risk")),
    ]
    for i, issue in enumerate(data.get("issues") or [], 1):
        lines.append(
            f"  {i}. Workflow ID: {issue.get('workflow_id', '—')} | "
            f"Provider: {issue.get('provider', '—')} | "
            f"State: {issue.get('current_state', '—')}"
        )
    return "\n".join(lines)


def _resolve_tool_result(name: str, arguments: dict, audit_svc) -> str | dict:
    tool_handlers = {
        WorkflowMcpTools.GET_ORCHESTRATOR_BY_REQUEST.value: lambda args: _format_orchestrator_job(
            get_workflow_request(require_arg(args, "request_id"))
        ),
        WorkflowMcpTools.GET_ORCHESTRATOR_BY_APPLICANT.value: lambda args: _format_orchestrator_job(
            get_applicant_by_id(require_arg(args, "applicant_id"))
        ),
        WorkflowMcpTools.GET_REQUEST_JOB.value: lambda args: get_workflow_request(
            require_arg(args, "request_id")
        ),
        WorkflowMcpTools.GET_APPLICANT_JOB.value: lambda args: _format_workflow(
            get_workflow_by_applicant_id(require_arg(args, "applicant_id"))
        ),
        WorkflowMcpTools.GET_ENT_ISSUES.value: lambda args: get_prior_decision_traces(
            require_arg(args, "enterprise_identifier")
        ),
        WorkflowMcpTools.GET_AUDIT_BY_SESSION.value: lambda args: ForensicAuditService.format_records(
            audit_svc.get_by_session(require_arg(args, "session_id"), limit=args.get("limit", 50)),
            label=f"session={args['session_id']}",
        ),
        WorkflowMcpTools.GET_AUDIT_BY_CORRELATION.value: lambda args: ForensicAuditService.format_records(
            audit_svc.get_by_correlation(require_arg(args, "correlation_id"), limit=args.get("limit", 50)),
            label=f"correlation={args['correlation_id']}",
        ),
        WorkflowMcpTools.GET_AUDIT_BY_POLICY_TYPE.value: lambda args: ForensicAuditService.format_records(
            audit_svc.get_by_policy_type(require_arg(args, "policy_type"), limit=args.get("limit", 100)),
            label=f"policy_type={args['policy_type']}",
        ),
        WorkflowMcpTools.GET_AUDIT_BY_REQUEST.value: lambda args: ForensicAuditService.format_records(
            audit_svc.get_by_request_id(require_arg(args, "request_id"), limit=args.get("limit", 50)),
            label=f"request_id={args['request_id']}",
        ),
        WorkflowMcpTools.GET_AUDIT_SUMMARY.value: lambda args: ForensicAuditService.format_summary(
            audit_svc.get_summary(
                session_id=args.get("session_id"),
                correlation_id=args.get("correlation_id"),
            )
        ),
        WorkflowMcpTools.GET_FORENSIC_AUDIT.value: lambda args: ForensicAuditService.format_entity_timeline(
            _get_forensic_svc().get_entity_timeline(
                entity_id=require_arg(args, "applicant_id"),
                limit=int(args.get("limit", 100)),
            ),
            entity_id=args.get("applicant_id", ""),
        ),
        WorkflowMcpTools.GET_CAUSAL_CHAIN.value: lambda args: ForensicAuditService.format_causal_chain(
            _get_forensic_svc().get_causal_chain(
                correlation_id=require_arg(args, "correlation_id"),
                limit=int(args.get("limit", 100)),
            ),
            correlation_id=args.get("correlation_id", ""),
        ),
        WorkflowMcpTools.GET_CROSS_SESSION_HISTORY.value: lambda args: ForensicAuditService.format_cross_session_history(
            _get_forensic_svc().get_cross_session_history(
                applicant_id=require_arg(args, "applicant_id"),
                limit=int(args.get("limit", 50)),
            )
        ),
        WorkflowMcpTools.GET_QUALITY_SUMMARY.value: lambda args: format_quality_summary(
            aggregate_quality_metrics(
                agent_id=args.get("agent_id") or None,
                window_hours=int(args.get("window_hours", 24)),
            )
        ),
    }

    handler = tool_handlers.get(name)
    if handler is None:
        raise ValueError(f"Unknown tool: {name}")
    result = handler(arguments)
    logger.info("tool exit name=%s result_len=%d", name, len(result) if isinstance(result, str) else 0)
    return result


def main(port: int, log_level: str, json_response: bool) -> None:
    setup_structured_logging(log_level)
    logger.info("Agent Workflow MCP Server starting port=%d log_level=%s", port, log_level)

    from core_data.service.policy_audit_service import PolicyAuditService
    audit_svc = PolicyAuditService()
    server = Server("agent_workflow_server")

    @server.call_tool()
    @handle_mcp_tool_errors
    async def call_tool(name: str, arguments: dict) -> Sequence[TextContent | EmbeddedResource] | dict:
        arguments = unwrap_object_argument(arguments)
        correlation_id = arguments.get("correlation_id", "")
        _key = next(iter(arguments.values()), "") if arguments else ""
        logger.info("tool enter name=%s key=%s correlation_id=%s", name, _key, correlation_id)
        result = _resolve_tool_result(name, arguments, audit_svc)
        logger.info("tool complete name=%s", name)
        if isinstance(result, dict):
            return result
        return format_tool_result(result)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(name=t.value, description=get_tool_description(t.value), inputSchema=get_tool_schema(t.value))
            for t in WorkflowMcpTools
        ]

    starlette_app = StreamableStarletteApp(
        server=server,
        json_response=json_response,
        path="/mcp_workflow",
        health_path=None,
        debug=False,
    ).create_app()
    run_starlette_app(starlette_app, host=Config.SERVER_HOST, port=Config.MCP_WORKFLOW_PORT, access_log=False)


if __name__ == "__main__":
    main(Config.MCP_WORKFLOW_PORT, Config.LOG_LEVEL, True)  # type: ignore[call-arg]
