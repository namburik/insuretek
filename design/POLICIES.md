   # Policy Summary

   This document summarizes all policy implementations under `src/shared/policy`.

   ## Intake policies

   - **IntakePolicy** (`src/bff_service/prompt_validation_policy.py`) validates intake and servicing prompts. It blocks prompts containing sensitive data patterns like SSN or valid credit card numbers. In the servicing path (`validate_servicing`), it also blocks unauthorized explicit IDs — but only once `session.applicant_id` is set (authorized set is `{applicant_id, request_id}`; if no applicant is established yet, all IDs are allowed). Otherwise returns an allowed or denied `PolicyDecision`.
   - **PromptInjectionPolicy** (`src/shared/policy/intake/prompt_injection_policy.py`) detects prompt injection attempts in the intake message via two stages. Stage 1 runs 7 fully configurable regex patterns (role override, instruction override, system leak, jailbreak keywords, delimiter injection, token smuggling, indirect-injection *setup* — i.e. a user announcing "when you read X, do Y") — all overridable via `injection_patterns` in YAML. Stage 2 is an optional LLM scorer (`llm_enabled: false` by default). All detection constants including LLM response patterns are in `PromptInjectionConfig` and compiled in `__post_init__`. Note: this policy only sees the user's intake message — it cannot detect injected instructions arriving later via tool results or hydrated entity data; see **LiveHydrationPolicy** for that vector.
   - **RateLimitPolicy** (`src/shared/policy/intake/rate_limit_policy.py`) enforces sliding-window rate limits by session and tenant. It counts requests in a time window and denies requests that exceed configured session or tenant maximums.
   - **TokenBudgetPolicy** (`src/shared/policy/intake/token_budget_policy.py`) enforces per-session and per-request LLM token budgets. `evaluate()` blocks when a single request or cumulative session usage exceeds configured caps; `consume()` tracks cumulative spend. Token counts are estimated from input and output messages using `tiktoken.cl100k_base` (or character count fallback), allowing the policy to proactively check projected usage before dispatch and record actual usage after responses.
   - **AgentCostPolicy** (`src/shared/policy/access/cost_policy.py`) enforces per-agent sliding-window limits after routing resolves the agent. It limits expensive agents like `sme_agent` (5/min) and `underwriting_agent` (10/min) independently of session/tenant rate limits. Uses only `session_id` and `agent_id` — never entity metadata from hydration. Agents not listed in `agent_limits` fall through to `default_limit` (0 = unlimited).

   ## Access policies

   - **CallerAuthorizationPolicy** (`src/shared/policy/access/caller_authorization_policy.py`) checks whether the caller role permits the requested agent. Built-in roles include READ_ONLY, OPERATOR, and ADMIN, and the policy grants or denies agent invocation accordingly.
   - **RoutingPolicy** (`src/shared/policy/access/routing_policy.py`) evaluates whether a routed agent is allowed in the current environment. Rules are loaded from `routing: rules:` in `policy_config.yaml` via `RoutingPolicy.from_dict()` — no code changes needed to add new constraints. Supports `"*"` wildcard for env or agent.
   - **JitElevationPolicy** (`src/shared/policy/access/jit_elevation_policy.py`) governs just-in-time tool elevation. It decides whether a tool call is permitted based on agent/tool-specific allow-lists and runtime constraints.

   ## Context policies

   - **MetadataEnrichmentPolicy** (`src/shared/policy/context/metadata_enrichment_policy.py`) injects static domain metadata into agent context. It always returns allowed=True and provides statistical, relational, and operational context to semantic agents.
   - **LiveHydrationPolicy** (`src/shared/policy/context/hydration_policy.py`) fetches applicant and workflow context for resolved entity IDs (UUIDs → applicant, `wf_*` → workflow). Each fetched text is scanned for injection patterns; flagged entities are excluded from `hydration_block` and reported in `metadata["flagged_entities"]` for audit. Surviving context is wrapped with "untrusted reference data" framing. Fetches are sequential with per-entity timeout.

   ## Guard policies

   - **GuardianAgent** (`src/shared/policy/guard/guardian_policy.py`) is a pattern-based threat detector for agent intents. It checks for destructive verbs, bulk operations, explicit IDs, and write actions, then returns allow/block/needs_reflection with LOW/MEDIUM/HIGH risk.
   - **GuardianReflectionPolicy** (`src/shared/policy/guard/guardian_policy.py`) handles ambiguous Guardian blocks. See Blocking Policies section for full details on timeout behavior and decision outcomes.

   ## Output policies

   - **SensitiveRedactionPolicy** (`src/shared/policy/output/sensitive_redaction_policy.py`) scans agent responses and tool results for PII. It detects SSN, routing numbers, account numbers, phone numbers, and credit card numbers, then either blocks the response or scrubs redacted values. UUID tokens are pre-masked before PII patterns to prevent false positives on structured IDs. Replaces the former `PiiEgressPolicy`.
   - **SemanticSynthesisPolicy** (`src/shared/policy/output/semantic_synthesis_policy.py`) post-agent output policy. Scans the agent result for a registered ToolMessage (`isar_explain_applicant`) and concurrently runs two LLM calls: a narrative synthesis (user-facing) and a structured evidence chain extraction (audit-only via `_emit_isar_decision`). Injects the result as `isar_synthesis` in the result dict. Skipped (no LLM call) when no registered tool message is present. Tagged with `@with_token_phase("synthesize")`.
   - **LLMJudge** (`src/shared/policy/output/llm_judge_policy.py`) is a post-response correctness policy. It sends a judge prompt to an LLM to score whether the response fulfills the original request and returns allowed based on the judge verdict.

   ## Scoring metrics

   - **ResponseEvaluator** (`src/shared/policy/metrics/evaluation.py`) is a lightweight post-response quality scorer that runs four deterministic checks: absence of error keywords (25%), expected keyword coverage proportional to fraction found (0–35%), absence of excluded keywords (20%), and response substantiveness ≥ 20 chars (20%). Returns a `QuickEvalResult` with a 0–1 score mapped to `QualityLevel` bands: EXCELLENT (≥ 0.85), GOOD (≥ 0.70), FAIR (≥ 0.50), POOR (< 0.50). Configurable via `EvalConfig` (`enable_eval`, `retry_on_low_quality`, `max_retries`, `min_quality_threshold`). Specialized subclasses `ApplicantOperationEvaluator` and `GroupOperationEvaluator` provide domain-specific keyword sets. Convenience wrapper `evaluate_agent_response(response_text, operation_type)` selects keyword sets by operation type (`query`, `create`, `update`, `list`, `delete`). Pipeline helper `eval_step()` returns `None` when no eval type is set, allowing workflow steps to opt out of scoring. This scorer is intentionally non-blocking — it scores for observability and retry signals, not hard gates; see **LLMJudge** for the authoritative correctness gate.
   - **record_routing_confidence** (`src/shared/policy/metrics/routing_metrics.py`) emits a `quality_metric` audit record via `audit_inline` for each resolved routing decision. Logs `agent_id`, `confidence` (0–1), and `routing_mode`; treats confidence ≥ 0.35 as `allowed=True` to surface low-confidence routes in the audit trail without blocking them.

   ## Middleware guard rails

   Middleware guard rails sit in the `AgentMiddleware` stack (`src/shared/common/agents/middleware/`) and execute per-tool-call, wrapping every MCP dispatch before and after execution. They are orthogonal to `PolicyPipeline` stages — they fire inside the agent's tool loop rather than at request ingress/egress.

   - **CorrelationMiddleware** (`src/shared/common/agents/middleware/correlation_middleware.py`) stamps the active `correlation_id` from `CorrelationContext` into every tool call's `args` dict before the call is dispatched to the MCP server. This is necessary because the MCP server runs in a separate process and cannot read context vars directly. No blocking — purely a context propagation guard rail.
   - **JitPrivilegeMiddleware** (`src/shared/common/agents/middleware/jit_privilege_middleware.py`) sits at position 0 in the middleware stack (before `ToolIntentMiddleware`). For each tool call it performs a fast-path check: unrestricted tools pass through immediately with zero overhead. For restricted tools it evaluates `JitElevationPolicy` against the current `phase`, `env`, and `session_id`. On GRANTED the call executes in the same middleware invocation — no extra LangGraph step or token round-trip. On DENIED it returns a `[JIT_DENIED]` `ToolMessage` immediately. This is the runtime enforcement counterpart to the policy-level `JitElevationPolicy`.
   - **ToolIntentMiddleware** (`src/shared/common/agents/middleware/tool_intent_middleware.py`) gates each tool call against the user's resolved intent before execution. `abefore_agent` runs once per agent invocation and pre-clears the check (via `_run_checked` ContextVar) when Guardian has already cleared the intent or intent is empty. `awrap_tool_call` LLM-scores `(intent, tool_name, args)` via a structured `ToolIntentScore` call on the first un-pre-cleared tool; subsequent tools in the same run are pre-cleared. Calls scoring below `threshold` (default 0.5, YAML-configurable under `semantic_tool`) return `[INTENT_BLOCKED]`. Audited via `audit_inline(policy_type="tool_intent")`.
   - **ToolLoopGuardMiddleware** (`src/shared/common/agents/middleware/tool_loop_guard_middleware.py`) detects and breaks infinite tool call loops within a single agent run. `abefore_agent` resets a per-invocation `_dedup_seen` ContextVar. `awrap_tool_call` hashes `(tool_name, args)` via SHA-256 (first 16 hex chars); on the first call it executes normally and caches the result. On subsequent identical calls exceeding `max_duplicate_count` (default 1), it returns a `[LOOP_DETECTED]` `ToolMessage` containing the cached result, preventing infinite reasoning loops while preserving the information from the first call.

   ## Supporting policies

   - **PolicyDecision** and **Policy** (`src/shared/policy/abstract_policy.py`) define the shared return type and policy interface used by all policies.

   ---

   ## Policy Execution Flow

   **PolicyPipeline** orchestrates: RateLimitPolicy + PromptInjectionPolicy (`check_intake`), CallerAuthorizationPolicy + AgentCostPolicy + TokenBudgetPolicy (`check_dispatch`), and SensitiveRedactionPolicy (`check_response`). Other policies (IntakePolicy, Guardian, Routing, LiveHydration, Metadata, SemanticSynthesis, JitElevation, LLMJudge) are invoked directly by their call sites (mainly `resolver.py`, `bff_service/endpoints.py`, `privilege_gate.py`). The steps below describe execution order across all mechanisms.

   0. **BFF intake gate** (`bff_service/endpoints.py`):
      - IntakePolicy validates sensitive data and unauthorized IDs. Blocks here skip all downstream policies.
      - **Note**: Intake rejections don't increment rate/token counters (those live in pipeline, Step 1+). HTTP-level rate limiting should protect intake from brute-force.

   1. **Intake & pre-route checks** (`AgentExecutor._ainvoke`):
      - RateLimitPolicy + PromptInjectionPolicy: session/tenant throttling and injection detection
      - LiveHydrationPolicy (pre-route): establishes entity context; injection-flagged entities excluded from `hydration_block`
      - GuardianAgent (pre-route): hard-block only; reflection flags logged but not escalated until post-route

   2. **Agent resolution & dispatch**:
      - `pipeline.check_dispatch`: CallerAuthorizationPolicy → AgentCostPolicy → TokenBudgetPolicy
      - **Note**: AgentCostPolicy uses only session_id/agent_id, never entity metadata — injection-safe
      - RoutingPolicy checks env/agent constraints
      - GuardianAgent (post-route): ambiguous cases escalate to GuardianReflectionPolicy

   3. **Context enrichment**: MetadataEnrichmentPolicy injects domain metadata

   4. **Tool dispatch**: Middleware stack intercepts each MCP tool call in order — CorrelationMiddleware stamps correlation context, JitPrivilegeMiddleware enforces JIT elevation (wrapping JitElevationPolicy), ToolIntentMiddleware LLM-scores intent alignment, ToolLoopGuardMiddleware deduplicates repeated identical calls

   5. **Post-agent synthesis**: SemanticSynthesisPolicy synthesizes registered tool outputs (e.g. `isar_explain_applicant`) into a narrative and evidence chain concurrently; pass-through if no registered tool message found

   6. **Output checks** (post-response):
      - TokenBudgetPolicy.consume: records cumulative token spend; emits a standalone `token_budget` audit record via `_DIRECT_ONLY_TYPES`
      - SensitiveRedactionPolicy: detects/scrubs PII
      - LLMJudge: semantic correctness check
      - ResponseEvaluator (scoring metrics): deterministic quality score (EXCELLENT/GOOD/FAIR/POOR) for observability and retry signalling; non-blocking
      - record_routing_confidence (routing metrics): emits `quality_metric` audit record for the routing decision confidence

   7. **BFF response gate**: IntakePolicy.validate_agent_response blocks sensitive data in final reply (audited)

   **Halting logic**: Pipeline evaluative checks use `if not decision.allowed: return decision`. Directly-invoked policies use call-site-specific branching (e.g., GuardianReflectionPolicy's `metadata["action"]` drives resolver logic).

   ---

   ## Blocking vs. Non-Blocking Policies

   ### Blocking Policies
   These policies return `allowed=False` to halt request processing:
   - **IntakePolicy** — sensitive data or unauthorized IDs
   - **PromptInjectionPolicy** — prompt injection patterns detected
   - **RateLimitPolicy** — session or tenant rate limit exceeded
   - **TokenBudgetPolicy** — single request or cumulative session token usage exceeds configured limits
   - **AgentCostPolicy** — per-agent call limit exceeded
   - **CallerAuthorizationPolicy** — caller role not authorized for agent
   - **RoutingPolicy** — env/agent combination blocked by rules
   - **GuardianAgent** — hard threat patterns or constraints violated
   - **GuardianReflectionPolicy** — blocks when reformulator cannot fix intent, max iterations exhausted with no rewrite, or (if configured) critic cannot correct. **Timeout:** returns `allowed=False` (fail-closed) to prevent latency spikes bypassing safety.
   - **JitElevationPolicy** — tool call not authorized (tool not in agent's allow-list or elevation denied)
   - **SensitiveRedactionPolicy** — PII detected
   - **LLMJudge** — response fails semantic correctness check

   ### Non-Blocking (Enrichment/Synthesis) Policies
   These always return `allowed=True` or pass-through and add context in metadata or the result dict:
   - **MetadataEnrichmentPolicy** — injects domain metadata (enums, constraints, tool sequencing)
   - **LiveHydrationPolicy** — injects entity context (applicant/workflow) with injection-scanned output
   - **SemanticSynthesisPolicy** — synthesizes registered tool output into a narrative; no `PolicyDecision` return, mutates the result dict directly
   - **ResponseEvaluator** (scoring metrics) — deterministic quality scoring; non-blocking, emits score for observability only
   - **record_routing_confidence** (routing metrics) — emits confidence audit record; `allowed=False` when confidence < 0.35 but does not halt routing

   ### Blocking Middleware Guard Rails
   These execute in the `AgentMiddleware` stack per-tool-call and can halt individual tool dispatches (not the whole request):
   - **JitPrivilegeMiddleware** — returns `[JIT_DENIED]` ToolMessage for restricted tools without an active elevation grant
   - **ToolIntentMiddleware** — returns `[INTENT_BLOCKED]` ToolMessage when LLM intent-alignment score < threshold

   ### Non-Blocking Middleware Guard Rails
   These always pass through and add context or deduplication logic:
   - **CorrelationMiddleware** — stamps `correlation_id` into tool call args; never blocks
   - **ToolLoopGuardMiddleware** — returns cached result with `[LOOP_DETECTED]` prefix on duplicate calls; preserves information without blocking the agent

   ---

   ## Configuration

   Most policies are YAML-configurable via the router config file. Load patterns:

   ```python
   # Example: load PromptInjectionPolicy config from YAML
   from shared.policy.intake.prompt_injection_policy import PromptInjectionConfig
   config = PromptInjectionConfig.from_dict(router_config.get("prompt_injection", {}))

   # Example: load GuardianConfig from YAML
   from shared.policy.guard.guardian_policy import GuardianConfig
   guardian_config = GuardianConfig.from_dict(router_config.get("guardian", {}))
   ```

   ### Configurable Policies

   | Policy | Config Class | YAML Section | Typical Override |
   |--------|--------------|--------------|------------------|
   | RateLimitPolicy | RateLimitConfig | `rate_limit` | `session_max_calls`, `tenant_max_calls` |
   | TokenBudgetPolicy | TokenBudgetConfig | `token_budget` | `max_session_tokens`, `max_request_tokens` |
   | AgentCostPolicy | AgentCostConfig | `agent_cost` | `agent_limits` (per-agent caps) |
   | PromptInjectionPolicy | PromptInjectionConfig | `prompt_injection` | `llm_enabled`, `hard_block_threshold`, `injection_patterns`, `llm_score_pattern`, `llm_reason_pattern` |
   | CallerAuthorizationPolicy | CallerAuthConfig | `caller_auth` | `role_agents` (role→agent mapping) |
   | RoutingPolicy | `from_dict()` | `routing` | `rules:` list of `{env, agent, message}` entries |
   | MetadataEnrichmentPolicy | MetadataConfig | `metadata` | `enabled`, `domains` |
   | LiveHydrationPolicy | HydrationConfig | `hydration` | `max_entities`, `cache_ttl_secs` |
   | SensitiveRedactionPolicy | SensitiveRedactionConfig | `pii_egress` | `mode` (block or scrub) |
   | LLMJudge | JudgeConfig | `judge` | `enabled`, `model` |
   | GuardianAgent | GuardianConfig | `guardian` | token_limit, verb patterns, explicit ID patterns |
   | ToolIntentMiddleware | ToolIntentConfig | `semantic_tool` | `threshold` (default 0.5) |
   | ResponseEvaluator | EvalConfig | *(constructed inline)* | `enable_eval`, `retry_on_low_quality`, `min_quality_threshold`, `max_retries` |

   ---

   ## Observability & Audit

   All `evaluate()` methods use `@audit_policy_decision` decorator. Audit behavior differs by policy type and agent:

   ### Audit Buffering & Flushing
   - **Allowed decisions** for `underwriting_agent`: buffered into a single `pipeline_summary` per request
   - **Allowed decisions** for all other agents: skipped (no audit record)
   - **Denied decisions**: always logged as standalone `PolicyAuditRecord` regardless of agent
   - **Policies in `_ALWAYS_AUDIT_TYPES`** (currently `llm_judge`, `reflection`): written directly via `await _persist()` instead of buffered, ensuring they complete before `flush_pipeline_summary` runs
   - **Policies in `_DIRECT_ONLY_TYPES`** (currently `token_budget`): always written as a standalone direct record — bypasses pipeline_summary buffering even for underwriting_agent. Use for infrastructure concerns that should be independently auditable regardless of agent type.

   ### Pipeline Summary
   The `pipeline_summary` record is flushed just before `llm_judge` evaluation (if enabled) to preserve execution order. It includes:
   - `operation`: the active agent name (e.g. `underwriting_agent`)
   - `domain`: inferred classification from user intent or buffered tool operations
     - Domains: `policy`, `applicant`, `general`
     - `policy` matches on underwrite/bind/quote/create_policy keywords; `applicant` matches on applicant/account/enroll keywords
     - `general` is the fallback when no keywords match
   - `metadata.intent_domain`: the matched domain for queryability
   - `reason`: human-readable summary of allowed controls
   - `metadata.controls`: compact list of policy checks (e.g., `[caller_auth, token_budget, routing, guardian, ...]`)
