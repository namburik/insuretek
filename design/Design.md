# Paytek — Technical Design Document

> **Audience:** Product, operations, security, and engineering stakeholders
> **Status:** Living document — updated as the system evolves

---

## 1. Purpose

Paytek automates merchant onboarding by combining conversational intake, policy-driven validation, and workflow orchestration with semantic decision support. The design prioritizes safe, auditable onboarding flows while preserving the flexibility to support approvals, review, and interruption.

### Outcomes
- Complete merchant onboarding in a guided automated flow
- Clear separation of intake, workflow execution, and agent reasoning
- Strong compliance gating with auditability and retry safety
- Support for production and shadow environments with distinct runtime behavior

---

## 2. L1 Architecture — System Boundaries

### Primary system layers
- **Intake layer**: conversational data collection, validation, draft persistence, and session handling
- **Workflow layer**: orchestrates onboarding steps, manages async gate checks, and enforces state transitions
- **Agent layer**: routes intent, executes specialized actions, and provides semantic reasoning
- **Storage layer**: persists merchant state, workflow checkpoints, and graph relationships

### L1 logical flow

```text
User input → Intake → Orchestrator → Workflow engine
                   ↓                     ↓
               Policy checks        Tool/Agent execution
                   ↓                     ↓
             Validation outcomes   Graph/DB updates
                   ↓                     ↓
         Approval / rejection    Activation / audit trail
```

### Key architectural boundaries
- **UI / intake** is responsible for surface-level conversational flow and draft state.
- **Workflow engine** owns long-running onboarding progress, gate checks, and state transitions.
- **Agents** own decision routing, tool invocation, and semantic context generation.
- **Policies** guard inputs, tool calls, and routing decisions before state changes occur.

---

## 3. L2 Component Design

### 3.1 Intake and session management

- Captures merchant draft data through conversational turns
- Validates required fields and preserves partial progress
- Maintains in-memory session state keyed by session_id
- Applies intake-side policy checks for sensitive content and malformed input
- Shortens session IDs to 8 hex chars for backend compatibility

### 3.2 Workflow orchestration

- Manages onboarding workflow state and approval tasks
- Uses explicit transition rules to prevent illegal state jumps
- Advances state based on webhook results from external gate checks
- Computes partial and final approval status from essential gate outcomes

### 3.3 Agent routing and execution

- Uses semantic and intent routing to select the correct agent
- Supports specialized agents for boarding, merchant lookup, workflow queries, hierarchy, and semantic analysis
- Applies `Guardian` safety checks before agent execution
- Uses middleware for tool deduplication, semantic gating, and tool-result condensation

### 3.4 Policy and safety layer

- `IntakePolicy` validates intake text and session interactions
- `PolicyPipeline` (`src/shared/policy/pipeline.py`, config-driven via `PolicyPipelineConfig.from_dict`) runs the mechanical checks in three phases — intake (`RateLimitPolicy`, `PromptInjectionPolicy`), dispatch (`CallerAuthorizationPolicy`, `AgentCostPolicy`, `TokenBudgetPolicy`), response (`TokenBudgetPolicy.consume`, `SensitiveRedactionPolicy`)
- `GuardianPolicy` performs pre-agent route safety checks and can escalate ambiguous requests to reflection; pre-route check fires with `agent_id="pre_route"` before routing is resolved
- `ReflectionPolicy` attempts safe reformulation when Guardian finds an ambiguous block; resolver calls `evaluate()` (not `handle_blocked()`) so the audit decorator fires
- `RoutingPolicy` gates agent dispatch based on (env, agent) rules; replaces hardcoded routing blocks with a configurable rules dict — new constraints require only a new entry, no resolver edits
- `JitElevationPolicy` evaluates session phase and env before a restricted write tool may execute; `collecting` phase is always denied; shadow env follows the same phase rules as prod
- `JitPrivilegeMiddleware` enforces JIT gating at position 0 in the middleware stack — policy check and execution happen in the same step, no token round-trip or extra graph step
- `SemanticToolPolicyMiddleware` scores intent alignment on every tool call; first call uses LLM scoring, subsequent calls in the same run are pre-cleared by guardian and audited as `checked=False`
- `LLMJudgePolicy` performs post-response quality checks for mutating agents; `source` and `env` are passed through so the audit record carries full context
- `MetadataEnrichmentPolicy` injects statistical, relational, and operational context before agent dispatch; enrichment is audited with `domain` and `elapsed_ms` (context block excluded from audit to avoid size bloat)
- `SemanticSynthesisPolicy` (`src/shared/policy/output/semantic_synthesis_policy.py`) post-agent output policy, split from `MetadataEnrichmentPolicy`; synthesizes registered tool outputs (currently `isar_explain_merchant`) into an LLM narrative via `call_chat_agent` and a ISAR-guided system prompt; returns `isar_synthesis` key in the result dict; tagged with `@with_token_phase("synthesize")`
- `SensitiveRedactionPolicy` (`src/shared/policy/output/sensitive_redaction_policy.py`) replaces `PiiEgressPolicy`; same scrub/block behavior for SSN, routing numbers, account numbers, phone, and credit card patterns; pre-masks UUID tokens to prevent false positives on structured IDs

### 3.6 Policy audit subsystem

- Every policy gate decision is persisted to an append-only `policy_audit` table (Postgres and DynamoDB backed, env-aware shadow table)
- `PolicyAuditRecord` captures: `audit_id`, `session_id`, `correlation_id`, `request_id`, `policy_type`, `operation`, `allowed`, `reason`, `env`, `metadata`, `created_at`
- `@audit_policy_decision` decorator wraps any `evaluate()` method returning `PolicyDecision`; schedules a fire-and-forget `asyncio.create_task` — never blocks the request path; swallows all persistence errors
- `audit_inline` provides the same write path for middleware that doesn't use a Policy `evaluate()` method (e.g. `SemanticToolPolicyMiddleware`, `MetadataEnrichmentPolicy`)
- **Selective auditing gate** — `_active_agent` ContextVar is set by the resolver after routing; `_AUDIT_AGENTS = {"boarding_agent", "hierarchy_agent"}` controls full auditing; non-boarding agents only write DENIED decisions; ALLOWED read-path decisions are never persisted
- Boarding/hierarchy allowed gate decisions buffer per correlation_id (`_correlation_buffer`, max 50) and flush as one `pipeline_summary` record — tagged with a domain (`merchant`/`pricing`/`auth`/`platform`/`group`/`general`) inferred from intent or tool operations and stored in `metadata.intent_domain`. Flush runs after `check_response` and again in `AgentExecutor.ainvoke`'s `try/finally` as a safety net
- `llm_judge` and `reflection` write immediately via `await _persist()` (`_ALWAYS_AUDIT_TYPES`) so they can't bleed into the next agent's buffer; `semantic_tool` and denied decisions also stay immediate; non-boarding agents are unaffected
- `token_budget` writes directly via `_DIRECT_ONLY_TYPES` — bypasses pipeline_summary buffering entirely even for boarding/hierarchy; `TokenBudgetPolicy.consume()` emits a standalone `audit_inline` record after recording actual spend
- `MerchantOrchestrator` steps propagate `request_id`/`session_id` via context vars so every step writes audit records with full context, giving complete workflow audit trails
- Nine MCP tools expose audit data to the servicing agent: `get_policy_audit_by_session`, `get_policy_audit_by_correlation`, `get_policy_audit_by_policy_type`, `get_policy_audit_by_request_id`, `get_policy_audit_summary`, `get_forensic_audit`, `get_causal_chain`, `get_cross_session_history`, `get_quality_summary`
- Policy types covered: `onboarding`, `servicing`, `guardian`, `routing`, `jit_elevation`, `llm_judge`, `reflection`, `metadata`, `semantic_tool`, `live_hydration`, `quality_metric`, `pipeline_summary`, `token_budget`

### 3.5 Storage and data services

- **Core relational storage** holds merchants, pricing, platforms, groups, and auth credentials
- **Workflow state store** retains workflow progress, tasks, and audit metadata
- **Graph store** powers relationship traversal, similarity, and risk analytics
- **Policy audit store** append-only `policy_audit` / `policy_audit_shadow` tables; indexed on `session_id`, `correlation_id`, `policy_type`, `request_id`, and `created_at`; DynamoDB equivalent uses GSIs on the same keys

---

## 4. Policy and Runtime Behavior

### Policy execution order

```text
Intake validation (IntakePolicy)            → audit: onboarding / servicing
  ↓
Intake pipeline (PolicyPipeline.check_intake)
    → RateLimitPolicy → PromptInjectionPolicy
  ↓
Guardian pre-flight (agent_id="pre_route")  → audit: guardian [DENIED only for non-boarding]
  ├─ Hard block → return immediate rejection
  ├─ Needs reflection → Reflection Policy  → audit: reflection
  └─ Allow → set_active_agent(target)
               ↓
             RoutingPolicy check             → audit: routing
               ├─ Block (env/agent rule) → return rejection
               └─ Allow
                    ↓
             Guardian post-route check       → audit: guardian
                    ↓
             Dispatch pipeline (PolicyPipeline.check_dispatch)
               → CallerAuthorizationPolicy → AgentCostPolicy → TokenBudgetPolicy
                    ↓
             MetadataEnrichmentPolicy inject    → audit: metadata
                    ↓
             Agent dispatch
                    ↓
             JIT phase/env gate              → audit: jit_elevation (boarding_agent only)
               ├─ DENIED → [JIT_DENIED] ToolMessage
               └─ GRANTED → SemanticToolPolicy → audit: semantic_tool (every tool call)
                    ↓
             Tools execute
                    ↓
             SemanticSynthesisPolicy.synthesize (post-agent)
               ├─ isar_explain_merchant → LLM narrative → isar_synthesis key
               └─ no matching tool → pass-through
                    ↓
             LLMJudge post-execution         → audit: llm_judge (mutating agents only)
                    ↓
             Response pipeline (PolicyPipeline.check_response)
               → TokenBudgetPolicy.consume → SensitiveRedactionPolicy
```

### Audit write rules

| Agent | ALLOWED decisions | DENIED decisions |
|---|---|---|
| `boarding_agent` | Buffered then summarized per request | Written |
| all others | **Skipped** | Written |

Pre-routing decisions (intake, guardian pre-route) use `active_agent=""` — not in `_AUDIT_AGENTS` — so they write only on DENIED. This captures blocked intake and pre-route threat blocks without producing noise for the normal servicing read path.

### LLM call optimization

- Common production path uses zero semantic tool LLM calls when Guardian already clears the intent
- Read-only lookups often skip LLMJudge entirely
- Write workflows typically incur only one judge call after execution

### Tool safety

- Deduplication middleware prevents repeated identical tool calls in the same reasoning run
- `JitPrivilegeMiddleware` performs phase/env gating for restricted write tools in a single middleware step, allowing granted calls to execute immediately without a retry token round-trip
- ToolResultCondenser reduces board-agent payload size by replacing large write results with compact summaries
- Shadow mode blocks `sme_agent` routing and `kg_*` tools via `RoutingPolicy`

---

## 5. Workflow State Model

### Defined states
- `INITIALIZED`
- `AWAITING_WEBHOOKS`
- `PARTIALLY_APPROVED`
- `APPROVED`
- `ACTIVE`
- `REJECTED`
- `FAILED`

### Transition rules
- `INITIALIZED` → `AWAITING_WEBHOOKS` or `FAILED`
- `AWAITING_WEBHOOKS` → `PARTIALLY_APPROVED`, `APPROVED`, `REJECTED`, or `FAILED`
- `PARTIALLY_APPROVED` → `APPROVED`, `REJECTED`, or `FAILED`
- `APPROVED` → `ACTIVE` or `FAILED`
- Terminal states have no outbound transitions

### Gate evaluation
- Essential gate checks drive final approval
- Any essential failure leads to `REJECTED`
- All essential approvals lead to `APPROVED`
- Mixed partial completion remains in `PARTIALLY_APPROVED`

---

## 6. Recent architecture updates

- **`PiiEgressPolicy` renamed to `SensitiveRedactionPolicy`**: file is `src/shared/policy/output/sensitive_redaction_policy.py`; config class is `SensitiveRedactionConfig`; behavior unchanged (scrub/block SSN, routing, account, phone, CC). UUID tokens are pre-masked before PII patterns to prevent false positives on structured IDs.
- **`SemanticSynthesisPolicy` split from `MetadataEnrichmentPolicy`**: synthesis logic (ISAR-guided LLM narrative from tool output) is now its own output-layer class in `src/shared/policy/output/semantic_synthesis_policy.py`. `MetadataEnrichmentPolicy` retains only context injection; `PolicyPipeline` receives a separate `isar_synthesis` parameter; resolver builds both side by side.
- **Token phase tracking**: `_token_phase` ContextVar in `context.py` tags every LLM-consuming stage. `@with_token_phase(phase)` decorator sets the phase before a call and logs tokens consumed after; handles both regular `async def` and `async def` generators (fixes broken SSE `stream_reply`). Stages: `routing`, `agent`, `synthesize`, `judge`, `reflection`, `prompt_injection`, `metadata_enrichment`, `semantic_tool`, `workflow_decision`.
- **Callback token tracking across LangChain task barrier**: LangChain callbacks run in child asyncio tasks where ContextVar writes don't propagate back to the parent. `_parent_task_id` ContextVar + `_task_tokens: dict[int, int]` bypass this — callbacks call `add_request_tokens(n)` which writes into the parent's bucket keyed by `_parent_task_id`; `get_request_tokens()` reads the current task's bucket. `_TokenUsageCallback` in `lang_agents.py` implements `on_chat_model_end` with `on_llm_start` fallback estimation.
- **`TokenBudgetPolicy.consume()` audit**: consume now emits a standalone `audit_inline` record tagged `token_budget`; this type is in `_DIRECT_ONLY_TYPES` in `audit_decorator.py` — it always writes directly, bypassing pipeline_summary buffering even for boarding/hierarchy agents. Ensures token spend is independently auditable separate from gate decisions.
- **Policy audit subsystem**: append-only `policy_audit` table with dual Postgres/DynamoDB backends. All 9 policy types emit fire-and-forget audit records via `@audit_policy_decision` decorator or `audit_inline`. Selective gate: `boarding_agent` writes all decisions; other agents write DENIED-only. Five MCP query tools expose audit data via the servicing path.
- **Audit-selective agent gating**: `_active_agent` ContextVar set by resolver after routing; `_AUDIT_AGENTS = {"boarding_agent"}` controls full audit scope. Pre-routing policy decisions (intake, guardian pre-route) default to `""` agent — write DENIED only.
- **Reflection wiring fix**: resolver now calls `sidecar.evaluate()` instead of `handle_blocked()` directly, ensuring the `@audit_policy_decision` decorator fires for every reflection run.
- **JIT audit fix**: `operation_arg=1` replaced with `operation_kwarg="tool_name"` — tool name is always passed as a keyword argument; positional index was out-of-bounds.
- **LLMJudge audit fix**: resolver now passes `source=source, env=env` to `judge.evaluate()` so operation resolves to the source channel instead of falling back to the function name.
- **Guardian pre-route label**: pre-routing guardian check now uses `agent_id="pre_route"` instead of `"unknown"` for audit clarity.
- **Semantic tool coverage**: `SemanticToolPolicyMiddleware` now audits every tool call, not just the first. Pre-cleared calls record `checked=False`; scored calls record `checked=True` with score.
- **Metadata audit**: `MetadataEnrichmentPolicy.evaluate()` calls `audit_inline` with `domain`, `enriched`, and `elapsed_ms`; the full context block is excluded to avoid audit record size bloat.
- **Boarding-agent audit summary**: `_persist`/`audit_inline` buffer allowed boarding/hierarchy gate decisions per correlation_id (`_correlation_buffer`, capped at 50) instead of writing immediately; `flush_pipeline_summary` runs in `AgentExecutor.ainvoke`'s `try/finally` so it fires on every exit path. Each request yields one domain-tagged `pipeline_summary` (`metadata.controls` + `metadata.intent_domain`) plus individual `semantic_tool`/denial records — non-boarding requests write nothing on the allowed path.
- **JIT privilege gating**: `JitPrivilegeMiddleware` sits at position 0 in write-agent stacks (boarding, platform, hierarchy). Policy is evaluated in a single step — GRANTED calls execute immediately; no token round-trip, no extra LangGraph step. Gate is phase-only: `collecting` → deny; `offering_optional` / `confirming` / `done` → grant (prod and shadow). `set_jit_session` must be called at every resolver entry point (BFF endpoints, workflow activation node, merchant orchestrator).
- **RoutingPolicy**: replaces the hardcoded `sme_agent` shadow block in `resolver.py` with a declarative `_DEFAULT_RULES` dict. Adding new (env, agent) routing constraints requires only a new dict entry — no resolver edits.
- **ToolResultCondenser**: compacts large boarding tool outputs while keeping error details intact
- **Workflow state guard**: explicit transition matrix added to the workflow domain
- **Create idempotency**: DynamoDB conditional writes + BFF pre-flight guard for duplicate onboarding requests
- **Shadow env**: shadow routes to shadow DynamoDB tables for boarding prompt testing; write tools are permitted in shadow (JIT phase gate still applies)
- **Resilience**: graceful recovery from graph recursion failures with no successful tool result
- **Policy hardening**: intake credit-card detection now includes Luhn validation
- **Session ID stability**: session identifiers shortened for backend alignment

---

## 7. Design principles

- Keep workflow logic separate from presentation and conversational state
- Prefer modular, auditable services over monolithic execution paths
- Enforce policy before state mutation whenever possible
- Make production and shadow behavior explicit and isolated
- Support safe retries, idempotency, and invalid state detection
- Design for extensibility, especially for future policy, agent, or graph capabilities

---

## 8. Next steps

- Add a more formal L1/L2 architecture diagram and component map
- Document non-functional requirements for latency, resilience, and policy throughput
- Define shadow/test environment behavior and deployment isolation more explicitly
- Capture security assumptions for credential provisioning and agent tool execution
- Maintain this document as the shared L1/L2 architecture reference

---

## 10. Things to remember

### Policy LLM cost optimisation
- `SemanticToolPolicyMiddleware` scores only the **first tool call** per agent run (`_run_checked` ContextVar). Subsequent tool calls in the same run skip the LLM scorer entirely.
- When Guardian clears an intent (LOW risk, `allowed=True`), the resolver sets a `_guardian_cleared` ContextVar before invoking the agent. `abefore_agent` reads this and pre-sets `_run_checked=True`, so **zero semantic LLM calls** occur on the Guardian-cleared common path.
- `LLMJudge` is **skipped for read-only agents** (`merchant_agent`, `sme_agent`, etc.) — Guardian already auto-allows them and a wrong lookup answer is low-stakes.
- `SemanticSynthesisPolicy.synthesize()` scans reversed message list for a ToolMessage whose `name` is in `_SYNTHESIS_REGISTRY` and `status != "error"`. If none found it returns the result unchanged — no LLM call on the default path for non-semantic agents.

### Token phase tracking
- Every LLM-consuming stage wraps its call with `@with_token_phase(phase)` or `async with with_token_phase(phase)`. This sets `_token_phase` so callbacks can identify which stage spent tokens.
- LangChain callbacks (e.g. `_TokenUsageCallback`) run in child tasks. ContextVar writes from child tasks don't reach the parent — use `add_request_tokens(n)` which writes into `_task_tokens[_parent_task_id]` on the shared module-level dict. Call `get_request_tokens()` in the parent after agent execution to read the total.
- `@with_token_phase` handles `async def` generators via `inspect.isasyncgenfunction` — avoids wrapping the generator result in an extra coroutine that would break SSE streaming.

### boarding_agent recursion limit and write-tool recovery
- `boarding_agent` can call `get_merchant` before the write tool even when the prompt specifies the merchant_id directly. This consumes 2 graph nodes before the write, leaving fewer nodes for the agent to formulate a response.
- `ToolCallLimitMiddleware(run_limit=2, exit_behavior="end")` is added to `boarding_agent` to hard-cap tool calls and force a text response after 2 tools, preventing a 3rd tool call that would hit the recursion limit.
- `AgentExecutor.ainvoke` uses **`astream(stream_mode="values")`** instead of `ainvoke`. On `GraphRecursionError`, it scans the collected partial state for the last successful `ToolMessage`. If a write tool already completed, the partial result is returned normally — "Resolver dispatch completed" is logged and the orchestrator marks the step **PASS** instead of FAIL.
- The pricing record in DynamoDB is written correctly even when the graph hits the recursion limit. The failure was a graph bookkeeping issue, not a data failure.

### Merchant pricing data integrity
- `deactivate_merchant_pricing` in `PricingRepository` calls `get_active_pricing` which returns only `records[0]`. If multiple active records exist (e.g. from a prior failed deactivation), only one is cleared. Fix: iterate `get_active_items` directly and deactivate all.
- `get_active_pricing` has no `ORDER BY` — the first active record returned is non-deterministic. Should sort by `effective_at DESC` to consistently return the most recent record.
- `PricingService.set_pricing` deactivates old pricing and creates new pricing in two separate calls with no transaction wrapper. A crash between them leaves the merchant with no active pricing.

### JIT context propagation
- `_jit_session_phase` and `_jit_session_id` are ContextVars that default to `"collecting"` and `""`. If `set_jit_session` is never called, every restricted tool call is denied.
- Must be called before any `call_resolver` / `call_resolver_safe` invocation: `generate_intake_stream`, `get_intake_message_response`, `get_servicing_message_response` (BFF), `_activate_merchant_node` (workflow graph), and `MerchantOrchestrator.run` (orchestrator).
- Workflow engine callers use `set_jit_session("done", workflow_id_or_name)` — merchant has already cleared the full approval pipeline at that point.
- `env` is NOT read from the JIT ContextVar; it comes from `_env_var` (set by `set_env` in the resolver), so env propagation is independent of JIT session setup.

### Agent source trust
- `source=internal` bypasses Guardian's `checked_sources` filter (which defaults to `{"react"}`). Internal workflow steps are trusted callers; Guardian skips them and returns LOW/allowed. The `_guardian_cleared` ContextVar is still set, so semantic and judge are also skipped — zero policy LLM calls for internal workflow steps on the common path.
