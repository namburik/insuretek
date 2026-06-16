DECISION_SYSTEM = (
    "You are an insurance underwriting risk analyst reviewing workflow approval results. "
    "APPROVE: all checks satisfactory, low risk. "
    "ESCALATE: anomalies or edge cases requiring human review. "
    "REJECT: clear risk signals, contradictions, or policy violations. "
    "Respond with ONLY a JSON object, no extra text: "
    '{"risk_score": <0.0-1.0>, "risk_reasoning": "<text>", "decision": "APPROVE|REJECT|ESCALATE"}'
)

DECISION_USER = (
    "Applicant ID: {applicant_id}\n"
    "Applicant context: {applicant_context}\n\n"
    "Gate-check results:\n{summaries}\n\n"
    "All essential gate checks have COMPLETED. "
    "Review the response payloads for quality, internal consistency, and risk signals."
)

ACTIVATE_APPLICANT_PROMPT = "bind policy for applicant {applicant_id} and set status to Active."

ORCHESTRATOR_STATUS_BY_REQUEST_PROMPT = (
    "Get the orchestrator underwriting job status for request_id={request_id}. "
    "Show each step result (PASS/FAIL/SKIP) and any HITL suspension detail."
)

ORCHESTRATOR_STATUS_BY_APPLICANT_PROMPT = (
    "Get the orchestrator underwriting job status for applicant_id={applicant_id}. "
    "Show each step result (PASS/FAIL/SKIP) and any HITL suspension detail."
)

def build_orchestrator_status_prompt(*, request_id: str | None = None, applicant_id: str | None = None) -> str:
    if request_id:
        return ORCHESTRATOR_STATUS_BY_REQUEST_PROMPT.format(request_id=request_id)
    if applicant_id:
        return ORCHESTRATOR_STATUS_BY_APPLICANT_PROMPT.format(applicant_id=applicant_id)
    raise ValueError("Either request_id or applicant_id must be provided")


def build_decision_prompt(applicant_id: str, applicant_context: str, summaries: str) -> str:
    return DECISION_USER.format(
        applicant_id=applicant_id,
        applicant_context=applicant_context,
        summaries=summaries,
    )
