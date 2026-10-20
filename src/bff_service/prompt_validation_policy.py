"""PromptValidationPolicy — input PII detection and unauthorized ID gate for BFF prompts."""

from __future__ import annotations

import re
from typing import Any, Protocol

from mltek.common.log import setup_structured_logging
from mltek.policy.abstract_policy import Policy, PolicyDecision
from mltek.common.infra.audit_decorator import audit_policy_decision

logger = setup_structured_logging(__name__)


# ── Session protocol ───────────────────────────────────────────────────────────

class SessionData(Protocol):
    """Structural interface for session objects passed at call time."""
    applicant_id: str | None
    request_id: str | None


# ── Sensitive data patterns ────────────────────────────────────────────────────

_DEFAULT_EXPLICIT_ID_PATTERNS = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",  # applicant_id
    r"pol_[0-9a-f]{16}",   # policy_id
    r"wf_[0-9a-f]{12}",    # workflow_id
    r"req_[0-9a-f]{16}",   # request_id
)

_SENSITIVE_DATA_PATTERNS = [
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("Credit Card", re.compile(r"\b(?:\d[ -]*?){13,16}\b")),
]

_DIGIT_STRIP_RE = re.compile(r"[^0-9]")


def _sensitive_labels(matches: list[tuple[str, str]]) -> str:
    return ", ".join(sorted({label for label, _ in matches}))


# ── Concrete implementation ────────────────────────────────────────────────────

class PromptValidationPolicy(Policy):
    """Intake-layer policy for onboarding and servicing message validation.

    Checks for sensitive data leakage and unauthorized explicit IDs. Not the
    primary Guardian/routing guard — the resolver has its own Guardian checks.
    Session data is passed at call time only; no BFF-specific types are stored.
    """

    def __init__(self, explicit_id_patterns: tuple[str, ...] = _DEFAULT_EXPLICIT_ID_PATTERNS):
        self._explicit_id_pattern = (
            re.compile("|".join(explicit_id_patterns), re.I)
            if explicit_id_patterns else None
        )

    async def evaluate(self, message: str | None = None, session: Any = None, source: str = "react", env: str = "prod", **kwargs) -> PolicyDecision:
        return await self.validate_onboarding(message, session, source)

    @audit_policy_decision(policy_type="onboarding", operation_kwarg="source")
    async def validate_onboarding(self, message: str | None, session: SessionData, source: str = "react") -> PolicyDecision:
        normalized = (message or "").strip()
        sensitive = self._find_sensitive_data(normalized)
        if sensitive:
            labels = _sensitive_labels(sensitive)
            policy = PolicyDecision(allowed=False, reason=f"Sensitive data detected ({labels}). Remove it from the prompt before continuing.")
            logger.info("Policy Decision - blocked sensitive data=%s source=%s prompt_len=%d", labels, source, len(normalized))
            return policy
        policy = self._validate_policy(normalized, source)
        logger.info("Policy Decision - allowed=%s source=%s prompt_len=%d", policy.allowed, source, len(normalized))
        return policy

    @audit_policy_decision(policy_type="servicing", operation_kwarg="source")
    async def validate_servicing(self, message: str | None, session: SessionData, source: str = "react") -> PolicyDecision:
        normalized = (message or "").strip()
        unauthorized = self._find_unauthorized_ids(normalized, session)
        if unauthorized:
            detail = ", ".join(unauthorized)
            policy = PolicyDecision(allowed=False, reason=f"Unauthorized identifier detected in servicing prompt: {detail}.")
            logger.info("Policy Decision - allowed=%s source=%s prompt_len=%d unauthorized=%s", policy.allowed, source, len(normalized), unauthorized)
            return policy
        policy = self._validate_policy(normalized, source)
        logger.info("Policy Decision - allowed=%s source=%s prompt_len=%d", policy.allowed, source, len(normalized))
        return policy

    @audit_policy_decision(policy_type="intake_response", operation_kwarg="source")
    async def validate_agent_response(self, response: str, source: str = "react") -> PolicyDecision:
        normalized = (response or "").strip()
        sensitive = self._find_sensitive_data(normalized)
        if sensitive:
            labels = _sensitive_labels(sensitive)
            policy = PolicyDecision(allowed=False, reason=f"Sensitive data detected ({labels}). Remove it from the response before continuing.")
            logger.info("Policy Decision - blocked agent response sensitive data=%s source=%s response_len=%d", labels, source, len(normalized))
            return policy
        policy = PolicyDecision(allowed=True, reason="")
        logger.info("Policy Decision - allowed agent response source=%s response_len=%d", source, len(normalized))
        return policy

    def _find_unauthorized_ids(self, prompt: str, session: SessionData) -> list[str]:
        if not prompt or self._explicit_id_pattern is None:
            return []
        # Only enforce ID authorization once applicant_id is established on the session.
        # Before that the user may legitimately reference any ID to identify the applicant.
        if not session.applicant_id:
            return []
        matching = [m.group(0) for m in self._explicit_id_pattern.finditer(prompt)]
        if not matching:
            return []
        authorized = {v.lower() for v in (session.applicant_id, session.request_id) if v}
        return [token for token in matching if token.lower() not in authorized]

    def _find_sensitive_data(self, prompt: str) -> list[tuple[str, str]]:
        if not prompt:
            return []
        sensitive = []
        for label, pattern in _SENSITIVE_DATA_PATTERNS:
            for match in pattern.finditer(prompt):
                value = match.group(0)
                if label == "Credit Card" and not self._is_valid_credit_card_number(value):
                    continue
                sensitive.append((label, value))
        return sensitive

    def _is_valid_credit_card_number(self, value: str) -> bool:
        digits = _DIGIT_STRIP_RE.sub("", value)
        if len(digits) < 13 or len(digits) > 16:
            return False
        total = 0
        for idx, dig in enumerate(digits[::-1]):
            n = int(dig)
            if idx % 2 == 1:
                n *= 2
                if n > 9:
                    n -= 9
            total += n
        return total % 10 == 0

    def _validate_policy(self, intent: str, source: str) -> PolicyDecision:
        snippet = intent[:120].replace("\n", " ") if intent else ""
        policy = PolicyDecision(allowed=True, reason="Prompt passed policy checks", metadata={"source": source, "intent_snippet": snippet, "intent_len": len(intent)})
        logger.debug("Policy Decision - allowed=%s source=%s intent_len=%d", policy.allowed, source, len(intent))
        return policy
