# Simplification Patterns: Usage Guide

Implemented patterns: **PydanticAI Structured Output**, **Config `model_validate()`**, **LLM JSON Repair**, **RequestContext Bridge**.
Proposed patterns: **OutputAdapter** and **RequestContext**.

---

## Pattern 7: Structured LLM Output ✅ Implemented

Replace `PydanticOutputParser` + format injection + string parsing with a single typed call.

```python
# Before — 3 moving parts per model
_PARSER = PydanticOutputParser(output_cls=MyModel)
raw = await call_chat_agent(task, system_prompt="..." + _PARSER.get_format_instructions())
result = _PARSER.parse(raw)  # + regex fallback on failure

# After — 1 call, validated instance returned
result = await call_structured_agent(task, MyModel, system_prompt=SYSTEM_PROMPT)
```

**Entry point**: `call_structured_agent(task, result_type, system_prompt, model)` in `lang_agents.py`. Internally uses `call_chat_agent` + `parse_llm_json` — no pydantic-ai Agent machinery, works reliably with local Ollama models. Provider routing is handled internally.

**Schema generation — `_flat_schema()` not `model_json_schema()`**: small Ollama models get confused by raw JSON Schema (`$defs`, `$ref`, `anyOf`, `title`, nested `properties`) and emit malformed JSON in response. `_flat_schema(result_type)` walks `model_fields` directly and renders a flat, human-readable field list instead:

```python
# Before — full JSON Schema, verbose and model-confusing
schema_str = json.dumps(result_type.model_json_schema(), indent=2)

# After — simple field list
schema_str = _flat_schema(result_type)
# {
#   "agent_id": string  // which agent should handle this
#   "tool_hint": string
# }
```

**Rule placement — system_prompt, not task**: `call_structured_agent` appends the schema instruction to `system_prompt`. Any domain rules (disambiguation logic, routing priorities, etc.) must also go in `system_prompt`, not the `task` argument — the model weighs system-prompt content more heavily, so rules split across both compete unevenly and task-only rules get ignored. Fixed for the intent resolver: `INTENT_RESOLVER_SYSTEM` (rules, no `{message}`) is formatted and passed as `system_prompt`; the raw user message is the `task`.

**Rule**: `call_chat_agent()` for plain text. `call_structured_agent()` for typed output. Nothing else.

---

## Pattern 9: LLM JSON Repair ✅ Implemented

Parse JSON from LLM text responses with progressive repair for common model output issues.

```python
# Before — single json.loads, brittle
data = json.loads(re.search(r'\{.*\}', raw, re.DOTALL).group(0))

# After — 6-pass repair, handles real model output
from shared.common.agents.lang_agents import parse_llm_json
data = parse_llm_json(raw_or_extracted_blob, context="MyModel")
result = MyModel.model_validate(data)
```

**Repair passes** (in order, stops at first success — each pass transforms a clean base, not the previous pass's output, so failures don't compound):
1. `json.loads` — clean path
2. `True/False/None` → `true/false/null` — Python-style literals
3. `ast.literal_eval` — single-quoted strings, Python dict syntax
4. `_quote_unquoted_keys()` — quotes bare object keys (`reason:` → `"reason":`)
5. `_insert_missing_commas()` — inserts commas the model dropped between adjacent fields (newline-adjacent quoted props, `}{` and `][` adjacency)
6. Regex — quotes bare unquoted string values (`"reason": text` → `"reason": "text"`)

Key-quoting runs before comma-insertion, which runs before value-quoting — each pass needs the prior structural fix in place to find its own pattern correctly.

**Rule**: Never call `json.loads` directly on LLM output. Use `parse_llm_json`. `call_structured_agent` calls it automatically. If `parse_llm_json` still fails on a complex nested schema, check whether the schema itself (passed via `_flat_schema()` in the prompt, see Pattern 7) is too verbose for the model — simplifying the schema is often more effective than adding more repair passes.

---

## Pattern 8: Config `BaseModel` + `model_validate()` ✅ Implemented

Replace `@dataclass + from_dict()` with `BaseModel`. Pydantic handles type coercion, defaults, and missing keys automatically.

```python
# Before — manual coercion duplicates field types
@dataclass
class SomeConfig:
    window_secs: float = 60.0
    max_calls: int = 30
    @classmethod
    def from_dict(cls, d): return cls(window_secs=float(d.get(...)), max_calls=int(d.get(...)))

# After — no from_dict() needed
class SomeConfig(BaseModel):
    window_secs: float = 60.0
    max_calls: int = 30

config = SomeConfig.model_validate(yaml_dict.get("some_config", {}))
```

**Special cases**:
- Dynamic defaults (`frozenset`, etc.) → `Field(default_factory=lambda: ...)`
- Post-init computation (e.g. compiling regex) → `@model_validator(mode='after')`
- Enum-keyed dict with default merge → `@model_validator(mode='before')`

**Rule**: Never write `from_dict()` on a config class. Add a `model_validator` only for logic Pydantic can't infer from the type annotation alone.

---

## Pattern 5: OutputAdapter (Result Normalization)

Replace scattered extraction helpers (`_summarize_result`, `extract_result_content`, etc.) with one adapter that normalises any agent result to the format needed at the call site.

```python
# Before — different helper per format
text = extract_result_content(result)
logger.info("result=%s", _summarize_result(result))

# After — one object, pick the format you need
adapter = OutputAdapter(result)
text    = adapter.to_text()
summary = adapter.to_summary()   # compact log string
data    = adapter.to_dict()
items   = adapter.to_list()
field   = adapter.get_field("error", default=None)
```

**Import**: `from shared.common.infra import OutputAdapter`

**Rule**: Never write a new extraction helper. Wrap the result in `OutputAdapter` and call the method that matches what you need.

---

## Pattern 6: RequestContext (Context Threading)

Replace growing parameter lists with a single context object that flows through the pipeline.

```python
# Before — parameters multiply as pipeline grows
async def _ainvoke(self, prompt, source, env, session_id, correlation_id, caller_id, ...):
    ...

# After — one object carries all state
async def _ainvoke(self, prompt: str, ctx: RequestContext) -> dict:
    ctx.set_agent(await self._select_agent(prompt), confidence=0.95)
    await self._check_intake(ctx)
    await self._check_dispatch(ctx)
    ctx.set_result(result)
    return ctx.to_dict()
```

**Key methods**: `set_agent()`, `set_result()`, `add_policy_decision()`, `is_blocked()`, `summary()`

**Import**: `from shared.common.infra import RequestContext`

**Bridging legacy call sites**: use `RequestContext.from_invoke_ctx(legacy_ctx)` to convert an existing `_InvokeCtx` to `RequestContext` without rewriting the caller. Set any extra fields (e.g. `rc.tokens_used`, `rc.result`) before passing to the pipeline.

```python
# Pipeline method now takes ctx: RequestContext — but caller still has _InvokeCtx
rc = RequestContext.from_invoke_ctx(ctx)
rc.tokens_used = tokens_requested
decision = await self._pipeline.check_dispatch(rc)
```

**Rule**: New pipeline functions take `ctx: RequestContext`. Replace positional parameter lists as you touch existing functions — don't add new parameters. Use `from_invoke_ctx` as the bridge at the call site, not as a permanent solution.
