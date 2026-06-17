"""GuardianDispatchPolicy — post-route Guardian + Reflection orchestration."""

from __future__ import annotations

from mltek.common.agents.bridge import HumanMessage
from mltek.common.infra.context import with_token_phase
from mltek.common.log import setup_structured_logging
from mltek.policy.abstract_policy import PolicyDecision
from mltek.policy.guard.guardian_policy import GuardianAgent, GuardianReflectionPolicy

logger = setup_structured_logging(__name__)


class GuardianDispatchPolicy:
    """Post-route Guardian evaluation with optional Reflection escalation.

    Returned PolicyDecision metadata keys:
      - guardian   : dict with allowed/risk_level/reason/needs_reflection
      - reflection : dict with action/allowed/iterations/explanation (if ran)
      - messages   : possibly rewritten message list (fix or pass_through action)
    """

    def __init__(
        self,
        guardian: GuardianAgent,
        reflection: GuardianReflectionPolicy | None = None,
    ) -> None:
        self._guardian   = guardian
        self._reflection = reflection

    async def evaluate(
        self,
        intent: str,
        messages: list,
        agent_id: str,
        source: str = "unknown",
        env: str = "prod",
    ) -> PolicyDecision:
        logger.info(
            "GuardianDispatchPolicy: entering check agent=%s source=%s env=%s",
            agent_id, source, env,
        )
        decision = await self._guardian.evaluate(intent, agent_id=agent_id, source=source, env=env)
        risk             = decision.metadata.get("risk_level", "UNKNOWN")
        needs_reflection = decision.metadata.get("needs_reflection", False)
        logger.info(
            "GuardianDispatchPolicy: exited agent=%s allowed=%s risk=%s env=%s",
            agent_id, decision.allowed, risk, env,
        )

        guardian_meta = {
            "allowed":          decision.allowed,
            "risk_level":       risk,
            "reason":           decision.reason,
            "needs_reflection": needs_reflection,
        }

        if needs_reflection:
            return await self._handle_reflection(
                intent, messages, agent_id, source, env, decision.reason, guardian_meta,
            )

        if not decision.allowed:
            logger.warning(
                "GuardianDispatchPolicy BLOCKED: agent=%s risk=%s reason=%s",
                agent_id, risk, decision.reason,
            )
            return PolicyDecision(
                allowed=False,
                reason=decision.reason,
                metadata={"guardian": guardian_meta},
            )

        return PolicyDecision(
            allowed=True,
            reason=decision.reason,
            metadata={"messages": messages, "guardian": guardian_meta},
        )

    @with_token_phase("reflection")
    async def _handle_reflection(
        self,
        intent: str,
        messages: list,
        agent_id: str,
        source: str,
        env: str,
        blocking_reason: str,
        guardian_meta: dict,
    ) -> PolicyDecision:
        logger.info(
            "GuardianDispatchPolicy: escalating to Reflection agent=%s reason=%s",
            agent_id, blocking_reason,
        )
        if self._reflection is None:
            return PolicyDecision(
                allowed=False,
                reason=blocking_reason,
                metadata={"guardian": guardian_meta},
            )

        ref_decision = await self._reflection.evaluate(
            intent, blocking_reason, agent_id, source=source, env=env,
        )
        ref_meta = ref_decision.metadata
        reflection_meta = {
            "action":      ref_meta.get("action"),
            "allowed":     ref_decision.allowed,
            "iterations":  ref_meta.get("iterations"),
            "explanation": ref_decision.reason,
        }

        action = ref_meta.get("action")
        if action == "fix":
            logger.info(
                "GuardianDispatchPolicy: Reflection fix accepted iter=%d",
                ref_meta.get("iterations", 0),
            )
            corrected = ref_meta.get("corrected_intent", intent)
            return PolicyDecision(
                allowed=True,
                reason=ref_decision.reason,
                metadata={
                    "messages":   list(messages[:-1]) + [HumanMessage(content=corrected)],
                    "guardian":   guardian_meta,
                    "reflection": reflection_meta,
                },
            )

        if action == "pass_through":
            logger.info("GuardianDispatchPolicy: Reflection pass-through agent=%s", agent_id)
            return PolicyDecision(
                allowed=True,
                reason=ref_decision.reason,
                metadata={
                    "messages":   messages,
                    "guardian":   guardian_meta,
                    "reflection": reflection_meta,
                },
            )

        return PolicyDecision(
            allowed=False,
            reason=ref_decision.reason,
            metadata={"guardian": guardian_meta, "reflection": reflection_meta},
        )
