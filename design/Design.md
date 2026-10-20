# Insuretek — Technical Design Document

> **Audience:** Product, operations, security, and engineering stakeholders
> **Status:** Living document — updated as the system evolves

---

## 1. Purpose

Insuretek automates applicant onboarding for insurance products by combining conversational intake, policy-driven validation, and workflow orchestration with semantic decision support. The design prioritizes safe, auditable onboarding flows while preserving the flexibility to support approvals, review, and interruption.

### Outcomes
- Complete applicant onboarding in a guided automated flow
- Clear separation of intake, workflow execution, and agent reasoning
- Strong compliance gating with auditability and retry safety
- Support for production and shadow environments with distinct runtime behavior

---

## 2. L1 Architecture — System Boundaries

### Package structure

The codebase is split into two tiers with a hard dependency boundary:

| Package | Role | Imports |
|---|---|---|
| `mltek` | Control layer — framework, policies, routing, agent execution | No domain imports |
| `insuretek` | Domain layer — implements `mltek.domain_provider.DomainPolicyProvider` | Imports from `mltek` only |
| `core_data` | Data access layer — repositories, DB models, audit persistence | Imports from `insuretek` domain models |
| `bff_service` | Intake layer — conversational session, prompt validation | Imports `mltek`, `insuretek`, `core_data` |
| `mcp_server` | MCP tool server — exposes data tools to agents | Imports `insuretek`, `core_data` |
| `workflow_engine` | Workflow state machine and orchestration | Imports `mltek`, `insuretek`, `core_data` |
| `web_gateway` | HTTP layer — routes, React frontend | Imports `bff_service`, `workflow_engine` |

### Primary system layers
- **Intake layer**: conversational data collection, validation, draft persistence, and session handling
- **Workflow layer**: orchestrates onboarding steps, manages async gate checks, and enforces state transitions
- **Agent layer**: routes intent, executes specialized actions, and provides semantic reasoning
- **Storage layer**: persists applicant state, workflow checkpoints, and audit records

### L1 logical flow

```text
User input → BFF Intake → mltek Resolver (AgentExecutor)
                  ↓                     ↓
            Prompt policies      Intent routing → Agent dispatch
                  ↓                     ↓
          Session validation     MCP tool execution
                  ↓                     ↓
       Response / audit trail    core_data / DynamoDB updates
```

### Key architectural boundaries
- **`mltek`** owns all policy machinery, agent execution, audit infrastructure, and the resolver. It never imports from `insuretek` directly — all domain behaviour enters through the `DomainPolicyProvider` interface.
- **`insuretek`** is the plugin layer. `InsureTekDomainProvider` wires domain-specific prompts, configs, fetchers, and factories into the `mltek` resolver at startup.
- **`core_data`** is the data access layer. Repositories and services are consumed by the MCP server, BFF, and workflow engine.
- **`web_gateway`** is the sole HTTP entry point — it delegates intake to `bff_service` and workflow operations to `workflow_engine`.

---

## 3. L2 Component Design

### 3.1 Control layer — `mltek`

`mltek` is the reusable framework. It never imports domain code; all domain customization flows in via `DomainPolicyProvider`.

#### 3.1.1 Resolver (`mltek/common/router/resolver.py`)

Central entry point for all agent requests. Implements `AgentExecutor` and the public helpers `call_resolver` / `call_resolver_safe`.

Execution phases (in order):
1. `_run_intake_control` — rate limit, prompt injection, live hydration
2. `_run_pre_route_safety` — Guardian pre-flight (hard block, no reflection)
3. `_resolve_target_agent` — keyword match → semantic router → LLM intent classifier
4. `_run_dispatch_control` — caller auth, cost, token budget, routing admission, metadata enrichment
5. `_run_post_route_safety` — post-route Guardian + Reflection escalation
6. `_execute_agent` — stream selected agent to completion (with recursion-limit recovery)
7. `synthesize` — optional post-agent ISAR synthesis
8. `_process_response_control` — PII redaction, token accounting, audit flush, LLM judge

#### 3.1.2 `DomainPolicyProvider` (`mltek/domain_provider.py`)

Abstract contract that domain packages implement once and register via `register_domain_provider()`. The resolver calls only this interface — it never imports from the domain directly. Key methods:

| Method | Purpose |
|---|---|
| `get_hydration_fetchers()` | Entity-type → fetcher callable mapping for live context injection |
| `get_metadata_store()` | `MetadataStore` for `MetadataEnrichmentPolicy` |
| `get_synthesis_policy()` | Post-agent synthesis policy (e.g. `SemanticSynthesisPolicy`), or `None` |
| `get_agent_domain_mapping()` | `agent_id → domain` string for enrichment and hydration |
| `get_system_prompt(type)` | Domain-specific LLM system prompts (judge, guardian, injection, etc.) |
| `get_caller_auth_config()` | Role → agent mapping from domain `policy_config.yaml` |
| `get_agent_cost_config()` | Per-agent call-limit config |
| `get_jit_elevation_config()` | Restricted tools and blocked phases |
| `get_guardian_config()` | Guardian threat-detection rules |
| `get_routing_config()` | Keyword routes and intent resolver system prompt |
| `get_audit_config()` | Audit agents, domain keywords, entity kwarg map |
| `get_entity_patterns()` | Entity ID regex patterns for hydration extraction |
| `get_audit_writer()` | `(write_fn, emit_fn)` for policy audit persistence |
| `get_agent_factory()` | Optional domain `AgentFactory` instance registered at startup |

#### 3.1.3 Policy layer (`mltek/policy/`)

All policy classes live in `mltek/policy/`. Sub-packages:

| Sub-package | Policies |
|---|---|
| `access/` | `CallerAuthorizationPolicy`, `AgentCostPolicy`, `RateLimitPolicy`, `TokenBudgetPolicy`, `JitElevationPolicy`, `RoutingPolicy` |
| `guard/` | `GuardianAgent`, `GuardianDispatchPolicy`, `PromptInjectionPolicy` |
| `context/` | `LiveHydrationPolicy`, `MetadataEnrichmentPolicy` |
| `output/` | `LLMJudgePolicy`, `SensitiveRedactionPolicy`, `SynthesisPolicy` (base) |
| `metrics/` | `EvaluationPolicy`, `RoutingMetrics` |
| `pipeline.py` | `PolicyPipeline` — orchestrates `check_intake / check_dispatch / check_response` |
| `abstract_policy.py` | `PolicyDecision`, `AbstractPolicy` base class |

`PolicyPipeline` accepts optional domain-specific instances for hydration, routing, metadata enrichment, synthesis, judge, guardian, and prompt injection — all provided by the domain via `DomainPolicyProvider`.

#### 3.1.4 Agent layer (`mltek/agents/`)

LangChain-backed agent infrastructure:

| Module | Role |
|---|---|
| `agent_factory.py` | Generic factory; domain overrides via `register_agent_factory()` |
| `graph.py`, `graph_builder.py` | LangGraph state-machine construction |
| `lang_agents.py` | `call_structured_agent`, cached agent map, token tracking callbacks |
| `bridge.py` | LangChain / LangSmith import shims |
| `prompts.py` | Generic agent prompt templates |
| `middleware/` | `JitPrivilegeMiddleware`, `SemanticToolPolicyMiddleware` (tool_intent), `ToolCallLimitMiddleware` (loop_guard), `ToolResultCondenser`, `CorrelationMiddleware` |

#### 3.1.5 Orchestration layer (`mltek/orchestration/workflow_orchestrator.py`)

Generic YAML/JSON workflow runner. Loads a step-list definition and executes each step through `call_resolver` — no `agent:` declaration needed in YAML. Steps support prompt templating, state extraction, skip conditions, and `eval_type` quality labelling.

#### 3.1.6 Common infrastructure (`mltek/common/infra/`)

| Module | Purpose |
|---|---|
| `context.py` | ContextVars: `_env_var`, `_active_agent`, `_token_phase`, `_jit_session_phase`, `_guardian_cleared`, etc. |
| `audit_decorator.py` | `@audit_policy_decision`, `audit_inline`, `flush_pipeline_summary`, selective buffering per `_AUDIT_AGENTS` |
| `request_context.py` | `RequestContext` — per-request state threaded through pipeline phases |
| `config_loader.py` | `load_config` YAML loader |
| `jit_token.py` | `set_jit_session` / JIT ContextVar helpers |
| `output_adapter.py` | `OutputAdapter` — normalizes agent result shapes |
| `error_helpers.py` | Shared error-handling decorators |
| `vendor.py` | Bedrock inline-context helpers |

### 3.2 Domain layer — `insuretek`

`InsureTekDomainProvider` (`insuretek/domain_provider.py`) is the single glue point between the domain and `mltek`. It is registered once at startup via `register_domain_provider(InsureTekDomainProvider())`.

```text
startup
  └── register_domain_provider(InsureTekDomainProvider())
        ├── init_audit_config()         ← uses get_audit_config()
        ├── init_routing_config()       ← uses get_routing_config()
        ├── init_entity_patterns()      ← uses get_entity_patterns()
        └── register_agent_factory()    ← uses get_agent_factory()
```

Domain sub-packages:

| Sub-package | Contents |
|---|---|
| `insuretek/domain/` | `model_applicant.py`, `enums.py`, `constants.py`, `ids.py`, `validators.py` |
| `insuretek/common/agents/` | `factory.py` (`InsureTekAgentFactory`), `prompts.py` (all system prompts, `AGENT_DOMAIN`, `GUARDIAN_GUARDED_AGENTS`) |
| `insuretek/policy/context/` | `hydration_fetchers.py` (`INSURETEK_FETCHERS`), `metadata_store.py` (`StaticMetadataStore`) |
| `insuretek/policy/output/` | `semantic_synthesis_policy.py` (ISAR-guided LLM narrative), `isar_evidence_chain.py` |
| `insuretek/config/` | `resolver_config.yaml`, `policy_config.yaml`, `config.py` |

Domain config files are the single source of truth for all policy parameters (guardian config, caller auth, agent cost, JIT elevation, routing, audit agents).

### 3.3 Data access layer — `core_data`

| Sub-package | Contents |
|---|---|
| `core_data/db/models/` | `models_applicant.py`, `models_policy_audit.py`, `models_base.py` |
| `core_data/db/dynamodb/` | `factory.py` (table factory), `db_init.py` |
| `core_data/db/graph/` | Neo4j client, graph operations, sync worker |
| `core_data/db/vector/` | Vector store factory |
| `core_data/repositories/` | `applicant_repository.py`, `policy_audit_repository.py`, `vector_repository.py`, `portal_access_repository.py`, `login_activity_repository.py` |
| `core_data/service/` | `applicant_service.py`, `policy_audit_service.py`, `graph_service.py` |

`policy_audit_service.get_audit_writer()` returns `(write_fn, emit_fn)` consumed by `InsureTekDomainProvider.get_audit_writer()`.

### 3.4 Intake layer — `bff_service`

- Captures applicant draft data through conversational turns
- Validates required fields and preserves partial progress
- Maintains in-memory session state keyed by `session_id` (shortened to 8 hex chars)
- Applies intake-side policy checks (`prompt_validation_policy.py`) for sensitive content and malformed input
- Calls `call_resolver` / `call_resolver_safe` for all LLM-driven responses

### 3.5 MCP tool server — `mcp_server`

Exposes domain data as MCP tools consumed by agents during execution:

| Handler | Tools exposed |
|---|---|
| `handlers/applicant_tools.py` | Applicant CRUD, lookup |
| `handlers/graphrag_tools.py` | Graph relationship queries |
| `handlers/policy_tools.py` | Policy audit query tools (9 tools) |
| `insuretek_core_data.py` | Core data tools |
| `insuretek_agent_workflow.py` | Workflow step tools |
| `insuretek_semantic_context.py` | ISAR explain, semantic context |

### 3.6 Workflow engine — `workflow_engine`

| Sub-package | Contents |
|---|---|
| `domain/workflow_domain.py` | Workflow states, transition rules, gate evaluation logic |
| `repository/` | Workflow repository, checkpointer, request repository |
| `service/applicant_workflow.py` | Applicant onboarding workflow driver |
| `service/workflow_graph.py` | LangGraph-based workflow state machine |
| `service/orchestrator_graph.py` | Orchestrator graph integration |
| `service/gate_check_client.py` | External gate-check HTTP client |
| `service/workflow_endpoints.py` | Internal HTTP endpoints for workflow operations |
| `config/gate_check_config.py` | Gate-check configuration |

### 3.7 HTTP gateway — `web_gateway`

Starlette/FastAPI application. Routes delegate to `bff_service` (intake) and `workflow_engine` (workflow state transitions). React frontend served from `web_gateway/static/`.

---

## 4. Policy and Runtime Behavior

### Policy execution order

```text
Intake validation (BFF prompt_validation_policy)
  ↓
Intake pipeline (PolicyPipeline.check_intake)
    → RateLimitPolicy → PromptInjectionPolicy → LiveHydrationPolicy
  ↓
Guardian pre-flight (agent_id="pre_route")  → audit: guardian [DENIED only for non-boarding]
  ├─ Hard block → return immediate rejection
  └─ Allow → intent classification (keyword / semantic / LLM)
               ↓
             RoutingPolicy check             → audit: routing
               ├─ Block (env/agent rule) → return rejection
               └─ Allow
                    ↓
             Dispatch pipeline (PolicyPipeline.check_dispatch)
               → CallerAuthorizationPolicy → AgentCostPolicy → TokenBudgetPolicy
               → MetadataEnrichmentPolicy inject    → audit: metadata
                    ↓
             GuardianDispatchPolicy (post-route)     → audit: guardian
               ├─ Hard block → return rejection
               ├─ Needs reflection → ReflectionPolicy → audit: reflection
               └─ Allow
                    ↓
             Agent dispatch (@with_token_phase("agent"))
                    ↓
             JIT phase/env gate              → audit: jit_elevation (write agents only)
               ├─ DENIED → [JIT_DENIED] ToolMessage
               └─ GRANTED → SemanticToolPolicyMiddleware → audit: semantic_tool
                    ↓
             MCP Tools execute
                    ↓
             PolicyPipeline.synthesize()
               └─ SemanticSynthesisPolicy (domain-supplied) → isar_synthesis key
                    ↓
             PolicyPipeline.check_response()
               → LLMJudgePolicy (mutating agents)  → audit: llm_judge
               → TokenBudgetPolicy.consume         → audit: token_budget
               → SensitiveRedactionPolicy
                    ↓
             flush_pipeline_summary (boarding/hierarchy → pipeline_summary audit)
```

### Intent routing order

```text
1. Keyword match (regex over `policy_config.yaml` keyword_routes)
2. Semantic router (embedding similarity, threshold=0.35 in auto mode)
3. LLM intent classifier (structured output → IntentResolution)
4. Fallback to default_route (sme_agent)
```

Routing mode is configurable via `resolver_config.yaml` `graph.routing_mode` (`auto` | `semantic` | `intent`).

### Audit write rules

| Agent | ALLOWED decisions | DENIED decisions |
|---|---|---|
| agents in `audit_agents` (config) | Buffered per `correlation_id`, flushed as `pipeline_summary` | Written immediately |
| all others | **Skipped** | Written immediately |

Pre-routing decisions (intake, guardian pre-route) use `active_agent=""` — not in `audit_agents` — so they write only on DENIED. This prevents noise on the normal read path.

`_ALWAYS_AUDIT_TYPES`: `llm_judge`, `reflection` — written immediately, never buffered.
`_DIRECT_ONLY_TYPES`: `token_budget` — bypasses `pipeline_summary` buffering entirely.

### LLM call optimization

- When Guardian pre-clears an intent (`_guardian_cleared=True`), `SemanticToolPolicyMiddleware` skips the LLM scorer for the first tool call — zero semantic LLM calls on the common Guardian-cleared path.
- Read-only lookups skip `LLMJudge` entirely.
- Write workflows incur only one judge call after execution.
- `SemanticSynthesisPolicy.synthesize()` only fires when a matching `ToolMessage` is found in the reversed message list — no LLM call for non-synthesis agents.

### Tool safety

- `JitPrivilegeMiddleware` — position 0 in write-agent middleware stack. Phase/env gate evaluated in a single step; GRANTED calls execute immediately without a token round-trip.
- `SemanticToolPolicyMiddleware` — scores intent alignment on every tool call. First scored call uses LLM; pre-cleared calls skip scoring.
- `ToolCallLimitMiddleware` — hard-caps tool calls per agent run and forces a text response to prevent recursion-limit failures.
- `ToolResultCondenser` — compacts large write-tool results to reduce agent payload size.
- Deduplication: identical tool calls within the same reasoning run are suppressed by `CorrelationMiddleware`.
- Shadow mode blocks specific agents and write tools via `RoutingPolicy` declarative rules.

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

## 6. Policy audit subsystem

- Every policy gate decision is persisted to an append-only DynamoDB `policy_audit` table (shadow table for shadow env)
- `PolicyAuditRecord` captures: `audit_id`, `session_id`, `correlation_id`, `request_id`, `policy_type`, `operation`, `allowed`, `reason`, `env`, `metadata`, `created_at`
- `@audit_policy_decision` decorator wraps any `evaluate()` method returning `PolicyDecision`; schedules a fire-and-forget `asyncio.create_task` — never blocks the request path; swallows all persistence errors
- `audit_inline` provides the same write path for middleware that doesn't use a Policy `evaluate()` method
- Boarding/write-agent allowed gate decisions buffer per `correlation_id` (`_correlation_buffer`, max 50) and flush as one `pipeline_summary` record tagged with `intent_domain`
- Nine MCP tools expose audit data: `get_policy_audit_by_session`, `get_policy_audit_by_correlation`, `get_policy_audit_by_policy_type`, `get_policy_audit_by_request_id`, `get_policy_audit_summary`, `get_forensic_audit`, `get_causal_chain`, `get_cross_session_history`, `get_quality_summary`
- Policy types: `onboarding`, `servicing`, `guardian`, `routing`, `jit_elevation`, `llm_judge`, `reflection`, `metadata`, `semantic_tool`, `live_hydration`, `quality_metric`, `pipeline_summary`, `token_budget`

---

## 7. Design principles

- `mltek` is domain-agnostic: it imports no domain code; all customization enters through `DomainPolicyProvider`
- Keep workflow logic separate from presentation and conversational state
- Prefer modular, auditable services over monolithic execution paths
- Enforce policy before state mutation whenever possible
- Make production and shadow behavior explicit and isolated via `RoutingPolicy` declarative rules
- Support safe retries, idempotency, and invalid state detection
- Design for extensibility: new domain requires only a new `DomainPolicyProvider` implementation

---

## 8. Next steps

- Add a formal L1/L2 architecture diagram and component map
- Document non-functional requirements for latency, resilience, and policy throughput
- Define shadow/test environment behavior and deployment isolation more explicitly
- Capture security assumptions for credential provisioning and agent tool execution
- Maintain this document as the shared L1/L2 architecture reference

---

## 9. Things to remember

### Policy LLM cost optimisation
- `SemanticToolPolicyMiddleware` scores only the **first tool call** per agent run (`_run_checked` ContextVar). Subsequent tool calls in the same run skip the LLM scorer entirely.
- When Guardian clears an intent (LOW risk, `allowed=True`), the resolver sets a `_guardian_cleared` ContextVar before invoking the agent. `abefore_agent` reads this and pre-sets `_run_checked=True`, so **zero semantic LLM calls** occur on the Guardian-cleared common path.
- `LLMJudge` is **skipped for read-only agents** — Guardian already auto-allows them and a wrong lookup answer is low-stakes.
- `SemanticSynthesisPolicy.synthesize()` scans reversed message list for a ToolMessage whose `name` is in `_SYNTHESIS_REGISTRY` and `status != "error"`. If none found it returns the result unchanged — no LLM call on the default path for non-synthesis agents.

### Token phase tracking
- Every LLM-consuming stage wraps its call with `@with_token_phase(phase)` or `async with with_token_phase(phase)`. Stages: `routing`, `agent`, `synthesize`, `judge`, `reflection`, `prompt_injection`, `metadata_enrichment`, `semantic_tool`, `workflow_decision`.
- LangChain callbacks run in child tasks. ContextVar writes from child tasks don't reach the parent — use `add_request_tokens(n)` which writes into `_task_tokens[_parent_task_id]` on the shared module-level dict. Call `get_request_tokens()` in the parent after agent execution to read the total.
- `@with_token_phase` handles `async def` generators via `inspect.isasyncgenfunction` to avoid breaking SSE streaming.

### Agent recursion limit and write-tool recovery
- `ToolCallLimitMiddleware(run_limit=N, exit_behavior="end")` is added to write agents to hard-cap tool calls and force a text response, preventing a graph recursion limit failure.
- `AgentExecutor._run_agent` uses **`astream(stream_mode="values")`** instead of `ainvoke`. On `GraphRecursionError`, it scans the collected partial state for the last successful `ToolMessage`. If a write tool already completed, the partial result is returned normally — the orchestrator marks the step **PASS** instead of FAIL.

### JIT context propagation
- `_jit_session_phase` and `_jit_session_id` are ContextVars that default to `"collecting"` and `""`. If `set_jit_session` is never called, every restricted tool call is denied.
- Must be called before any `call_resolver` / `call_resolver_safe` invocation: BFF endpoints, workflow activation node, and orchestrator.
- Workflow engine callers use `set_jit_session("done", workflow_id)` — applicant has already cleared the full approval pipeline at that point.
- `env` is NOT read from the JIT ContextVar; it comes from `_env_var` (set by `set_env` in the resolver).

### Agent source trust
- `source=internal` bypasses Guardian's `checked_sources` filter (which defaults to `{"react"}`). Internal workflow steps are trusted callers; Guardian skips them and returns LOW/allowed. The `_guardian_cleared` ContextVar is still set, so semantic and judge are also skipped — zero policy LLM calls for internal workflow steps on the common path.

### Domain provider registration
- `register_domain_provider(InsureTekDomainProvider())` must be called once at startup before any `call_resolver` invocation.
- Registration triggers lazy initialization of `audit_config`, `routing_config`, and `entity_patterns` (via `init_*` helpers called inside `register_domain_provider`).
- `resolver_config.yaml` keys that are absent fall back to the domain provider's getter methods, so YAML is the primary source and the provider is the fallback.
