"""Dynamic MCP tool schema definitions for Insuretek underwriting tools."""

from typing import Dict, Any
from mcp_server.utilities.constants import TOOL_DESCRIPTIONS


# ── GraphRAG schemas ──────────────────────────────────────────────────────────

def get_find_similar_applicants_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "applicant_id": {"type": "string", "description": "Applicant or entity ID to find similar records for"},
            "top_k": {"type": ["integer", "string"], "description": "Number of results (default 5, max 20)", "default": 5},
        },
        "required": ["applicant_id"],
    }


def get_query_applicant_graph_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Keyword terms to search across the knowledge graph."},
        },
        "required": ["query"],
    }


# ── MSAgent / semantic-context schemas ───────────────────────────────────────

def get_kg_similar_applicants_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "applicant_id": {"type": "string", "description": "Anchor applicant ID for graph-based matching.", "default": ""},
            "query_text": {"type": "string", "description": "Natural-language profile for semantic vector search.", "default": ""},
            "limit": {"type": "integer", "description": "Maximum results (default 5).", "default": 5},
            "alpha": {"type": "number", "description": "Graph score weight: 0=pure vector, 1=pure graph, 0.5=balanced.", "default": 0.5},
        },
        "required": [],
    }


def get_kg_detect_risk_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"applicant_id": {"type": "string", "description": "Applicant ID to analyse."}}, "required": ["applicant_id"]}


def get_kg_explain_applicant_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"applicant_id": {"type": "string", "description": "Applicant ID to explain."}}, "required": ["applicant_id"]}


def get_isar_explain_applicant_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"applicant_id": {"type": "string", "description": "Applicant ID."}}, "required": ["applicant_id"]}



# ── Workflow / orchestrator schemas ───────────────────────────────────────────

def get_orchestrator_status_by_request_id_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"request_id": {"type": "string"}}, "required": ["request_id"]}


def get_orchestrator_status_by_applicant_id_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"applicant_id": {"type": "string", "description": "Applicant ID."}}, "required": ["applicant_id"]}


def get_workflow_job_by_request_id_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"request_id": {"type": "string"}}, "required": ["request_id"]}


def get_workflow_job_by_applicant_id_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"applicant_id": {"type": "string", "description": "Applicant ID."}}, "required": ["applicant_id"]}


def get_prior_decision_traces_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"enterprise_identifier": {"type": "string"}}, "required": ["enterprise_identifier"]}


# ── Policy audit schemas ──────────────────────────────────────────────────────

def get_policy_audit_by_session_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"session_id": {"type": "string"}, "limit": {"type": "integer", "default": 50}}, "required": ["session_id"]}


def get_policy_audit_by_correlation_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"correlation_id": {"type": "string"}, "limit": {"type": "integer", "default": 50}}, "required": ["correlation_id"]}


def get_policy_audit_by_policy_type_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "policy_type": {"type": "string", "enum": ["jit_elevation", "routing", "onboarding", "servicing", "guardian", "llm_judge", "reflection", "tool_intent"]},
            "limit": {"type": "integer", "default": 100},
        },
        "required": ["policy_type"],
    }


def get_policy_audit_by_request_id_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"request_id": {"type": "string"}, "limit": {"type": "integer", "default": 50}}, "required": ["request_id"]}


def get_policy_audit_summary_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"session_id": {"type": "string"}, "correlation_id": {"type": "string"}}}


def aggregate_quality_metrics_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"agent_id": {"type": ["string", "null"]}, "window_hours": {"type": "integer", "default": 24}}, "required": []}


def get_isar_evidence_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "applicant_id": {"type": "string", "description": "Filter by applicant UUID — returns all isar_decision records for that applicant, oldest first."},
            "correlation_id": {"type": "string", "description": "Filter by HTTP correlation ID — returns the isar_decision record(s) for that specific request."},
            "limit": {"type": "integer", "default": 2, "description": "Maximum number of records to return."},
        },
        "required": [],
    }


def get_forensic_audit_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "applicant_id": {"type": "string", "description": "Applicant ID (UUID)."},
            "entity_type": {"type": "string", "enum": ["applicant"], "default": "applicant"},
            "limit": {"type": "integer", "default": 100},
        },
        "required": ["applicant_id"],
    }


def get_causal_chain_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"correlation_id": {"type": "string"}, "limit": {"type": "integer", "default": 100}}, "required": ["correlation_id"]}


def get_cross_session_history_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"applicant_id": {"type": "string", "description": "Applicant ID (UUID)."}, "limit": {"type": "integer", "default": 50}}, "required": ["applicant_id"]}


# ── Insurance applicant schemas ───────────────────────────────────────────────

def get_create_applicant_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "applicant_json": {
                "type": "object",
                "description": "Applicant fields: applicant_name, policy_type, coverage_request, declared_value, contact_info, location_address, risk_profile.",
            },
        },
        "required": ["applicant_json"],
    }


def get_get_applicant_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"applicant_id": {"type": "string"}}, "required": ["applicant_id"]}


def get_search_applicants_by_policy_type_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"policy_type": {"type": "string", "enum": ["HOME", "AUTO", "COMMERCIAL", "LIFE"]}}, "required": ["policy_type"]}


def get_all_applicants_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"before_created_at": {"type": ["integer", "null"]}}, "required": []}


def get_high_risk_applicants_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {}, "required": []}


def get_update_applicant_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "applicant_id": {"type": "string"},
            "applicant_name": {"type": ["string", "null"]},
            "policy_type": {"type": ["string", "null"]},
            "declared_value": {"type": ["string", "null"]},
            "coverage_request": {"type": ["object", "null"]},
            "risk_profile": {"type": ["object", "null"]},
            "contact_info": {"type": ["object", "null"]},
            "location_address": {"type": ["object", "null"]},
        },
        "required": ["applicant_id"],
    }


def get_quote_applicant_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"applicant_id": {"type": "string"}}, "required": ["applicant_id"]}


def get_delete_applicant_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"applicant_id": {"type": "string"}}, "required": ["applicant_id"]}


# ── Insurance policy schemas ──────────────────────────────────────────────────

def get_create_policy_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "applicant_id": {"type": "string"},
            "policy_type": {"type": "string", "enum": ["HOME", "AUTO", "COMMERCIAL", "LIFE"]},
            "coverage_amount": {"type": ["string", "null"]},
            "deductible": {"type": ["string", "null"]},
            "declared_value": {"type": ["string", "null"]},
            "metadata": {"type": ["object", "null"]},
        },
        "required": ["applicant_id", "policy_type"],
    }


def get_active_policy_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"applicant_id": {"type": "string"}}, "required": ["applicant_id"]}


def get_policy_history_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"applicant_id": {"type": "string"}}, "required": ["applicant_id"]}


def get_bind_policy_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"applicant_id": {"type": "string"}, "policy_id": {"type": "string"}}, "required": ["applicant_id", "policy_id"]}


def get_cancel_policy_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {"applicant_id": {"type": "string"}}, "required": ["applicant_id"]}


# ── Dispatch table ────────────────────────────────────────────────────────────

_SCHEMA_REGISTRY: Dict[str, Any] = {
    # GraphRAG
    "find_similar_applicants":            get_find_similar_applicants_schema,
    "query_applicant_graph":              get_query_applicant_graph_schema,
    # MSAgent / semantic-context
    "kg_similar_applicants":              get_kg_similar_applicants_schema,
    "kg_detect_risk_cluster":             get_kg_detect_risk_schema,
    "kg_explain_applicant":               get_kg_explain_applicant_schema,
    "isar_explain_applicant":             get_isar_explain_applicant_schema,
    # Workflow / orchestrator
    "get_orchestrator_status_by_request_id":   get_orchestrator_status_by_request_id_schema,
    "get_orchestrator_status_by_applicant_id": get_orchestrator_status_by_applicant_id_schema,
    "get_workflow_job_by_request_id":          get_workflow_job_by_request_id_schema,
    "get_workflow_job_by_applicant_id":        get_workflow_job_by_applicant_id_schema,
    "get_prior_decision_traces":              get_prior_decision_traces_schema,
    # Policy audit
    "get_policy_audit_by_session":     get_policy_audit_by_session_schema,
    "get_policy_audit_by_correlation": get_policy_audit_by_correlation_schema,
    "get_policy_audit_by_policy_type": get_policy_audit_by_policy_type_schema,
    "get_policy_audit_by_request_id":  get_policy_audit_by_request_id_schema,
    "get_policy_audit_summary":        get_policy_audit_summary_schema,
    "aggregate_quality_metrics":             aggregate_quality_metrics_schema,
    "get_isar_evidence":               get_isar_evidence_schema,
    "get_forensic_audit":              get_forensic_audit_schema,
    "get_causal_chain":                get_causal_chain_schema,
    "get_cross_session_history":       get_cross_session_history_schema,
    # Insurance applicant
    "create_applicant":                get_create_applicant_schema,
    "get_applicant":                   get_get_applicant_schema,
    "search_applicants_by_policy_type": get_search_applicants_by_policy_type_schema,
    "get_all_applicants":              get_all_applicants_schema,
    "get_high_risk_applicants":        get_high_risk_applicants_schema,
    "update_applicant":                get_update_applicant_schema,
    "quote_applicant":                 get_quote_applicant_schema,
    "delete_applicant":                get_delete_applicant_schema,
    # Insurance policy
    "create_policy":                   get_create_policy_schema,
    "get_active_policy":               get_active_policy_schema,
    "get_policy_history":              get_policy_history_schema,
    "bind_policy":                     get_bind_policy_schema,
    "cancel_policy":                   get_cancel_policy_schema,
}


def get_tool_schema(tool_name: str) -> Dict[str, Any]:
    if tool_name not in _SCHEMA_REGISTRY:
        raise ValueError(f"Unknown tool: {tool_name}. Available: {list(_SCHEMA_REGISTRY)}")
    return _SCHEMA_REGISTRY[tool_name]()


def get_tool_description(tool_name: str) -> str:
    if tool_name not in TOOL_DESCRIPTIONS:
        raise ValueError(f"Unknown tool: {tool_name}. Available: {list(TOOL_DESCRIPTIONS)}")
    return TOOL_DESCRIPTIONS[tool_name]


def list_tool_descriptions() -> list[dict]:
    return [{"name": n, "description": d} for n, d in TOOL_DESCRIPTIONS.items()]
