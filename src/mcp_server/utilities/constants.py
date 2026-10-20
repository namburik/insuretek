"""MCP server tool-name registries for all Insuretek MCP servers."""

from enum import Enum


class InsurtekTools(str, Enum):
    # GraphRAG tools
    FIND_SIMILAR_APPLICANTS  = "find_similar_applicants"
    QUERY_APPLICANT_GRAPH    = "query_applicant_graph"
    # Insurance applicant tools
    CREATE_APPLICANT        = "create_applicant"
    GET_APPLICANT           = "get_applicant"
    SEARCH_APPLICANTS       = "search_applicants_by_policy_type"
    GET_ALL_APPLICANTS      = "get_all_applicants"
    GET_HIGH_RISK_APPLICANTS = "get_high_risk_applicants"
    UPDATE_APPLICANT        = "update_applicant"
    QUOTE_APPLICANT         = "quote_applicant"
    DELETE_APPLICANT        = "delete_applicant"
    # Insurance policy tools
    CREATE_POLICY           = "create_policy"
    GET_ACTIVE_POLICY       = "get_active_policy"
    GET_POLICY_HISTORY      = "get_policy_history"
    BIND_POLICY             = "bind_policy"
    CANCEL_POLICY           = "cancel_policy"


class MSAgentTools(str, Enum):
    KG_SIMILAR_APPLICANTS  = "kg_similar_applicants"
    KG_DETECT_RISK         = "kg_detect_risk_cluster"
    KG_EXPLAIN_APPLICANT   = "kg_explain_applicant"
    ISAR_EXPLAIN_APPLICANT = "isar_explain_applicant"
    GET_ISAR_EVIDENCE      = "get_isar_evidence"


class WorkflowMcpTools(str, Enum):
    GET_REQUEST_JOB              = "get_workflow_job_by_request_id"
    GET_APPLICANT_JOB            = "get_workflow_job_by_applicant_id"
    GET_ORCHESTRATOR_BY_REQUEST  = "get_orchestrator_status_by_request_id"
    GET_ORCHESTRATOR_BY_APPLICANT = "get_orchestrator_status_by_applicant_id"
    GET_ENT_ISSUES               = "get_prior_decision_traces"
    GET_AUDIT_BY_SESSION         = "get_policy_audit_by_session"
    GET_AUDIT_BY_CORRELATION     = "get_policy_audit_by_correlation"
    GET_AUDIT_BY_POLICY_TYPE     = "get_policy_audit_by_policy_type"
    GET_AUDIT_BY_REQUEST         = "get_policy_audit_by_request_id"
    GET_AUDIT_SUMMARY            = "get_policy_audit_summary"
    GET_FORENSIC_AUDIT           = "get_forensic_audit"
    GET_CAUSAL_CHAIN             = "get_causal_chain"
    GET_CROSS_SESSION_HISTORY    = "get_cross_session_history"
    GET_QUALITY_SUMMARY          = "aggregate_quality_metrics"


TOOL_DESCRIPTIONS: dict[str, str] = {
    # GraphRAG tools
    "query_applicant_graph": "Search applicants by keyword across name, policy type, location. Extract keyword terms only — do not pass full sentences.",
    "find_similar_applicants": "Find applicants similar to a given applicant using graph-based scoring.",
    # Insurance applicant tools
    "create_applicant": "Create a new insurance applicant and initiate underwriting intake. Pass applicant_json with required fields: applicant_name, policy_type, coverage_request, declared_value, contact_info, location_address, risk_profile.",
    "get_applicant": "Retrieve an insurance applicant by applicant_id. Returns all fields including risk_profile, coverage_request, and current underwriting status.",
    "search_applicants_by_policy_type": "Filter and return applicants by policy type (HOME | AUTO | COMMERCIAL | LIFE).",
    "get_all_applicants": "List all applicants with their current underwriting status and risk tier. Pass before_created_at for pagination.",
    "get_high_risk_applicants": "Return all applicants with HIGH_RISK or DECLINED risk tier. No arguments required.",
    "update_applicant": "Apply a partial update to an existing applicant record. Pass applicant_id and only the fields to change.",
    "quote_applicant": "Generate or retrieve the current premium quote for an applicant based on their risk profile.",
    "delete_applicant": "Soft-delete an applicant record. Irreversible — use with caution.",
    # Insurance policy tools
    "create_policy": "Create or replace the active insurance policy for an applicant. Requires applicant_id and policy_type.",
    "get_active_policy": "Retrieve the current active policy for an applicant by applicant_id.",
    "get_policy_history": "List all historical policy records for an applicant, newest first.",
    "bind_policy": "Bind (issue) a quoted policy, transitioning it from QUOTED to BOUND status. Requires applicant_id and policy_id.",
    "cancel_policy": "Cancel the active policy for an applicant. Transitions status to CANCELLED.",
    # MSAgent / semantic-context tools
    "kg_similar_applicants": (
        "Find applicants similar to a given applicant or a natural-language profile description. "
        "Provide applicant_id for graph-based field matching or query_text for semantic vector search. "
        "Returns ranked list with similarity_score (%), graph_score, and vector_score."
    ),
    "kg_detect_risk_cluster": (
        "Detect the risk cluster around an applicant. "
        "Use for questions about fraud exposure, suspicious connections, or network risk."
    ),
    "kg_explain_applicant": (
        "Return a KG-derived narrative summary: peer context, risk band relative to peers. "
        "Use only when the question is specifically about peer comparison or KG context."
    ),
    "isar_explain_applicant": (
        "PRIMARY applicant lookup. Return a structured snapshot: identity, policy type, risk tier, "
        "coverage request, and active policy. Use for any question about a known applicant."
    ),
    # Workflow tools
    "get_workflow_job_by_request_id": "Retrieve underwriting workflow status by request_id (req_xxx).",
    "get_workflow_job_by_applicant_id": "Retrieve underwriting workflow details for an applicant_id.",
    "get_orchestrator_status_by_request_id": (
        "Return the step-by-step orchestrator execution status for an underwriting job by request_id. "
        "Shows each step's PASS/FAIL/SKIP result and eval score."
    ),
    "get_orchestrator_status_by_applicant_id": (
        "Return the orchestrator underwriting job status for an applicant resolved by applicant_id."
    ),
    "get_prior_decision_traces": "Return pending workflow approval issues for a specific enterprise_identifier.",
    # Policy audit tools
    "get_policy_audit_by_session": "Return all policy decisions recorded for a JIT session_id.",
    "get_policy_audit_by_correlation": "Return all policy gate decisions (routing, guardian, cost, token_budget, etc.) for a single HTTP request identified by its correlation_id. NOT for ISAR semantic conclusions — use get_isar_evidence for those.",
    "get_policy_audit_by_policy_type": (
        "Return the most recent policy decisions for a specific policy_type. "
        "Valid values: jit_elevation, routing, onboarding, servicing, guardian, llm_judge, reflection, tool_intent."
    ),
    "get_policy_audit_by_request_id": "Return all policy decisions recorded for a workflow request_id.",
    "get_policy_audit_summary": "Aggregate policy decisions into a summary of allowed/denied counts per policy_type. Scope by session_id or correlation_id.",
    "aggregate_quality_metrics": (
        "Return aggregated quality metrics for the AI pipeline: routing confidence histogram and LLM Judge pass rate. "
        "Filter by agent_id (optional) and a lookback window in hours."
    ),
    "get_isar_evidence": "Retrieve persisted ISAR semantic conclusions for an applicant or request. Filter by applicant_id (all analyses for that applicant) or correlation_id (a specific request). Returns subject and conclusion from each semantic analysis. Use for 'isar evidence', 'evidence chain', or 'analysis history' — NOT for policy gate decisions.",
    "get_forensic_audit": "Return the full chronological timeline of all policy decisions that ever affected a specific applicant.",
    "get_causal_chain": "Return the ordered sequence of all policy decisions evaluated for a single HTTP request identified by its correlation_id.",
    "get_cross_session_history": "Return all sessions that touched a specific applicant and their aggregate outcomes.",
}
