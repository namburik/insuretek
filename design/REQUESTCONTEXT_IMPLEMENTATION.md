# RequestContext Implementation Roadmap

Complete migration guide for integrating `RequestContext` throughout the codebase.

---

## Phase 1: Core Pipeline (CRITICAL) ⚡

### 1.1 Update `PolicyPipeline.check_intake()`
**File**: `src/shared/policy/pipeline.py:98`

**Before**:
```python
async def check_intake(
    self,
    *,
    message: str = "",
    messages: list | None = None,
    session_id: str = "",
    tenant_id: str = "",
    source: str = "react",
    env: str = "prod",
    **kwargs: Any,
) -> PolicyDecision:
```

**After**:
```python
async def check_intake(self, ctx: RequestContext) -> PolicyDecision:
    # Extract from ctx as needed
    session_id = ctx.session_id
    source = ctx.source
    messages = ctx.messages
    # ... rest of logic unchanged
```

**Affected Policies**:
- `RateLimitPolicy`
- `PromptInjectionPolicy`

---

### 1.2 Update `PolicyPipeline.check_dispatch()`
**File**: `src/shared/policy/pipeline.py:141`

**Before**:
```python
async def check_dispatch(
    self,
    *,
    caller_id: str = "",
    agent_id: str = "",
    session_id: str = "",
    tokens_requested: int = 0,
    messages: list | None = None,
    intent: str = "",
    env: str = "prod",
    tool_hint: str = "",
    **kwargs: Any,
) -> PolicyDecision:
```

**After**:
```python
async def check_dispatch(self, ctx: RequestContext) -> PolicyDecision:
    # Direct access from ctx
    caller_id = ctx.caller_id
    agent_id = ctx.target
    tokens = ctx.tokens_used
    # ... rest of logic unchanged
```

**Affected Policies**:
- `CallerAuthorizationPolicy`
- `AgentCostPolicy`
- `TokenBudgetPolicy`
- `JITElevationPolicy`
- `HydrationPolicy`
- `MetadataEnrichmentPolicy`

---

### 1.3 Update `PolicyPipeline.check_response()`
**File**: `src/shared/policy/pipeline.py:232`

**Before**:
```python
async def check_response(
    self,
    *,
    text: str = "",
    prompt: str = "",
    agent_id: str = "",
    session_id: str = "",
    source: str = "agent_response",
    tokens_used: int = 0,
    **kwargs: Any,
) -> PolicyDecision:
```

**After**:
```python
async def check_response(self, ctx: RequestContext) -> PolicyDecision:
    # Extract result via OutputAdapter
    adapter = OutputAdapter(ctx.result)
    text = adapter.to_text()
    agent_id = ctx.target
    # ... rest of logic unchanged
```

**Affected Policies**:
- `SensitiveRedactionPolicy`
- `LLMJudgePolicy`

---

### 1.4 Update `AgentExecutor._ainvoke()`
**File**: `src/shared/common/router/resolver.py:200+`

**Before**:
```python
async def _ainvoke(self, input: dict, forced_agent: str | None = None) -> dict:
    # Build scattered context
    correlation_id = extract_correlation_id(input)
    session_id = extract_session_id(input)
    source = input.get("source", "web")
    # ... 10+ variables
    
    # Pass separately to each phase
    result = await self._pipeline.check_intake(
        message=message, messages=messages, 
        session_id=session_id, tenant_id=tenant_id,
        source=source, env=env,
    )
```

**After**:
```python
async def _ainvoke(self, input: dict, forced_agent: str | None = None) -> dict:
    from shared.common.infra import RequestContext
    
    # Build context once
    ctx = RequestContext(
        correlation_id=extract_correlation_id(input),
        session_id=extract_session_id(input),
        source=input.get("source", "web"),
        env=input.get("env", "prod"),
        caller_id=input.get("caller_id", ""),
        messages=input.get("messages", []),
        last_msg=get_last_human_message_content(input),
    )
    
    # Pass context to all phases
    result = await self._pipeline.check_intake(ctx)
    result = await self._pipeline.check_dispatch(ctx)
    result = await self._pipeline.check_response(ctx)
```

---

## Phase 2: Guardian & Reflection (HIGH) 🔥

### 2.1 Update `GuardianDispatchPolicy.evaluate()`
**File**: `src/shared/policy/guard/guardian_dispatch_policy.py:31`

**Before**:
```python
async def evaluate(
    self,
    intent: str,
    messages: list,
    agent_id: str,
    source: str = "unknown",
    env: str = "prod",
) -> PolicyDecision:
```

**After**:
```python
async def evaluate(self, ctx: RequestContext) -> PolicyDecision:
    # Guardian checks using ctx
    decision = await self._guardian.evaluate(
        intent=ctx.last_msg,
        agent_id=ctx.target,
        source=ctx.source,
        env=ctx.env,
    )
    ctx.set_guardian_check(
        allowed=decision.allowed,
        reason=decision.reason,
        metadata=decision.metadata,
    )
```

---

### 2.2 Update `GuardianAgent.evaluate()`
**File**: `src/shared/policy/guard/guardian_policy.py:332`

**Before**:
```python
async def evaluate(
    self,
    intent: str,
    agent_id: str,
    source: str = "unknown",
    env: str = "prod",
    **kwargs
) -> PolicyDecision:
```

**After**:
```python
async def evaluate(self, ctx: RequestContext) -> PolicyDecision:
    # Extract from context
    intent = ctx.last_msg
    agent_id = ctx.target
    source = ctx.source
    # ... rest unchanged
```

---

### 2.3 Update `GuardianReflectionPolicy.evaluate()`
**File**: `src/shared/policy/guard/guardian_policy.py:519`

**Before**:
```python
async def evaluate(
    self,
    original_intent: str,
    blocking_reason: str,
    agent_id: str,
    source: str = "unknown",
    env: str = "prod",
    **kwargs
) -> PolicyDecision:
```

**After**:
```python
async def evaluate(
    self,
    blocking_reason: str,
    ctx: RequestContext
) -> PolicyDecision:
    original_intent = ctx.last_msg
    agent_id = ctx.target
    # ... rest unchanged
```

---

## Phase 3: Individual Policies (OPTIONAL) 💡

For each policy's `evaluate()` method, extract needed fields from context instead of separate parameters.

### Pattern for Policy Refactor:
```python
# Old
async def evaluate(
    self,
    caller_id: str,
    agent_id: str,
    session_id: str,
) -> PolicyDecision:

# New
async def evaluate(self, ctx: RequestContext) -> PolicyDecision:
    caller_id = ctx.caller_id
    agent_id = ctx.target
    session_id = ctx.session_id
    # ... rest unchanged
```

**Policies to Refactor** (in any order):
- `CallerAuthorizationPolicy`
- `AgentCostPolicy`
- `TokenBudgetPolicy`
- `HydrationPolicy`
- `MetadataEnrichmentPolicy`
- `RoutingPolicy`
- `PromptInjectionPolicy`
- `SensitiveRedactionPolicy`
- `LLMJudgePolicy`

---

## Phase 4: Middleware & Tools (OPTIONAL) 🔵

### 4.1 Store RequestContext in CorrelationContext
**File**: `src/shared/common/agents/middleware/correlation_middleware.py`

```python
class CorrelationMiddleware(AgentMiddleware):
    async def awrap_tool_call(self, request, handler):
        # Existing correlation_id injection
        correlation_id = CorrelationContext.get()
        
        # NEW: Also store full context if available
        # Tools can access via CorrelationContext.get_request_context()
        
        return await handler(request)
```

### 4.2 Access RequestContext in Tool Handlers
**Files**: `src/mcp_server/*.py` (tool implementations)

```python
def call_tool(name: str, args: dict):
    # Extract from CorrelationContext (set by middleware)
    ctx = CorrelationContext.get_request_context()
    
    # Use for logging, auditing
    logger.info("tool=%s correlation=%s caller=%s", name, ctx.correlation_id, ctx.caller_id)
```

---

## Backward Compatibility

### Support Legacy Code During Transition

**Option 1: Adapter Methods**
```python
# Keep old signature, delegate to new
async def check_intake_legacy(
    self,
    *,
    message: str = "",
    messages: list | None = None,
    session_id: str = "",
    # ... old params
) -> PolicyDecision:
    ctx = RequestContext(
        correlation_id="legacy",
        session_id=session_id,
        messages=messages or [],
        source="legacy",
        env="prod",
    )
    return await self.check_intake(ctx)
```

**Option 2: Gradual Migration**
- Update `PolicyPipeline` first (centralized)
- Update `AgentExecutor` to build context
- Update policies one-by-one as you touch them
- Keep old signatures as deprecated wrappers

---

## Testing Strategy

### Unit Test Pattern
```python
from shared.common.infra import RequestContext

@pytest.mark.asyncio
async def test_policy_with_context():
    ctx = RequestContext(
        correlation_id="test-123",
        source="test",
        env="test",
        session_id="sess-1",
        target="sme_agent",
        tokens_used=500,
    )
    
    policy = MyPolicy(config)
    decision = await policy.evaluate(ctx)
    
    assert decision.allowed
    assert "reason" in decision.metadata
```

### Integration Test Pattern
```python
@pytest.mark.asyncio
async def test_pipeline_with_context():
    ctx = RequestContext(
        correlation_id="test-456",
        source="web",
        session_id="sess-2",
        messages=[...],
        target="sme_agent",
    )
    
    pipeline = PolicyPipeline(config)
    
    # All phases use same context
    result = await pipeline.check_intake(ctx)
    assert result.allowed
    
    result = await pipeline.check_dispatch(ctx)
    assert result.allowed
    
    result = await pipeline.check_response(ctx)
    assert result.allowed
```

---

## Migration Checklist

- [ ] **Phase 1A**: Update `PolicyPipeline.check_intake(ctx)`
- [ ] **Phase 1B**: Update `PolicyPipeline.check_dispatch(ctx)`
- [ ] **Phase 1C**: Update `PolicyPipeline.check_response(ctx)`
- [ ] **Phase 1D**: Update `AgentExecutor._ainvoke()` to build and thread context
- [ ] Test Phase 1 end-to-end (core pipeline working)

- [ ] **Phase 2A**: Update `GuardianDispatchPolicy.evaluate(ctx)`
- [ ] **Phase 2B**: Update `GuardianAgent.evaluate(ctx)`
- [ ] **Phase 2C**: Update `GuardianReflectionPolicy.evaluate(ctx)`
- [ ] Test Phase 2 (guardian flow working)

- [ ] **Phase 3**: Refactor individual policies to accept context
  - [ ] `CallerAuthorizationPolicy`
  - [ ] `AgentCostPolicy`
  - [ ] `TokenBudgetPolicy`
  - [ ] `HydrationPolicy`
  - [ ] `MetadataEnrichmentPolicy`
  - [ ] `RoutingPolicy`
  - [ ] `PromptInjectionPolicy`
  - [ ] `SensitiveRedactionPolicy`
  - [ ] `LLMJudgePolicy`

- [ ] **Phase 4** (Optional):
  - [ ] Store RequestContext in CorrelationContext
  - [ ] Access from middleware and tools
  - [ ] Use `ctx.summary()` for structured logging

---

## Impact Summary

| Metric | Before | After |
|--------|--------|-------|
| Pipeline function signatures | 6-8 params each | 1 param (`ctx`) |
| AgentExecutor variables | 10+ scattered | 1 `RequestContext` object |
| Policy signatures | Varied | Consistent: `evaluate(ctx)` |
| Test setup | Mock 10 args | Mock 1 `RequestContext` |
| Extension (add new field) | Update all callers | Add to `RequestContext` |
| Observability | Scattered logging | `ctx.summary()`, `ctx.policy_decisions` |

---

## Related Files

- New patterns: `src/shared/common/infra/output_adapter.py`, `src/shared/common/infra/request_context.py`
- Usage guide: `PATTERN_USAGE.md`
- Pipeline: `src/shared/policy/pipeline.py`
- Resolver: `src/shared/common/router/resolver.py`
- Policies: `src/shared/policy/**/*.py`

---

## Questions?

Refer to `PATTERN_USAGE.md` for detailed examples and `src/shared/common/infra/request_context.py` for API reference.
