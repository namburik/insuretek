"""Policy pipeline — ordered check_intake, check_dispatch, check_response stages."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from mltek.common.log import setup_structured_logging
from mltek.common.infra import RequestContext, OutputAdapter
from mltek.policy.abstract_policy import PolicyDecision
from mltek.policy.access.caller_authorization_policy import (
    CallerAuthConfig, CallerAuthorizationPolicy, CallerRole,
)
from mltek.policy.output.sensitive_redaction_policy import SensitiveRedactionConfig, SensitiveRedactionPolicy
from mltek.policy.guard.prompt_injection_policy import (
    PromptInjectionConfig, PromptInjectionPolicy,
)
from mltek.policy.access.cost_policy import AgentCostConfig, AgentCostPolicy, TokenBudgetConfig, TokenBudgetPolicy
from mltek.policy.access.cost_policy import RateLimitConfig, RateLimitPolicy

logger = setup_structured_logging(__name__)

_ALLOWED = PolicyDecision(allowed=True, reason="All pipeline checks passed.")


# ── Config ─────────────────────────────────────────────────────────────────────

class PolicyPipelineConfig(BaseModel):
    """Pipeline config. Set any sub-config to None to disable that stage."""
    rate_limit:       RateLimitConfig       | None = Field(default_factory=RateLimitConfig)
    prompt_injection: PromptInjectionConfig | None = Field(default_factory=PromptInjectionConfig)
    caller_auth:      CallerAuthConfig      | None = Field(default_factory=CallerAuthConfig)
    agent_cost:       AgentCostConfig       | None = Field(default_factory=AgentCostConfig)
    token_budget:     TokenBudgetConfig     | None = Field(default_factory=TokenBudgetConfig)
    sensitive_redact: SensitiveRedactionConfig | None = Field(default_factory=SensitiveRedactionConfig)

    @classmethod
    def from_dict(cls, d: dict) -> "PolicyPipelineConfig":
        def _sub(key, cls_):
            raw = d.get(key)
            if raw is None:
                return None
            return cls_.model_validate(raw) if raw is not False else None

        return cls(
            rate_limit       = _sub("rate_limit",       RateLimitConfig),
            prompt_injection = _sub("prompt_injection", PromptInjectionConfig),
            caller_auth      = _sub("caller_auth",      CallerAuthConfig),
            agent_cost       = _sub("agent_cost",       AgentCostConfig),
            token_budget     = _sub("token_budget",     TokenBudgetConfig),
            sensitive_redact = _sub("sensitive_redact", SensitiveRedactionConfig),
        )


# ── Pipeline ───────────────────────────────────────────────────────────────────

class PolicyPipeline:
    """Ordered policy checks: check_intake (pre-routing) → check_dispatch (post-routing) → check_response."""

    def __init__(
        self,
        config: PolicyPipelineConfig | None = None,
        *,
        hydration: Any | None = None,
        routing: Any | None = None,
        metadata_enrichment: Any | None = None,
        isar_synthesis: Any | None = None,
        judge: Any | None = None,
        guardian: Any | None = None,
        prompt_injection_policy: Any | None = None,
    ) -> None:
        cfg = config or PolicyPipelineConfig()

        self._rate_limit       = RateLimitPolicy(cfg.rate_limit)             if cfg.rate_limit       else None
        self._prompt_injection = prompt_injection_policy or (PromptInjectionPolicy(cfg.prompt_injection) if cfg.prompt_injection else None)
        self._caller_auth      = CallerAuthorizationPolicy(cfg.caller_auth)  if cfg.caller_auth      else None
        self._agent_cost       = AgentCostPolicy(cfg.agent_cost)             if cfg.agent_cost       else None
        self._token_budget     = TokenBudgetPolicy(cfg.token_budget)         if cfg.token_budget      else None
        self._sensitive_redaction       = SensitiveRedactionPolicy(cfg.sensitive_redact)             if cfg.sensitive_redact       else None

        # Context/admission policies — injected rather than built from typed sub-configs:
        # RoutingPolicy.from_dict() returns the policy itself (no separate *Config class),
        # so it can't be threaded through PolicyPipelineConfig.from_dict() like the others.
        # Hydration/metadata are injected alongside it so all three wire the same way.
        self._hydration           = hydration
        self._routing             = routing
        self._metadata_enrichment = metadata_enrichment
        self._isar_synthesis      = isar_synthesis
        # Output policies — injected so executor owns construction (judge needs guardian for
        # is_unguarded check; guardian is passed alongside so the pipeline can skip judge
        # for read-only agents without an external caller computing the flag).
        self._judge    = judge
        self._guardian = guardian

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count via the configured TokenBudgetPolicy, or 0 if disabled."""
        return self._token_budget.estimate(text) if self._token_budget else 0

    async def check_intake(self, ctx: RequestContext) -> PolicyDecision:
        """Run pre-routing checks: rate limiting then prompt injection on the raw user input.

        Hydration is intentionally deferred to check_dispatch so that domain context
        is never in-scope when the prompt injection scanner and guardian evaluate the
        raw user message — preventing a crafted prompt from extracting injected PII.

        `messages` always comes back in `metadata["messages"]`.
        """
        if self._rate_limit:
            decision = await self._rate_limit.evaluate(
                session_id=ctx.session_id, tenant_id=ctx.caller_id,
            )
            if not decision.allowed:
                logger.info("Pipeline BLOCKED at rate_limit session=%s", ctx.session_id[:8] if ctx.session_id else "—")
                return decision

        if self._prompt_injection:
            decision = await self._prompt_injection.evaluate(
                message=ctx.last_msg, source=ctx.source, session_id=ctx.session_id,
            )
            if not decision.allowed:
                logger.info("Pipeline BLOCKED at prompt_injection source=%s", ctx.source)
                return decision

        logger.info(
            "Pipeline check_intake PASSED session=%s source=%s",
            ctx.session_id[:8] if ctx.session_id else "—", ctx.source,
        )
        return PolicyDecision(allowed=True, reason=_ALLOWED.reason, metadata={"messages": ctx.messages})

    async def check_dispatch(self, ctx: RequestContext) -> PolicyDecision:
        """Run post-routing checks: caller auth, cost, budget, env/agent admission, hydration, context enrichment.

        Pure-metadata gates (auth, cost, budget, routing) run first — no DB I/O on a blocked caller.
        Hydration and metadata enrichment run last, only after all gates have passed.
        `messages` (possibly with prepended SystemMessages) and any `metadata` enrichment
        summary always come back in `metadata`.
        """
        messages = list(ctx.messages or [])

        if self._caller_auth:
            decision = await self._caller_auth.evaluate(
                caller_id=ctx.caller_id, agent_id=ctx.target, session_id=ctx.session_id,
            )
            if not decision.allowed:
                logger.info("Pipeline BLOCKED at caller_auth caller=%s agent=%s", ctx.caller_id or "—", ctx.target)
                return decision

        if self._agent_cost:
            if not ctx.target:
                logger.warning("Pipeline agent_cost skipped: agent_id missing session=%s", ctx.session_id[:8] if ctx.session_id else "—")
            else:
                decision = await self._agent_cost.evaluate(
                    session_id=ctx.session_id, agent_id=ctx.target,
                )
                if not decision.allowed:
                    logger.info("Pipeline BLOCKED at agent_cost agent=%s session=%s", ctx.target, ctx.session_id[:8] if ctx.session_id else "—")
                    return decision

        if self._token_budget:
            decision = await self._token_budget.evaluate(
                session_id=ctx.session_id, tokens_requested=ctx.tokens_used, caller_id=ctx.caller_id,
            )
            if not decision.allowed:
                logger.info("Pipeline BLOCKED at token_budget session=%s", ctx.session_id[:8] if ctx.session_id else "—")
                return decision

        if self._routing is not None:
            decision = await self._routing.evaluate(env=ctx.env, agent=ctx.target)
            if not decision.allowed:
                logger.info("Pipeline ROUTING_BLOCK agent=%s env=%s reason=%s", ctx.target, ctx.env, decision.reason)
                return decision

        # Hydration and enrichment run only after all gates pass — avoids DB cost on blocked callers.
        if self._hydration is not None:
            hyd_decision = await self._hydration.evaluate(agent_id=ctx.target, intent=ctx.last_msg, env=ctx.env)
            hydration_block = hyd_decision.metadata.get("hydration_block", "")
            if hydration_block:
                from mltek.agents.bridge import SystemMessage
                messages = [SystemMessage(content=hydration_block)] + messages
                logger.info(
                    "HydrationPolicy injected context (dispatch stage): agent=%s entities=%s cache_hits=%d",
                    ctx.target, hyd_decision.metadata.get("hydrated_entities"), hyd_decision.metadata.get("cache_hits", 0),
                )

        metadata_meta = None
        if self._metadata_enrichment is not None:
            meta_decision = await self._metadata_enrichment.evaluate(agent_id=ctx.target)
            metadata_meta = {
                "domain": meta_decision.metadata.get("domain"),
                "reason": meta_decision.reason,
            }
            context_block = meta_decision.metadata.get("context_block", "")
            if context_block:
                from mltek.agents.bridge import SystemMessage
                messages = [SystemMessage(content=context_block)] + messages
                logger.info("MetadataPolicy injected context: agent=%s domain=%s", ctx.target, metadata_meta["domain"])

        logger.info(
            "Pipeline check_dispatch PASSED agent=%s session=%s",
            ctx.target or "—", ctx.session_id[:8] if ctx.session_id else "—",
        )
        return PolicyDecision(allowed=True, reason=_ALLOWED.reason, metadata={"messages": messages, "metadata": metadata_meta})

    def consume_tokens(self, session_id: str, tokens_used: int) -> None:
        """Record token usage against the session budget. Call after all LLM work is done."""
        if self._token_budget:
            self._token_budget.consume(session_id, tokens_used)

    async def check_response(self, ctx: RequestContext) -> PolicyDecision:
        """Run post-response checks; returns metadata keys scrubbed_text (PII) and judge (verdict)."""
        adapter = OutputAdapter(ctx.result)
        text = adapter.to_text()

        if self._sensitive_redaction and self._sensitive_redaction.applies(text):
            decision = await self._sensitive_redaction.evaluate(text=text, source=ctx.source, session_id=ctx.session_id, agent_id=ctx.target)
            if not decision.allowed:
                logger.info("Pipeline BLOCKED at sensitive_redact source=%s", ctx.source)
                return decision
            if decision.metadata.get("scrubbed_text"):
                logger.info("Pipeline sensitive_redact scrubbed source=%s labels=%s", ctx.source, decision.metadata.get("pii_labels"))
                return decision

        judge_meta = None
        if self._judge is not None:
            is_read_only = self._guardian is not None and self._guardian.is_unguarded(ctx.target)
            if is_read_only:
                logger.debug("Pipeline judge skipped: read-only agent=%s", ctx.target)
            elif self._judge.applies(ctx.last_msg, text):
                judge_decision = await self._judge.evaluate(ctx.last_msg, text, source=ctx.source)
                judge_meta = {
                    "passed":     judge_decision.allowed,
                    "reason":     judge_decision.reason,
                    "confidence": judge_decision.metadata.get("confidence", 0.5),
                }
                logger.info(
                    "Pipeline judge agent=%s passed=%s confidence=%.2f",
                    ctx.target, judge_meta["passed"], judge_meta["confidence"],
                )

        if judge_meta and not judge_meta["passed"]:
            logger.info("Pipeline BLOCKED at judge agent=%s reason=%s", ctx.target, judge_meta["reason"])
            return PolicyDecision(allowed=False, reason=judge_meta["reason"], metadata={"judge": judge_meta})

        logger.info("Pipeline check_response PASSED session=%s source=%s", ctx.session_id[:8] if ctx.session_id else "—", ctx.source)
        return PolicyDecision(allowed=True, reason=_ALLOWED.reason, metadata={"judge": judge_meta})

    async def synthesize(self, result: dict) -> dict:
        """Delegate post-agent ISAR synthesis to IsarSynthesisPolicy if present and applicable."""
        if self._isar_synthesis is None or not self._isar_synthesis.applies(result):
            return result
        return await self._isar_synthesis.synthesize(result)
