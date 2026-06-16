"""SensitiveRedactionPolicy — scrub or block sensitive data (SSN, routing/account numbers, phone, CC) in agent responses."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel

from shared.common.infra.audit_decorator import audit_policy_decision
from shared.common.log import setup_structured_logging
from shared.policy.abstract_policy import Policy, PolicyDecision

logger = setup_structured_logging(__name__)

# ── UUID masking — applied before PII patterns to protect structured IDs ────────
# Covers standard hyphenated UUIDs; space/dot variants handled by pre-mask pass.
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

# ── PII patterns ───────────────────────────────────────────────────────────────

_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# ABA routing number: exactly 9 digits, first digit 0-3
_ROUTING_RE = re.compile(r"\b[0-3]\d{8}\b")

# Bank account: 8–17 digits, not routing-shaped (9 digits starting 0-3), not a hyphenated-ID segment
_ACCOUNT_RE = re.compile(r"\b(?<!-)(?![0-3]\d{8}\b)\d{8,17}(?!-)\b")

# E.164 and NANP phone numbers — (?<!\d) prevents matching mid-digit-run
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[\s\-.]?)?"
    r"\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}\b"
)

# Credit card: 13-16 digits (validated by Luhn in intake_policy; here pattern only)
_CC_RE = re.compile(r"\b(?:\d[ -]*?){13,16}\b")

_DIGIT_STRIP_RE = re.compile(r"[^0-9]")

_PII_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("SSN",             _SSN_RE,     "[REDACTED-SSN]"),
    ("RoutingNumber",   _ROUTING_RE, "[REDACTED-ROUTING]"),
    ("AccountNumber",   _ACCOUNT_RE, "[REDACTED-ACCOUNT]"),
    ("Phone",           _PHONE_RE,   "[REDACTED-PHONE]"),
    ("CreditCard",      _CC_RE,      "[REDACTED-CC]"),
]


def _mask_uuids(text: str) -> tuple[str, dict[str, str]]:
    store: dict[str, str] = {}
    def _sub(m: re.Match) -> str:
        key = f"\x00UUID{len(store)}\x00"
        store[key] = m.group(0)
        return key
    return _UUID_RE.sub(_sub, text), store

def _restore_uuids(text: str, store: dict[str, str]) -> str:
    for key, val in store.items():
        text = text.replace(key, val)
    return text


def _luhn_valid(value: str) -> bool:
    digits = _DIGIT_STRIP_RE.sub("", value)
    if not 13 <= len(digits) <= 16:
        return False
    total = 0
    for idx, d in enumerate(digits[::-1]):
        n = int(d)
        if idx % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _scan(text: str) -> list[tuple[str, str]]:
    """Return list of (label, matched_value) for all PII found."""
    masked, _ = _mask_uuids(text)
    found: list[tuple[str, str]] = []
    for label, pattern, _ in _PII_PATTERNS:
        for m in pattern.finditer(masked):
            val = m.group(0)
            if label == "CreditCard" and not _luhn_valid(val):
                continue
            found.append((label, val))
    return found


def _scrub(text: str) -> str:
    """Replace all PII matches with redaction markers."""
    masked, store = _mask_uuids(text)
    for label, pattern, marker in _PII_PATTERNS:
        if label == "CreditCard":
            def _replace_cc(m, _marker=marker):
                return _marker if _luhn_valid(m.group(0)) else m.group(0)
            masked = pattern.sub(_replace_cc, masked)
        else:
            masked = pattern.sub(marker, masked)
    return _restore_uuids(masked, store)


# ── Config ─────────────────────────────────────────────────────────────────────

class SensitiveRedactionConfig(BaseModel):
    """PII egress config. mode='scrub' redacts in-place (default); mode='block' denies the response."""
    mode: Literal["block", "scrub"] = "scrub"


# ── Policy ─────────────────────────────────────────────────────────────────────

class SensitiveRedactionPolicy(Policy):
    """Scans responses for PII (SSN, routing, account, phone, CC). Scrub mode returns metadata['scrubbed_text']."""

    def __init__(self, config: SensitiveRedactionConfig | None = None) -> None:
        self._config = config or SensitiveRedactionConfig()

    @staticmethod
    def applies(text: str) -> bool:
        """Return True only if there is text to scan."""
        return bool(text)

    @audit_policy_decision(policy_type="sensitive_redact", operation_kwarg="source")
    async def evaluate(
        self,
        text:       str = "",
        source:     str = "agent_response",
        session_id: str = "",
        agent_id:   str = "",
        **kwargs: Any,
    ) -> PolicyDecision:
        if not text:
            return PolicyDecision(allowed=True, reason="Empty text — no PII check needed.")

        found = _scan(text)
        if not found:
            logger.info(
                "SensitiveRedactionPolicy clean: source=%s agent=%s session=%s len=%d",
                source, agent_id or "—", session_id[:8] if session_id else "—", len(text),
            )
            return PolicyDecision(
                allowed=True,
                reason="No PII detected.",
                metadata={"source": source, "pii_labels": []},
            )

        labels = sorted({label for label, _ in found})
        logger.info(
            "SensitiveRedactionPolicy detected: source=%s agent=%s session=%s labels=%s count=%d",
            source, agent_id or "—", session_id[:8] if session_id else "—", labels, len(found),
        )

        if self._config.mode == "block":
            return PolicyDecision(
                allowed=False,
                reason=f"PII detected in response ({', '.join(labels)}). Response suppressed.",
                metadata={"source": source, "pii_labels": labels, "match_count": len(found)},
            )

        scrubbed = _scrub(text)
        logger.info(
            "SensitiveRedactionPolicy scrubbed: source=%s agent=%s session=%s labels=%s",
            source, agent_id or "—", session_id[:8] if session_id else "—", labels,
        )
        return PolicyDecision(
            allowed=True,
            reason=f"PII scrubbed from response ({', '.join(labels)}).",
            metadata={
                "source":        source,
                "pii_labels":    labels,
                "match_count":   len(found),
                "scrubbed_text": scrubbed,
            },
        )
