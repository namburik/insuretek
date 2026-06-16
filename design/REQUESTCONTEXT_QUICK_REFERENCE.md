# RequestContext: Quick Reference

Instant lookup for current vs. new signatures.

---

## Core Pipeline Methods

### PolicyPipeline.check_intake()
```python
# CURRENT
await pipeline.check_intake(
    message="...",
    messages=[...],
    session_id="sess-1",
    tenant_id="tenant-1",
    source="web",
    env="prod",
)

# NEW
ctx = RequestContext(
    correlation_id="corr-1",
    session_id="sess-1",
    messages=[...],
    source="web",
    env="prod",
    # ...
)
await pipeline.check_intake(ctx)
```

**Location**: `src/shared/policy/pipeline.py:98`

---

### PolicyPipeline.check_dispatch()
```python
# CURRENT
await pipeline.check_dispatch(
    caller_id="user-1",
    agent_id="sme_agent",
    session_id="sess-1",
    tokens_requested=1000,
    messages=[...],
    intent="explain something",
    env="prod",
    tool_hint="",
)

# NEW
ctx.target = "sme_agent"
ctx.caller_id = "user-1"
ctx.tokens_used = 1000
ctx.tool_hint = ""
await pipeline.check_dispatch(ctx)
```

**Location**: `src/shared/policy/pipeline.py:141`

---

### PolicyPipeline.check_response()
```python
# CURRENT
await pipeline.check_response(
    text="Agent response here",
    prompt="User asked this",
    agent_id="sme_agent",
    session_id="sess-1",
    source="agent_response",
    tokens_used=500,
)

# NEW
ctx.result = agent_result
ctx.target = "sme_agent"
ctx.tokens_used = 500
adapter = OutputAdapter(ctx.result)
await pipeline.check_response(ctx)
```

**Location**: `src/shared/policy/pipeline.py:232`

---

## Guardian Methods

### GuardianDispatchPolicy.evaluate()
```python
# CURRENT
await guardian_dispatch.evaluate(
    intent="explain something",
    messages=[...],
    agent_id="sme_agent",
    source="web",
    env="prod",
)

# NEW
await guardian_dispatch.evaluate(ctx)
# (ctx.last_msg, ctx.target, ctx.source, ctx.env used internally)
```

**Location**: `src/shared/policy/guard/guardian_dispatch_policy.py:31`

---

### GuardianAgent.evaluate()
```python
# CURRENT
await guardian.evaluate(
    intent="explain something",
    agent_id="sme_agent",
    source="web",
    env="prod",
)

# NEW
await guardian.evaluate(ctx)
# (ctx.last_msg, ctx.target, ctx.source, ctx.env used internally)
```

**Location**: `src/shared/policy/guard/guardian_policy.py:332`

---

### GuardianReflectionPolicy.evaluate()
```python
# CURRENT
await reflection.evaluate(
    original_intent="explain something",
    blocking_reason="potential jailbreak detected",
    agent_id="sme_agent",
    source="web",
    env="prod",
)

# NEW
await reflection.evaluate(
    blocking_reason="potential jailbreak detected",
    ctx=ctx,
)
# (ctx.last_msg, ctx.target, ctx.source, ctx.env used internally)
```

**Location**: `src/shared/policy/guard/guardian_policy.py:519`

---

## Individual Policies (Pattern)

### Template for Any Policy
```python
# CURRENT
await policy.evaluate(
    caller_id="user-1",
    agent_id="sme_agent",
    session_id="sess-1",
    some_field="value",
)

# NEW
await policy.evaluate(ctx)
# Inside: caller_id = ctx.caller_id; agent_id = ctx.target; etc.
```

---

## AgentExecutor Main Loop

### AgentExecutor._ainvoke()
```python
# CURRENT: Build scattered variables
async def _ainvoke(self, input: dict, forced_agent: str | None = None) -> dict:
    correlation_id = extract_correlation_id(input)
    session_id = extract_session_id(input)
    source = input.get("source", "web")
    env = input.get("env", "prod")
    messages = input.get("messages", [])
    # ... 10+ more variables
    
    # Pass separately to each phase
    intake_result = await self._pipeline.check_intake(
        message=..., messages=..., session_id=..., tenant_id=..., source=..., env=...
    )
    dispatch_result = await self._pipeline.check_dispatch(
        caller_id=..., agent_id=..., session_id=..., tokens_requested=..., messages=..., intent=..., env=..., tool_hint=...
    )
    response_result = await self._pipeline.check_response(
        text=..., prompt=..., agent_id=..., session_id=..., source=..., tokens_used=...
    )

# NEW: Build context once
async def _ainvoke(self, input: dict, forced_agent: str | None = None) -> dict:
    from shared.common.infra import RequestContext
    
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
    intake_result = await self._pipeline.check_intake(ctx)
    dispatch_result = await self._pipeline.check_dispatch(ctx)
    response_result = await self._pipeline.check_response(ctx)
```

**Location**: `src/shared/common/router/resolver.py` (around line 200+)

---

## OutputAdapter Usage

### Use Everywhere Tool Results Appear
```python
# OLD (scattered logic)
def _summarize_result(result) -> str:
    if isinstance(result, dict):
        keys = list(result.keys())[:10]
        # ... more logic
    return "..."

extracted_text = extract_result_content(result)
result_dict = result if isinstance(result, dict) else {}

# NEW (unified)
from shared.common.infra import OutputAdapter

adapter = OutputAdapter(result)
summary = adapter.to_summary()
text = adapter.to_text()
data = adapter.to_dict()
```

---

## CorrelationContext Integration (Phase 4 - Optional)

### Store RequestContext
```python
# In middleware or resolver
from shared.common.infra.context import CorrelationContext

ctx = RequestContext(...)
CorrelationContext.set_request_context(ctx)

# In tools or downstream code
ctx = CorrelationContext.get_request_context()
logger.info("caller=%s correlation=%s", ctx.caller_id, ctx.correlation_id)
```

---

## Common Extraction Patterns

### Extract from RequestContext
```python
# Session/correlation tracking
session_id = ctx.session_id
correlation_id = ctx.correlation_id

# Routing info
agent = ctx.target
confidence = ctx.routing_confidence

# Security
caller_id = ctx.caller_id
source = ctx.source

# Execution
result = ctx.result
error = ctx.error
tokens = ctx.tokens_used

# Policy tracking
is_blocked = ctx.is_blocked()
decisions = ctx.policy_decisions
```

---

## Testing Patterns

### Build RequestContext for Tests
```python
from shared.common.infra import RequestContext

ctx = RequestContext(
    correlation_id="test-123",
    source="test",
    env="test",
    session_id="sess-test",
    caller_id="test-user",
    messages=[{"role": "user", "content": "test message"}],
    target="sme_agent",
)

# Use in tests
result = await policy.evaluate(ctx)
assert result.allowed
```

### Build OutputAdapter for Tests
```python
from shared.common.infra import OutputAdapter

result = {"messages": [...], "metadata": {...}}
adapter = OutputAdapter(result)

assert adapter.has_messages()
assert adapter.message_count() == 2
assert "some_key" in adapter.to_dict()
```

---

## Phase-by-Phase Checklist

### When updating check_intake():
- [ ] Accept `ctx: RequestContext`
- [ ] Extract `ctx.session_id`, `ctx.source`, `ctx.messages`
- [ ] Update RateLimitPolicy callers
- [ ] Update PromptInjectionPolicy callers

### When updating check_dispatch():
- [ ] Accept `ctx: RequestContext`
- [ ] Extract `ctx.caller_id`, `ctx.target`, `ctx.tokens_used`
- [ ] Update all access policy callers
- [ ] Update hydration and enrichment callers

### When updating check_response():
- [ ] Accept `ctx: RequestContext`
- [ ] Use `OutputAdapter(ctx.result)` for result handling
- [ ] Extract `ctx.target`, `ctx.tokens_used`
- [ ] Update judge and redaction callers

### When updating AgentExecutor:
- [ ] Build `RequestContext` at start of `_ainvoke()`
- [ ] Pass to all pipeline phases
- [ ] Update `ctx.target` after routing
- [ ] Update `ctx.result` after execution
- [ ] Use `ctx.summary()` in logs

---

## Impact: Before & After Example

### Complete Flow: Before
```python
# In _ainvoke()
correlation_id = extract_correlation_id(input)
session_id = extract_session_id(input)
caller_id = input.get("caller_id", "")
source = input.get("source", "web")
env = input.get("env", "prod")
messages = input.get("messages", [])
intent = get_last_human_message_content(input)

# Intake check
intake = await self._pipeline.check_intake(
    message=intent, messages=messages, session_id=session_id,
    tenant_id=caller_id, source=source, env=env,
)

# Dispatch check (if intake passed)
dispatch = await self._pipeline.check_dispatch(
    caller_id=caller_id, agent_id=target, session_id=session_id,
    tokens_requested=estimated_tokens, messages=messages,
    intent=intent, env=env, tool_hint=tool_hint,
)

# Response check (if execution succeeded)
response = await self._pipeline.check_response(
    text=agent_text, prompt=intent, agent_id=target,
    session_id=session_id, source=source, tokens_used=tokens,
)
```

### Complete Flow: After
```python
# In _ainvoke()
ctx = RequestContext(
    correlation_id=extract_correlation_id(input),
    session_id=extract_session_id(input),
    caller_id=input.get("caller_id", ""),
    source=input.get("source", "web"),
    env=input.get("env", "prod"),
    messages=input.get("messages", []),
    last_msg=get_last_human_message_content(input),
)

# Intake check
intake = await self._pipeline.check_intake(ctx)

# Dispatch check (if intake passed)
ctx.target = target
ctx.tokens_used = estimated_tokens
ctx.tool_hint = tool_hint
dispatch = await self._pipeline.check_dispatch(ctx)

# Response check (if execution succeeded)
ctx.result = agent_result
response = await self._pipeline.check_response(ctx)

logger.info("request completed: %s", ctx.summary())
```

**Diff**: -15 lines of variable setup, +1 line context creation, -2 lines passing params = **cleaner, more maintainable**.

---

## Files to Modify

| File | Method | Current Params | Change |
|------|--------|----------------|--------|
| `pipeline.py:98` | `check_intake()` | 6 | → `ctx` |
| `pipeline.py:141` | `check_dispatch()` | 8 | → `ctx` |
| `pipeline.py:232` | `check_response()` | 6 | → `ctx` |
| `guardian_dispatch_policy.py:31` | `evaluate()` | 5 | → `ctx` |
| `guardian_policy.py:332` | `evaluate()` | 4 | → `ctx` |
| `guardian_policy.py:519` | `evaluate()` | 5 | → `blocking_reason`, `ctx` |
| `resolver.py:200+` | `_ainvoke()` | scatter | → build `ctx` |

**Total**: 7 core methods to refactor in Phase 1-2. Enables 10+ policy refactors in Phase 3.
