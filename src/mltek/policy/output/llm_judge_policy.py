"""LLMJudge — post-execution semantic correctness check; surfaces JudgeDecision to caller, takes no automatic action."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from mltek.common.agents.bridge import traceable
from mltek.common.log import setup_structured_logging

from mltek.policy.abstract_policy import Policy, PolicyDecision
from mltek.common.infra.audit_decorator import audit_policy_decision
from mltek.common.infra.context import with_token_phase

logger = setup_structured_logging(__name__)


class LLMJudgePolicy(Policy):
    """Policy contract for post-response correctness judgement."""

    @abstractmethod
    async def evaluate(
        self,
        prompt: str,
        response: str,
        source: str = "unknown",
        env: str = "prod",
        **kwargs: Any,
    ) -> PolicyDecision:
        ...


class JudgeConfig(BaseModel):
    """LLMJudge configuration. Load from YAML judge: section."""
    enabled: bool = False
    model: str | None = None


class JudgeDecision(BaseModel):
    passed: bool = Field(description="True if the agent response correctly fulfilled the request")
    reason: str = Field(description="One-sentence explanation of the verdict")
    confidence: float = Field(description="Confidence score between 0.0 and 1.0")




class LLMJudge(LLMJudgePolicy):
    """Post-execution semantic correctness policy module."""

    def __init__(self, config: JudgeConfig, system_prompt: str | None = None):
        self._config = config
        self._system_prompt = system_prompt

    def applies(self, prompt: str, response: str) -> bool:
        """Return True only if judge is enabled and both prompt and response are non-empty."""
        return self._config.enabled and bool(prompt) and bool(response)

    @with_token_phase("judge")
    @traceable(name="llm_judge", run_type="chain")
    async def judge(self, prompt: str, response: str, source: str = "unknown") -> JudgeDecision:
        from mltek.common.agents.lang_agents import call_structured_agent

        # Get system prompt from instance or domain provider
        system_prompt = self._system_prompt
        if not system_prompt:
            try:
                from mltek.common.router.resolver import _domain_provider
                if _domain_provider:
                    system_prompt = _domain_provider.get_system_prompt("llm_judge")
            except (ImportError, AttributeError):
                pass

        if not system_prompt:
            logger.warning("LLMJudge: no system prompt available; failing closed")
            return JudgeDecision(passed=False, reason="judge not configured", confidence=0.0)

        task = f"Original request:\n{prompt}\n\nAgent response:\n{response}"
        try:
            decision = await call_structured_agent(
                task, JudgeDecision, system_prompt=system_prompt, model=self._config.model
            )
        except Exception as exc:
            logger.warning("LLMJudge structured call failed: %s — failing closed", exc)
            return JudgeDecision(passed=False, reason="judge unavailable — failing closed", confidence=0.0)

        logger.info(
            "LLMJudge verdict=%s source=%s confidence=%.2f reason=%s",
            "PASSED" if decision.passed else "FAILED", source, decision.confidence, decision.reason,
        )
        return decision

    @audit_policy_decision(policy_type="llm_judge", operation_kwarg="source")
    async def evaluate(self, prompt: str, response: str, source: str = "unknown", env: str = "prod", **kwargs) -> PolicyDecision:
        if not self._config.enabled:
            return PolicyDecision(allowed=True, reason="LLMJudge disabled.", metadata={"confidence": 1.0})
        decision = await self.judge(prompt, response, source=source)
        logger.info(
            "LLMJudge.evaluate exit: allowed=%s source=%s confidence=%.2f reason=%s prompt_len=%d response_len=%d",
            decision.passed, source, decision.confidence, decision.reason, len(prompt), len(response),
        )
        return PolicyDecision(
            allowed=decision.passed,
            reason=decision.reason,
            metadata={"confidence": decision.confidence},
        )
