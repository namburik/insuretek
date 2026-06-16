"""Guardian — threat detection, reflection, and self-correction loop."""

from __future__ import annotations

import asyncio
import re
from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field
from shared.common.agents.bridge import traceable

from shared.common.agents.prompts import CRITIC_SYSTEM_PROMPT, REFORMULATOR_SYSTEM_PROMPT
from shared.common.log import setup_structured_logging
from shared.policy.abstract_policy import Policy, PolicyDecision
from shared.common.infra.audit_decorator import audit_policy_decision

logger = setup_structured_logging(__name__)


# ── Reflection models ──────────────────────────────────────────────────────────

@dataclass
class ReflectionResult:
    allowed: bool
    explanation: str
    iterations: int
    blocking_reason: str
    action: Literal["fix", "pass_through", "block"] = "block"
    corrected_intent: str | None = None


class ReflectionConfig(BaseModel):
    """Configurable knobs for the reflection policy."""

    max_iterations: int = 3
    pass_through_on_cannot_correct: bool = True
    reflection_timeout_secs: float = 10.0

# ── Token counting ─────────────────────────────────────────────────────────────

try:
    import tiktoken
    _tokenizer = tiktoken.get_encoding("cl100k_base")
    def _count_tokens(text: str) -> int:
        return len(_tokenizer.encode(text))
except Exception:
    def _count_tokens(text: str) -> int:  # type: ignore[misc]
        return len(text) // 4


# ── Abstract bases ─────────────────────────────────────────────────────────────

class GuardianPolicy(Policy):
    """Policy contract for routing safety and pre-agent checks."""

    @abstractmethod
    async def evaluate(
        self,
        intent: str,
        agent_id: str,
        source: str = "unknown",
        env: str = "prod",
        **kwargs: Any,
    ) -> PolicyDecision:
        ...


class ReflectionPolicy(Policy):
    """Policy contract for blocked intent remediation and self-correction."""

    @abstractmethod
    async def evaluate(
        self,
        original_intent: str,
        blocking_reason: str,
        agent_id: str,
        source: str = "unknown",
        env: str = "prod",
        **kwargs: Any,
    ) -> PolicyDecision:
        ...


# ── Configuration ──────────────────────────────────────────────────────────────

class GuardianConfig(BaseModel):
    """All Guardian trigger rules in one place. Override any field to adjust behavior."""

    token_limit: int = 1_000
    checked_sources: frozenset[str] = Field(default_factory=lambda: frozenset({"react"}))
    unguarded_agents: frozenset[str] = Field(default_factory=lambda: frozenset({
        "applicant_agent", "workflow_agent", "sme_agent",
    }))
    bulk_destructive_verbs: tuple[str, ...] = (
        "delete", "harm", "remove", "drop", "deactivate", "purge", "wipe", "clear",
        "revoke", "cancel", "terminate", "suspend", "truncate", "erase",
        "destroy", "reset", "archive", "invalidate", "flush",
    )
    bulk_quantifiers: tuple[str, ...] = ("all", "every", "bulk")
    destructive_verbs: tuple[str, ...] = (
        "delete", "harm", "remove", "drop", "deactivate", "purge", "revoke", "cancel",
        "terminate", "suspend", "void", "erase", "destroy", "truncate", "reset",
        "archive", "invalidate", "flush",
    )
    write_verbs: tuple[str, ...] = (
        "update", "modify", "change", "set", "rotate", "edit", "disable",
    )
    scope_keywords: tuple[str, ...] = (
        # Core entities
        "applicant", "policy", "workflow", "audit",
        "request", "underwriting", "status", "session", "evidence", "ISAR",
        # Insurance-specific
        "coverage", "risk", "premium", "claim", "renewal", "quote",
        "bind", "deductible", "tier", "underwrite",
        # Applicant attributes
        "account", "enterprise", "contact", "location",
        # Operational
        "job", "step", "guardian", "route", "intake",
        # Data & analytics
        "summary", "detail", "report", "insight", "distribution", "similar",
    )
    name_fields: tuple[str, ...] = ("applicant_name", "name")
    explicit_id_patterns: tuple[str, ...] = (
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        r"pol_[a-z0-9]{8,}",
    )


# ── Compiled rules ─────────────────────────────────────────────────────────────

@dataclass
class _CompiledRules:
    bulk_pattern: re.Pattern
    destructive_pattern: re.Pattern
    write_pattern: re.Pattern
    name_field_value_pattern: re.Pattern
    name_field_prefix_pattern: re.Pattern
    explicit_id_pattern: re.Pattern
    scope_pattern: re.Pattern

    @classmethod
    def from_config(cls, cfg: GuardianConfig) -> "_CompiledRules":
        bulk_v  = "|".join(re.escape(v) for v in cfg.bulk_destructive_verbs)
        bulk_q  = "|".join(re.escape(q) for q in cfg.bulk_quantifiers)
        destr_v = "|".join(re.escape(v) for v in cfg.destructive_verbs)
        write_v = "|".join(re.escape(v) for v in cfg.write_verbs)
        scope_v = "|".join(re.escape(k) for k in cfg.scope_keywords)

        nf_bare   = "|".join(re.escape(f) for f in cfg.name_fields)
        nf_spaced = "|".join(
            re.escape(f.replace("_", " ")) for f in cfg.name_fields if "_" in f
        )

        nf_value = (
            rf"""[\"']?\b({nf_bare})\b[\"']?\s*:\s*[\"'][^\"'\n]*\b(?:{destr_v})\b"""
        )
        nf_prefix = rf"""[\"']?\b({nf_bare})\b[\"']?\s*[:\-]\s*$"""
        if nf_spaced:
            nf_prefix += rf"""|\b({nf_spaced})\s*[:\-]\s*$"""

        return cls(
            bulk_pattern=re.compile(rf"\b({bulk_v})\s+({bulk_q})\b", re.I),
            destructive_pattern=re.compile(rf"\b({destr_v})\b", re.I),
            write_pattern=re.compile(rf"\b({write_v})\b", re.I),
            name_field_value_pattern=re.compile(nf_value, re.I),
            name_field_prefix_pattern=re.compile(nf_prefix, re.I),
            explicit_id_pattern=re.compile("|".join(cfg.explicit_id_patterns), re.I),
            scope_pattern=re.compile(rf"\b({scope_v})\b", re.I),
        )


# ── Match classification ───────────────────────────────────────────────────────

def _classify_destructive_matches(
    intent: str,
    rules: _CompiledRules,
) -> tuple[list[str], list[str]]:
    operational: list[str] = []
    name_field: list[str] = []
    for m in rules.destructive_pattern.finditer(intent):
        line_start = intent.rfind("\n", 0, m.start()) + 1
        line_end   = intent.find("\n", m.end())
        if line_end == -1:
            line_end = len(intent)
        line   = intent[line_start:line_end]
        prefix = intent[line_start:m.start()]
        if rules.name_field_value_pattern.search(line) or \
                rules.name_field_prefix_pattern.search(prefix):
            name_field.append(m.group(0))
        else:
            operational.append(m.group(0))
    return operational, name_field


# ── Decision model ─────────────────────────────────────────────────────────────

@dataclass
class GuardianDecision:
    allowed: bool
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    reason: str
    needs_reflection: bool = False


# ── Guardian agent ─────────────────────────────────────────────────────────────

class GuardianAgent(GuardianPolicy):
    """Pure pattern-based threat detector — no LLM calls, no fixing.

    Outcomes:
      allow            — no threat patterns; operation proceeds.
      block            — hard threat confirmed; operation rejected immediately.
      needs_reflection — ambiguous pattern (name-field verb or destructive+ID);
                         caller invokes ReflectionPolicy before deciding.
    """

    def __init__(self, config: GuardianConfig | None = None):
        self._config = config or GuardianConfig()
        self._rules  = _CompiledRules.from_config(self._config)

    def is_unguarded(self, agent_id: str) -> bool:
        """True for agents exempt from post-execution checks (e.g. LLMJudge)."""
        return agent_id in self._config.unguarded_agents

    @traceable(name="guardian_check", run_type="chain")
    async def check(
        self,
        intent: str,
        agent_id: str,
        source: str = "unknown",
        env: str = "prod",
    ) -> GuardianDecision:
        cfg         = self._config
        rules       = self._rules
        token_count = _count_tokens(intent)
        logger.info(
            "Guardian check: agent=%s source=%s tokens=%d",
            agent_id, source, token_count,
        )

        if cfg.checked_sources and source not in cfg.checked_sources:
            logger.debug("Guardian skipped: agent=%s source=%s not in checked_sources", agent_id, source)
            return GuardianDecision(
                allowed=True, risk_level="LOW",
                reason=f"Source '{source}' is not subject to Guardian checks.",
            )

        if cfg.scope_keywords and not rules.scope_pattern.search(intent):
            logger.info("Guardian blocked: out-of-scope intent agent=%s", agent_id)
            return GuardianDecision(
                allowed=False, risk_level="LOW",
                reason="Out of scope. Try asking about an applicant, workflow, policy, or audit.",
            )

        if token_count >= cfg.token_limit:
            logger.info("Guardian blocked: token limit agent=%s tokens=%d", agent_id, token_count)
            return GuardianDecision(
                allowed=False, risk_level="HIGH",
                reason=f"Intent exceeds {cfg.token_limit}-token limit ({token_count} tokens).",
            )

        if agent_id in cfg.unguarded_agents:
            logger.info("Guardian skipped: read-only agent=%s", agent_id)
            return GuardianDecision(
                allowed=True, risk_level="LOW",
                reason="Read-only agent; no write-risk checks required.",
            )

        if rules.bulk_pattern.search(intent):
            logger.info("Guardian blocked: bulk destructive agent=%s", agent_id)
            return GuardianDecision(
                allowed=False, risk_level="HIGH",
                reason="Bulk destructive operations are prohibited.",
            )

        operational, name_field = _classify_destructive_matches(intent, rules)
        logger.debug(
            "Guardian matches: operational=%s name_field=%s agent=%s",
            operational, name_field, agent_id,
        )

        if len(operational) >= 2:
            logger.info("Guardian blocked: multi-destructive agent=%s ops=%s", agent_id, operational)
            return GuardianDecision(
                allowed=False, risk_level="HIGH",
                reason="Combined destructive operations require separate authorization.",
            )

        has_operational = bool(operational)
        has_explicit_id = bool(rules.explicit_id_pattern.search(intent))

        if has_operational and not has_explicit_id and not name_field:
            logger.info("Guardian blocked: destructive+no-id agent=%s", agent_id)
            return GuardianDecision(
                allowed=False, risk_level="HIGH",
                reason="Destructive operations require an explicit resource ID.",
            )

        name_field_words = {v.lower() for v in name_field}
        operational_write = [
            m.group(0) for m in rules.write_pattern.finditer(intent)
            if m.group(0).lower() not in name_field_words
        ]
        if operational_write:
            logger.info("Guardian blocked: write verb agent=%s verbs=%s", agent_id, operational_write)
            return GuardianDecision(
                allowed=False, risk_level="HIGH",
                reason="Update and deactivate operations are temporarily blocked.",
            )

        if name_field or (has_operational and has_explicit_id):
            reason = (
                "Destructive verb in name-field context; Reflection required to disambiguate."
                if name_field else
                "Destructive operation scoped to explicit ID; Reflection required for context judgment."
            )
            logger.info("Guardian escalating to Reflection: agent=%s reason=%s", agent_id, reason)
            return GuardianDecision(
                allowed=False, risk_level="MEDIUM",
                needs_reflection=True, reason=reason,
            )

        logger.info("Guardian allowed: no threat patterns detected agent=%s", agent_id)
        return GuardianDecision(
            allowed=True, risk_level="LOW",
            reason="No threat patterns detected.",
        )

    @audit_policy_decision(policy_type="guardian", operation_kwarg="agent_id")
    async def evaluate(self, intent: str, agent_id: str, source: str = "unknown", env: str = "prod", **kwargs) -> PolicyDecision:
        decision = await self.check(intent, agent_id, source, env)
        logger.info("GuardianAgent.evaluate exit: agent=%s allowed=%s risk=%s", agent_id, decision.allowed, decision.risk_level)
        return PolicyDecision(
            allowed=decision.allowed,
            reason=decision.reason,
            metadata={
                "risk_level": decision.risk_level,
                "needs_reflection": decision.needs_reflection,
            },
        )


# ── Reflection models ──────────────────────────────────────────────────────────

class CriticResult(BaseModel):
    can_correct: bool = Field(description="False if reformulation is structurally impossible")
    diagnosis: str = Field(description="One of: RESTRICTED_NAME_WORD, BULK_DESTRUCTIVE, MULTI_DESTRUCTIVE, MISSING_ID, WRITE_VERB, TOKEN_LIMIT, UNKNOWN")
    reason: str = Field(description="One sentence explaining which pattern triggered the block")

class ReformulatorResult(BaseModel):
    can_reformulate: bool = Field(description="False if no safe compliant rewrite is possible")
    reformulated_intent: str = Field(default="", description="Full corrected intent; empty if can_reformulate is false")
    reason: str = Field(description="One sentence explaining what was corrected, or why reformulation failed")


_NAME_FIELD_PATTERN = re.compile(
    r"[\"']?\b(applicant_name|name)\b[\"']?\s*[:\-]\s*\S"
    r"|\b(applicant name|named?)\s+[A-Za-z]",
    re.I,
)
_DESTRUCTIVE_WORD_PATTERN = re.compile(
    r"\b(delete|remove|drop|deactivate|harm|purge|revoke|cancel|terminate"
    r"|suspend|void|erase|destroy|truncate|reset|archive|invalidate|flush)\b",
    re.I,
)
_CREATE_VERB_PATTERN = re.compile(
    r"\b(create|add|register|onboard|setup|set up|new)\b", re.I
)
_EMAIL_DOMAIN_RE  = re.compile(r"@([\w.-]+)")
_CLIENT_ID_RE     = re.compile(r"client_id\s*[:\-]\s*client_([a-zA-Z]+)", re.I)
_APPLICANT_ID_RE  = re.compile(r"applicant_id\s*[:\-]\s*applicant_([a-zA-Z]+)", re.I)
_ENT_ID_RE        = re.compile(r"\bENT-([A-Za-z]+)")


def _name_context_hint(intent: str) -> str:
    """Return a hint line when a restricted word appears as a name in a create intent."""
    if not _CREATE_VERB_PATTERN.search(intent):
        return ""
    if not _NAME_FIELD_PATTERN.search(intent):
        return ""

    for line in intent.splitlines():
        if not _NAME_FIELD_PATTERN.search(line):
            continue
        restricted = _DESTRUCTIVE_WORD_PATTERN.search(line)
        if not restricted:
            continue

        signals: list[str] = []
        email_m = _EMAIL_DOMAIN_RE.search(intent)
        if email_m:
            domain_root = email_m.group(1).split(".")[0]
            signals.append(f"email domain '{email_m.group(1)}' suggests name '{domain_root.capitalize()}'")
    
        mid_m = _APPLICANT_ID_RE.search(intent)
        if mid_m:
            signals.append(f"applicant_id prefix '{mid_m.group(1)}' suggests name '{mid_m.group(1).capitalize()}'")
        

        hint_body = "; ".join(signals) if signals else "no additional context signals found"
        return (
            f"HINT: The word '{restricted.group(0)}' appears inside a name field "
            f"('{line.strip()}') — this looks like a RESTRICTED_NAME_WORD case, not a "
            f"destructive operation. Context signals: {hint_body}."
        )

    return ""


# ── Guardian reflection ────────────────────────────────────────────────────────

class GuardianReflectionPolicy(ReflectionPolicy):
    """Self-correction loop for Guardian-escalated intents.

    Guardian only calls this for ambiguous cases (name-field verb or destructive+ID).
    Hard-blocked intents never reach here.
    """

    def __init__(
        self,
        guardian,
        config: ReflectionConfig | None = None,
    ):
        self._guardian = guardian
        self._config   = config or ReflectionConfig()

    @traceable(name="guardian_reflection", run_type="chain")
    async def handle_blocked(
        self,
        original_intent: str,
        blocking_reason: str,
        agent_id: str,
        source: str = "react",
        env: str = "prod",
    ) -> ReflectionResult:
        from shared.common.agents.lang_agents import call_structured_agent
        cfg = self._config
        logger.info("Reflection starting: agent=%s reason=%s", agent_id, blocking_reason)

        hint      = _name_context_hint(original_intent)
        base_task = "\n".join(filter(None, [
            f"BLOCKED INTENT: {original_intent}",
            f"GUARDIAN REASON: {blocking_reason}",
            hint,
            "Diagnose which rule fired.",
        ]))

        try:
            critic = await call_structured_agent(base_task, CriticResult, system_prompt=CRITIC_SYSTEM_PROMPT)
        except Exception as exc:
            logger.warning("Reflection critic failed: %s — blocking fail-closed", exc)
            return ReflectionResult(action="block", allowed=False, explanation="critic unavailable — failing closed", iterations=0, blocking_reason=blocking_reason)

        logger.debug("Critic: can_correct=%s diagnosis=%s reason=%s", critic.can_correct, critic.diagnosis, critic.reason)

        if not critic.can_correct:
            if cfg.pass_through_on_cannot_correct:
                logger.info("Reflection pass-through: cannot_correct, no hard harm agent=%s", agent_id)
                return ReflectionResult(
                    action="pass_through",
                    allowed=True,
                    explanation=f"No harmful operation confirmed; forwarding as-is. ({critic.reason})",
                    iterations=0,
                    blocking_reason=blocking_reason,
                )
            return ReflectionResult(action="block", allowed=False, explanation=critic.reason, iterations=0, blocking_reason=blocking_reason)

        reformulator_input = (
            f"Original blocked intent:\n{original_intent}\n\n"
            f"Critic diagnosis: {critic.diagnosis} — {critic.reason}"
        )

        for iteration in range(1, cfg.max_iterations + 1):
            try:
                reform = await call_structured_agent(reformulator_input, ReformulatorResult, system_prompt=REFORMULATOR_SYSTEM_PROMPT)
            except Exception as exc:
                logger.warning("Reflection reformulator failed iter=%d: %s — blocking", iteration, exc)
                return ReflectionResult(action="block", allowed=False, explanation="reformulator unavailable", iterations=iteration, blocking_reason=blocking_reason)

            logger.debug("Reformulator [iter=%d]: can_reformulate=%s", iteration, reform.can_reformulate)

            if not reform.can_reformulate:
                return ReflectionResult(action="block", allowed=False, explanation=reform.reason, iterations=iteration, blocking_reason=blocking_reason)

            if critic.diagnosis == "RESTRICTED_NAME_WORD" and _CREATE_VERB_PATTERN.search(reform.reformulated_intent):
                logger.info("Reflection name-word fix accepted (iter=%d)", iteration)
                return ReflectionResult(
                    action="fix", allowed=True, corrected_intent=reform.reformulated_intent,
                    explanation=f"Restricted name word corrected after {iteration} iteration(s).",
                    iterations=iteration, blocking_reason=blocking_reason,
                )

            decision = await self._guardian.check(reform.reformulated_intent, agent_id, source=source)
            if decision.allowed:
                logger.info("Reflection fix approved (iter=%d)", iteration)
                return ReflectionResult(
                    action="fix", allowed=True, corrected_intent=reform.reformulated_intent,
                    explanation=f"Intent reformulated after {iteration} iteration(s).",
                    iterations=iteration, blocking_reason=blocking_reason,
                )

            reformulator_input = (
                f"Original blocked intent:\n{original_intent}\n\n"
                f"Critic diagnosis: {critic.diagnosis} — {critic.reason}\n\n"
                f"Previous attempt (rejected):\n{reform.reformulated_intent}\n"
                f"Guardian rejection: {decision.reason}\n"
                "Produce a different compliant rewrite, or set can_reformulate=false if impossible."
            )

        return ReflectionResult(
            action="block", allowed=False,
            explanation=f"Could not produce a compliant reformulation after {cfg.max_iterations} iteration(s). Original block: {blocking_reason}",
            iterations=cfg.max_iterations, blocking_reason=blocking_reason,
        )

    @audit_policy_decision(policy_type="reflection", operation_kwarg="agent_id")
    async def evaluate(self, original_intent: str, blocking_reason: str, agent_id: str, source: str = "unknown", env: str = "prod", **kwargs) -> PolicyDecision:
        try:
            result = await asyncio.wait_for(
                self.handle_blocked(original_intent, blocking_reason, agent_id, source, env),
                timeout=self._config.reflection_timeout_secs,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "GuardianReflectionPolicy TIMEOUT agent=%s timeout=%.1fs — blocking (fail-closed)",
                agent_id, self._config.reflection_timeout_secs,
            )
            return PolicyDecision(
                allowed=False,
                reason=f"Reflection safety check timed out after {self._config.reflection_timeout_secs}s; request blocked to maintain safety.",
                metadata={"action": "block", "iterations": 0, "corrected_intent": None},
            )
        logger.info("GuardianReflectionPolicy.evaluate exit: agent=%s action=%s allowed=%s iter=%d", agent_id, result.action, result.allowed, result.iterations)
        return PolicyDecision(
            allowed=result.allowed,
            reason=result.explanation,
            metadata={
                "action": result.action,
                "iterations": result.iterations,
                "corrected_intent": result.corrected_intent,
            },
        )
