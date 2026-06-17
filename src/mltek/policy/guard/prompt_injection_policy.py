"""PromptInjectionPolicy — two-stage jailbreak detection: regex (always on) + optional LLM scoring."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mltek.common.infra.audit_decorator import audit_policy_decision
from mltek.common.infra.context import with_token_phase, get_active_agent, get_jit_session
from mltek.common.log import setup_structured_logging
from mltek.policy.abstract_policy import Policy, PolicyDecision

logger = setup_structured_logging(__name__)

_DEFAULT_INJECTION_PATTERNS: list[tuple[str, str]] = [
    ("role_override",        r"\b(you are now|act as|pretend (you are|to be)|roleplay as|simulate being)\b"),
    ("instruction_override", r"\b(ignore (previous|all|your|prior)|disregard (all|your|previous)|forget (all|everything|your))\b"),
    ("system_leak",          r"\b(output (your|the) system prompt|print (your )?instructions|reveal (your )?prompt|show (me )?your (system )?prompt)\b"),
    ("jailbreak_keyword",    r"\b(jailbreak|DAN mode|developer mode|unrestricted mode|no restrictions|bypass (safety|filter|guard))\b"),
    ("delimiter_inject",     r"(</?(system|user|assistant|human|instruction)>|\[INST\]|\[/INST\]|<\|im_start\|>|<\|im_end\|>)"),
    ("token_smuggle",        r"(\\u[0-9a-f]{4}.*){4,}"),
    ("indirect_inject",      r"\b(when (you|the AI) (reads?|processes?|sees?)|hidden (instruction|command|directive))\b"),
]


class InjectionScore(BaseModel):
    injection_score: float = Field(description="Likelihood of prompt injection between 0.0 (clean) and 1.0 (certain injection)")
    injection_reason: str = Field(description="One-sentence explanation of the score")




# ── Config ─────────────────────────────────────────────────────────────────────

class PromptInjectionConfig(BaseModel):
    """Prompt injection detection config. All detection constants are configurable.

    hard_block_threshold=1 means any single regex match blocks.
    Patterns are stored as raw strings; compiled_patterns is populated after validation.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    hard_block_threshold: int   = 1
    llm_enabled:          bool  = False
    llm_block_threshold:  float = 0.75
    injection_patterns:   list  = Field(default_factory=lambda: list(_DEFAULT_INJECTION_PATTERNS))
    compiled_patterns:    list  = Field(default_factory=list)

    @model_validator(mode="after")
    def _compile(self) -> "PromptInjectionConfig":
        self.compiled_patterns = [(name, re.compile(p, re.I)) for name, p in self.injection_patterns]
        return self


# ── Policy ─────────────────────────────────────────────────────────────────────

class PromptInjectionPolicy(Policy):
    """Detects prompt injection via regex (stage 1) and optional LLM scoring (stage 2)."""

    def __init__(self, config: PromptInjectionConfig | None = None, system_prompt: str | None = None) -> None:
        self._config = config or PromptInjectionConfig()
        self._system_prompt = system_prompt

    def _stage1_matches(self, text: str) -> list[str]:
        """Return list of matched category names."""
        return [name for name, pattern in self._config.compiled_patterns if pattern.search(text)]

    @with_token_phase("prompt_injection")
    async def _stage2_score(self, text: str) -> tuple[float, str]:
        from mltek.common.agents.lang_agents import call_structured_agent

        # Get system prompt from instance or domain provider
        system_prompt = self._system_prompt
        if not system_prompt:
            try:
                from mltek.common.router.resolver import _domain_provider
                if _domain_provider:
                    system_prompt = _domain_provider.get_system_prompt("prompt_injection")
            except (ImportError, AttributeError):
                pass

        if not system_prompt:
            logger.warning("PromptInjectionPolicy: no system prompt available; defaulting allow")
            return 0.0, "LLM scoring not configured; defaulting allow."

        try:
            result = await call_structured_agent(text, InjectionScore, system_prompt=system_prompt)
            return result.injection_score, result.injection_reason
        except Exception as exc:
            logger.warning("PromptInjectionPolicy LLM stage failed: %s — defaulting allow", exc)
            return 0.0, "LLM scoring unavailable; defaulting allow."

    @audit_policy_decision(policy_type="prompt_injection", operation_kwarg="source")
    async def evaluate(
        self,
        message:    str = "",
        source:     str = "react",
        session_id: str = "",
        **kwargs: Any,
    ) -> PolicyDecision:
        if not message:
            return PolicyDecision(allowed=True, reason="Empty message.")

        cfg        = self._config
        matched    = self._stage1_matches(message)
        agent      = get_active_agent() or "—"
        sid        = (session_id or get_jit_session()[1] or "")[:8] or "—"
        meta_base  = {"source": source, "stage1_matches": matched}

        if len(matched) >= cfg.hard_block_threshold:
            logger.info(
                "PromptInjectionPolicy BLOCKED stage1 source=%s session=%s agent=%s categories=%s",
                source, sid, agent, matched,
            )
            return PolicyDecision(
                allowed=False,
                reason=f"Prompt injection detected ({', '.join(matched)}).",
                metadata={**meta_base, "stage": "regex"},
            )

        if cfg.llm_enabled:
            score, reason = await self._stage2_score(message)
            logger.info(
                "PromptInjectionPolicy stage2 source=%s session=%s agent=%s score=%.2f threshold=%.2f",
                source, sid, agent, score, cfg.llm_block_threshold,
            )
            if score >= cfg.llm_block_threshold:
                return PolicyDecision(
                    allowed=False,
                    reason=f"Prompt injection detected by LLM (score={score:.2f}): {reason}",
                    metadata={**meta_base, "stage": "llm", "score": score, "reason": reason},
                )

        logger.info(
            "PromptInjectionPolicy allowed source=%s session=%s agent=%s msg_len=%d stage1_matches=%s stage1_count=%d llm_scored=%s llm_threshold=%.2f",
            source, sid, agent, len(message), matched or "none",
            len(matched), cfg.llm_enabled, cfg.llm_block_threshold,
        )
        return PolicyDecision(
            allowed=True,
            reason="No injection detected.",
            metadata=meta_base,
        )
