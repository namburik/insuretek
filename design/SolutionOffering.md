# AI-Gated Enrollment Platform — Technical Design Document

> **Audience:** Product, operations, security, and engineering stakeholders
> **Status:** Living document — updated as the system evolves

---

## 1. Purpose

The platform automates subject enrollment by combining conversational intake, policy-driven validation, and workflow orchestration with semantic decision support. The design prioritizes safe, auditable enrollment flows while preserving the flexibility to support approvals, review, and human-in-the-loop interruption across any regulated or compliance-sensitive domain.

### Outcomes
- Complete subject enrollment in a guided, automated conversational flow
- Clear separation of intake, workflow execution, and agent reasoning
- Strong compliance gating with full auditability and retry safety
- Support for production and shadow environments with distinct runtime behavior

### Applicable domains
Insurance underwriting, financial KYC/KYB, employee or vendor onboarding, healthcare intake, service provider registration, and any domain requiring multi-step guided data collection with policy-enforced approval gates.

---

## 2. L1 Architecture — System Boundaries

### Primary system layers
- **Intake layer**: conversational data collection, validation, draft persistence, and session handling
- **Workflow layer**: orchestrates enrollment steps, manages async gate checks, and enforces state transitions
- **Agent layer**: routes intent, executes specialized actions, and provides semantic reasoning
- **Storage layer**: persists subject state, workflow checkpoints, and graph relationships

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
- **UI / intake** owns surface-level conversational flow and draft state.
- **Workflow engine** owns long-running enrollment progress, gate checks, and state transitions.
- **Agents** own decision routing, tool invocation, and semantic context generation.
- **Policies** guard inputs, tool calls, and routing decisions before any state change occurs.

---

## 3. L2 Component Design

### 3.1 Intake and session management

- Captures applicant draft data through conversational turns
- Validates required fields and preserves partial progress across turns
- Maintains in-memory session state keyed by `session_id`
- Applies intake-side policy checks for sensitive content and malformed input
- Normalizes session identifiers for backend compatibility

### 3.2 Workflow orchestration

- Manages enrollment workflow state and approval tasks
- Uses an explicit transition matrix to prevent illegal state jumps
- Advances state based on webhook results from external gate checks
- Computes partial and final approval status from essential gate outcomes
- Supports idempotent re-entry via conditional writes and pre-flight duplicate guards

### 3.3 Agent routing and execution

- Uses semantic and intent-based routing to select the correct agent
- Supports specialized agents for enrollment, subject lookup, workflow queries, hierarchy traversal, and semantic analysis
- Applies `Guardian` safety checks before every agent execution
- Uses a composable middleware stack for tool deduplication, semantic gating, JIT privilege elevation, and result condensation

### 3.4 Policy and safety layer

- `IntakePolicy` validates intake text and session interactions at the boundary
- `PolicyPipeline` (`src/shared/policy/pipeline.py`, config-driven via `PolicyPipelineConfig.from_dict`) runs the mechanical checks in three phases — intake (`RateLimitPolicy`, `PromptInjectionPolicy`), dispatch (`CallerAuthorizationPolicy`, `AgentCostPolicy`, `TokenBudgetPolicy`), response (`TokenBudgetPolicy.consume`, `SensitiveRedactionPolicy`)
- `GuardianPolicy` performs pre-agent route safety checks; can escalate ambiguous requests to reflection; pre-route check fires with `agent_id="pre_route"` before routing is resolved
- `ReflectionPolicy` attempts safe reformulation when Guardian flags an ambiguous block; resolver calls `evaluate()` to ensure the audit decorator fires on every reflection run
- `RoutingPolicy` gates agent dispatch based on configurable `(env, agent)` rules; new constraints require only a new dict entry — no resolver edits
- `JitElevationPolicy` evaluates session phase and env before a restricted write tool may execute; `collecting` phase is always denied; shadow env follows the same phase rules as prod
- `JitPrivilegeMiddleware` enforces JIT gating at position 0 in the middleware stack — policy check and execution happen in the same step with no token round-trip or extra graph node
- `SemanticToolPolicyMiddleware` scores intent alignment on every tool call; first call uses LLM scoring; subsequent calls in the same run are pre-cleared by Guardian and audited as `checked=False`
- `LLMJudgePolicy` performs post-response quality checks for mutating agents; `source` and `env` propagate through so the audit record carries full context
- `MetadataEnrichmentPolicy` injects statistical, relational, and operational context before agent dispatch; enrichment is audited with `domain` and `elapsed_ms`; the full context block is excluded from audit records to avoid size bloat
- `SemanticSynthesisPolicy` (`src/shared/policy/output/semantic_synthesis_policy.py`) post-agent output policy, split from `MetadataEnrichmentPolicy`; synthesizes registered tool outputs into an LLM narrative using a domain-specific system prompt; returns a synthesis key in the result dict; no-op (no LLM call) when no registered tool message is present; tagged with `@with_token_phase("synthesize")`
- `SensitiveRedactionPolicy` (`src/shared/policy/output/sensitive_redaction_policy.py`) replaces `PiiEgressPolicy`; scrubs or blocks SSN, routing numbers, account numbers, phone numbers, and credit card numbers in agent responses; pre-masks UUID tokens before PII patterns to prevent false positives on structured IDs

### 3.5 Policy audit subsystem

- Every policy gate decision is persisted to an append-only `policy_audit` table (dual relational/document-store backends, env-aware shadow table)
- `PolicyAuditRecord` captures: `audit_id`, `session_id`, `correlation_id`, `request_id`, `policy_type`, `operation`, `allowed`, `reason`, `env`, `metadata`, `created_at`
- `@audit_policy_decision` decorator wraps any `evaluate()` method returning `PolicyDecision`; schedules a fire-and-forget async task — never blocks the request path; swallows all persistence errors
- `audit_inline` provides the same write path for middleware that does not use an `evaluate()` method
- **Selective auditing gate** — `_active_agent` ContextVar is set by the resolver after routing; `_AUDIT_AGENTS = {"boarding_agent", "hierarchy_agent"}` controls full audit scope; non-boarding agents write DENIED-only; ALLOWED read-path decisions are never persisted
- Boarding/hierarchy allowed gate decisions buffer per correlation_id (max 50) and flush as one `pipeline_summary` record — tagged with a domain (`merchant`/`pricing`/`auth`/`platform`/`group`/`general`) inferred from intent or tool operations and stored in `metadata.intent_domain`. Flush runs after `check_response` and again as a safety net at resolver exit
- `llm_judge` and `reflection` write immediately via `await _persist()` (`_ALWAYS_AUDIT_TYPES`) so they can't bleed into the next agent's buffer; `semantic_tool` and denied decisions also stay immediate; non-enrollment agents are unaffected
- `token_budget` writes directly via `_DIRECT_ONLY_TYPES` — bypasses pipeline_summary buffering entirely even for full-audit agents; `TokenBudgetPolicy.consume()` emits a standalone `audit_inline` record after recording actual spend
- Workflow orchestrator steps propagate `request_id`/`session_id` via context so every step writes audit records with full context
- Nine query tools expose audit data to the servicing agent: by session, correlation, policy type, request ID, summary, forensic entity timeline, causal chain, cross-session history, and quality summary
- Policy types covered: `enrollment`, `servicing`, `guardian`, `routing`, `jit_elevation`, `llm_judge`, `reflection`, `metadata`, `semantic_tool`, `live_hydration`, `quality_metric`, `pipeline_summary`, `token_budget`

### 3.6 Storage and data services

- **Core relational storage** holds subjects, entitlements, platforms, groups, and credentials
- **Workflow state store** retains workflow progress, tasks, and audit metadata
- **Graph store** powers relationship traversal, similarity, and risk analytics
- **Policy audit store** — append-only tables indexed on `session_id`, `correlation_id`, `policy_type`, `request_id`, and `created_at`; document-store equivalent uses secondary indexes on the same keys

---

## 4. Policy and Runtime Behavior

### Policy execution order

```text
Intake validation (IntakePolicy)            → audit: enrollment / servicing
  ↓
Intake pipeline (PolicyPipeline.check_intake)
    → RateLimitPolicy → PromptInjectionPolicy
  ↓
Guardian pre-flight (agent_id="pre_route")  → audit: guardian [DENIED only for non-enrollment agents]
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
             MetadataEnrichmentPolicy inject → audit: metadata
                    ↓
             Agent dispatch
                    ↓
             JIT phase/env gate              → audit: jit_elevation (enrollment_agent only)
               ├─ DENIED → [JIT_DENIED] ToolMessage
               └─ GRANTED → SemanticToolPolicy → audit: semantic_tool (every tool call)
                    ↓
             Tools execute
                    ↓
             SemanticSynthesisPolicy.synthesize (post-agent)
               ├─ registered tool result → LLM narrative → synthesis key in result
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
| `enrollment_agent` | Buffered then summarized per request | Written |
| all others | **Skipped** | Written |

Pre-routing decisions (intake validation, guardian pre-route) use `active_agent=""` — outside `_AUDIT_AGENTS` — so they write DENIED-only. This captures blocked intake and pre-route threat blocks without producing noise on the read path.

### LLM call optimization

- Common production path uses zero semantic tool LLM calls when Guardian already clears the intent
- Read-only lookups skip LLMJudge entirely
- Write workflows incur exactly one judge call after execution
- `source=internal` bypasses Guardian's checked-sources filter — internal workflow steps are trusted callers and produce zero policy LLM calls on the common path

### Tool safety

- Deduplication middleware prevents repeated identical tool calls in the same reasoning run
- `JitPrivilegeMiddleware` performs phase/env gating in a single middleware step — GRANTED calls execute immediately with no retry token round-trip
- `ToolResultCondenser` reduces agent payload size by replacing large write results with compact summaries while preserving error detail
- Shadow mode blocks advanced production-only tools (graph analytics, domain expert agents) from executing against live data

---

## 5. Workflow State Model

### Defined states
- `INITIALIZED`
- `AWAITING_GATES`
- `PARTIALLY_APPROVED`
- `APPROVED`
- `ACTIVE`
- `REJECTED`
- `FAILED`

### Transition rules
- `INITIALIZED` → `AWAITING_GATES` or `FAILED`
- `AWAITING_GATES` → `PARTIALLY_APPROVED`, `APPROVED`, `REJECTED`, or `FAILED`
- `PARTIALLY_APPROVED` → `APPROVED`, `REJECTED`, or `FAILED`
- `APPROVED` → `ACTIVE` or `FAILED`
- Terminal states (`ACTIVE`, `REJECTED`, `FAILED`) have no outbound transitions

### Gate evaluation
- Essential gate checks drive final approval
- Any essential failure leads to `REJECTED`
- All essential approvals lead to `APPROVED`
- Mixed partial completion remains in `PARTIALLY_APPROVED`

---

## 6. Reuse and Domain Adaptation

### Conversational intake + draft persistence
The intake layer is field-agnostic. Only the validation schema and intake-side policy rules are domain-specific. Any multi-step guided data collection flow maps directly onto this pattern.

### Policy-as-middleware stack
The layered execution order — Guardian → Routing → JIT → Semantic → Synthesis → Judge — is domain-agnostic. The same pipeline applies regardless of subject type. Only the policy rules, synthesis registry, and thresholds are swapped per domain. `SemanticSynthesisPolicy` uses a `_SYNTHESIS_REGISTRY` dict mapping tool names to system prompts — adding a domain-specific narrative requires one registry entry, no pipeline edits.

### Audit-selective agent gating
The `_AUDIT_AGENTS` pattern — write-heavy agents emit full audit trails; read-only agents emit DENIED-only — is directly transferable. Deployments configure which agents require full coverage vs. lightweight denial-only tracking.

### JIT privilege elevation
Phase-gated write tool access is a general security primitive. Any workflow with a collection → review → confirmation progression can use JIT elevation to prevent premature writes. The phase vocabulary (`collecting`, `offering_optional`, `confirming`, `done`) maps to any linear intake flow.

### Shadow/production isolation
The shadow env pattern — same logic, separate storage, writes permitted — applies to any system requiring safe prompt testing, load simulation, or compliance sandboxing without affecting live records.

### Agent graph + recursion recovery
`AgentExecutor` streams via `astream(stream_mode="values")` and scans partial state for the last successful `ToolMessage` on `GraphRecursionError`. Write tools that already completed are preserved even when the graph hits its node limit — the failure is a graph bookkeeping issue, not a data issue.

---

## 7. Design Principles

- Keep workflow logic separate from presentation and conversational state
- Prefer modular, auditable services over monolithic execution paths
- Enforce policy before state mutation whenever possible
- Make production and shadow behavior explicit and isolated
- Support safe retries, idempotency, and invalid state detection
- Design for extensibility — new policies, agents, or graph capabilities require configuration entries, not core edits

---

## 8. Next Steps

- Add formal L1/L2 architecture diagrams and component map
- Document non-functional requirements for latency, resilience, and policy throughput
- Define shadow/test environment behavior and deployment isolation more explicitly
- Capture security assumptions for credential provisioning and agent tool execution
- Maintain this document as the shared L1/L2 architecture reference

---

## 9. Implementation Notes

### Policy LLM cost optimisation
- `SemanticToolPolicyMiddleware` scores only the **first tool call** per agent run (`_run_checked` ContextVar). Subsequent tool calls in the same run skip the LLM scorer entirely.
- When Guardian clears an intent (LOW risk, `allowed=True`), the resolver sets `_guardian_cleared` before invoking the agent. `abefore_agent` reads this and pre-sets `_run_checked=True` — **zero semantic LLM calls** on the Guardian-cleared common path.
- `LLMJudge` is **skipped for read-only agents** — Guardian already auto-allows them and a wrong lookup answer is low-stakes.
- `SemanticSynthesisPolicy.synthesize()` scans the reversed message list for a registered ToolMessage. If none is found it returns the result unchanged — no LLM call for agents that don't emit registered tool outputs.

### Token phase tracking
- Every LLM-consuming stage wraps its call with `@with_token_phase(phase)`. This sets the `_token_phase` ContextVar so callbacks log which stage spent tokens. Phases: `routing`, `agent`, `synthesize`, `judge`, `reflection`, `prompt_injection`, `metadata_enrichment`, `semantic_tool`, `workflow_decision`.
- LangChain callbacks run in child asyncio tasks where ContextVar writes don't propagate back to the parent. `add_request_tokens(n)` writes into `_task_tokens[_parent_task_id]` on a module-level dict, bypassing the ContextVar barrier. Call `get_request_tokens()` in the parent after agent execution to read the total.
- `@with_token_phase` handles both regular coroutines and `async def` generators (fixes broken SSE streaming when wrapping stream endpoints).

### Agent graph recursion limit and write-tool recovery
- An agent can call a lookup tool before a write tool even when the subject ID is already known, consuming graph nodes before the write.
- `ToolCallLimitMiddleware(run_limit=2, exit_behavior="end")` hard-caps tool calls and forces a text response, preventing a third call from hitting the recursion limit.
- `AgentExecutor.ainvoke` uses `astream(stream_mode="values")`. On `GraphRecursionError`, it scans collected partial state for the last successful `ToolMessage`. If a write tool already completed, the partial result is returned normally — the write is preserved even when the graph fails.

### Data integrity for multi-step mutations
- Deactivating a record calls `get_active_items` and deactivates all matches — not just the first result — to handle prior partial failures.
- Queries with no `ORDER BY` on mutable fields are non-deterministic. Sort by `created_at DESC` to consistently return the most recent record.
- Sequential deactivate + create operations with no transaction wrapper leave a gap on crash. Wrap in a transaction or use conditional writes with compensating logic.

### JIT context propagation
- `_jit_session_phase` and `_jit_session_id` are ContextVars that default to `"collecting"` and `""`. If `set_jit_session` is never called, every restricted tool call is denied.
- Must be called before any resolver invocation: intake stream endpoints, servicing message endpoints, workflow activation nodes, and orchestrator entry points.
- Workflow engine callers use `set_jit_session("done", workflow_id)` — the subject has already cleared the full approval pipeline at that point.
- `env` is NOT read from the JIT ContextVar; it comes from a separate env ContextVar set by the resolver, so env propagation is independent of JIT session setup.
