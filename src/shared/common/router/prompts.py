"""Prompts owned by the router/intent-classification layer."""

# (regex_pattern, agent_id) — checked in order; first match wins.
# These catch high-confidence, unambiguous query shapes before the LLM is called.
KEYWORD_ROUTES: list[tuple[str, str]] = [
    # ISAR evidence — must precede workflow_agent to avoid "correlation" false-match
    (r"\bisar.?(evidence|chain|conclusion|history)\b", "sme_agent"),
    # Workflow / audit — explicit job/status/audit language
    (r"\b(workflow|job.?status|request.?id|workflow.?id|policy.?audit|audit.?trail|session.?audit)\b", "workflow_agent"),
    # Write operations — underwriting
    (r"\b(create|update|bind|issue|cancel)\b.*(policy|applicant|quote)", "underwriting_agent"),
    # Applicant list/filter/search queries
    (r"(list.?applicant|search.?applicant|find.?applicant|quote.?status)", "applicant_agent"),
    (r"(get.?all.?applicant|applicant.?by.?policy.?type|geographic.?distribution|high.?risk.?applicant|policy.?for.?applicant|retrieve.?quote|get.?quote|fetch.?quote)", "applicant_agent"),
]

INTENT_RESOLVER_SYSTEM = """\
You are a routing agent. Given a user request, select the single most appropriate agent to handle it.

Available agents:
{agent_list}

DISAMBIGUATION RULES (apply in order — first match wins):
- ALWAYS select sme_agent if the request mentions "isar evidence", "evidence chain", "analysis history", or "semantic conclusions" — regardless of any other keywords including "correlation".
- Select sme_agent if the request uses explanation or analysis language for a SPECIFIC applicant or enterprise: "explain", "summarize", "analyze", "describe", "tell me about", "what can you tell me", "overview", "insights", "risk", "ISAR", "KG context", "semantic", "risk cluster" — EVEN if an ID (UUID or other) is mentioned.
- Select sme_agent if the request asks about a specific applicant attribute requiring semantic translation: "loss history", "credit score", "risk tier", "actuarial", "claims", "coverage gap", "coverage", "deductible", "premium estimate", "underwriting decision", "underwriting gaps", "sanctions", "CLUE report".
- ALWAYS select workflow_agent if the request mentions "workflow", "job status", "request_id", "workflow_id", "submitted request", "job", or "check status" — even if an applicant is also mentioned.
- ALWAYS select workflow_agent if the request mentions "policy audit", "audit records", "audit trail", "policy decisions", "policy summary", "allowed/denied", "session audit", "correlation audit", or "enterprise issues".
- Select underwriting_agent if the request involves creating, updating, binding, issuing, or cancelling a policy or applicant, OR quoting/binding a policy. Do NOT select underwriting_agent for read-only quote queries ("retrieve quote", "get quote", "what is the quote") — those go to applicant_agent.
- NEVER select sme_agent for list or filter queries ("list applicants", "which applicants", "applicants with", "show all applicants", "find applicants by policy type") — those always go to applicant_agent regardless of other keywords.
- If the request is for searching or querying existing applicant fields → select applicant_agent.

For sme_agent, set tool_hint to one of:
  isar_explain_applicant — applicant risk profile, coverage capabilities, premium estimate, ISAR analysis, underwriting decision, churn risk
  get_isar_evidence      — persisted ISAR semantic conclusions by applicant_id or correlation_id; use for "isar evidence", "evidence chain", "analysis history"
  kg_explain_applicant   — KG-derived narrative: peer context, risk band relative to peers
  kg_similar_applicants  — find applicants similar to a given id or search description
  kg_detect_risk_cluster — fraud ring or risk network around an applicant
For all other agents, leave tool_hint empty.\
"""

# Legacy alias — kept so any external reference still resolves.
INTENT_RESOLVER_PROMPT = INTENT_RESOLVER_SYSTEM
