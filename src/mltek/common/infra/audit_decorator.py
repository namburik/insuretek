"""Decorator and helper for fire-and-forget policy decision persistence."""

import asyncio
import functools
import threading
import time
from typing import Any, Callable, Dict, Optional

from mltek.common.log.logging_config import setup_structured_logging

logger = setup_structured_logging(__name__)

_buffer_lock = threading.Lock()

# Audit persistence callables — injected by init_audit_config() via domain provider.
# write_fn(**kwargs): create and persist a new audit record from raw fields
# emit_fn(record): persist a pre-built record object
# Both are None until init_audit_config() is called; audit is silently skipped when None.
_audit_write_fn: Callable | None = None
_audit_emit_fn: Callable | None = None


def _capture_context() -> tuple[str, str, str, str, str]:
    """Snapshot ContextVars before awaiting to preserve request context."""
    from mltek.common.infra.context import (
        CorrelationContext,
        get_jit_session,
        get_request_id,
        get_audit_session_id,
    )
    correlation_id = CorrelationContext.get() or ""
    phase, _, env = get_jit_session()
    session_id = get_audit_session_id()
    request_id = get_request_id() or None
    return correlation_id, session_id, env, phase, request_id


# Domain-specific audit configuration — initialized by init_audit_config().
# mltek has empty defaults; domain provides values via DomainPolicyProvider.
_AUDIT_AGENTS: frozenset[str] = frozenset()

# Persist llm_judge and reflection as standalone audit records.
# Other allowed _AUDIT_AGENTS controls are buffered into pipeline_summary.
_ALWAYS_AUDIT_TYPES: frozenset[str] = frozenset({"llm_judge", "reflection"})

# Always write directly as a standalone record — never buffer into pipeline_summary.
# Use for cross-cutting infrastructure concerns.
_DIRECT_ONLY_TYPES: frozenset[str] = frozenset()

# Boarding_agent control buffer keyed by correlation_id.
# Each entry: {"controls": list[dict], "phase": str, "env": str} — snapshot captured at buffer time.
_correlation_buffer: dict[str, dict] = {}
_MAX_BUFFER_CONTROLS = 50
_MAX_BUFFER_KEYS = 500

# Domain keywords for intent classification — initialized by init_audit_config().
_DOMAIN_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = ()

# Entity kwarg mapping — initialized by init_audit_config().
_ENTITY_KWARG_MAP: dict[str, str] = {}


def init_audit_config() -> None:
    """Initialize audit configuration from domain provider.

    Call this once at startup after DomainPolicyProvider is registered.
    All domain access is injected via the domain provider — no direct imports.
    """
    global _AUDIT_AGENTS, _DOMAIN_KEYWORDS, _ENTITY_KWARG_MAP
    global _audit_write_fn, _audit_emit_fn
    try:
        from mltek.common.router.resolver import get_domain_provider
        provider = get_domain_provider()
        if provider:
            cfg = provider.get_audit_config()
            _AUDIT_AGENTS = cfg.get("audit_agents", frozenset())
            _DOMAIN_KEYWORDS = cfg.get("domain_keywords", ())
            _ENTITY_KWARG_MAP = cfg.get("entity_kwarg_map", {})
            logger.info("Audit config initialized: agents=%s domains=%d",
                       _AUDIT_AGENTS, len(_DOMAIN_KEYWORDS))
            writer = provider.get_audit_writer()
            if writer is not None:
                _audit_write_fn, _audit_emit_fn = writer
                logger.info("Audit writer injected from domain provider")
            else:
                logger.info("Audit writer not provided — audit persistence disabled")
    except Exception as e:
        logger.warning("Could not load audit config from domain provider: %s", e)


def _buffer_allowed_gate(
    correlation_id: str,
    policy_type: str,
    operation: str,
    reason: str,
    created_at: int,
    phase: str = "",
    env: str = "prod",
) -> None:
    with _buffer_lock:
        while len(_correlation_buffer) >= _MAX_BUFFER_KEYS:
            _correlation_buffer.pop(next(iter(_correlation_buffer)), None)
        if correlation_id not in _correlation_buffer:
            # Snapshot intent at first-buffer time so domain classification in
            # flush_pipeline_summary uses the intent that was active when the
            # pipeline started, not whatever is current at flush time.
            try:
                from mltek.common.infra.context import get_intent
                intent = get_intent() or ""
            except Exception:
                intent = ""
            _correlation_buffer[correlation_id] = {"controls": [], "phase": phase, "env": env, "intent": intent}
        buf = _correlation_buffer[correlation_id]["controls"]
        if len(buf) < _MAX_BUFFER_CONTROLS:
            buf.append({"policy_type": policy_type, "operation": operation, "reason": reason[:80], "created_at": created_at})


def _classify_domain(intent: str, controls: list[dict] | None = None) -> str:
    """Classify underwriting_agent activity into a coarse operational domain."""
    haystack = [intent or ""]
    haystack.extend(c.get("operation", "") for c in (controls or []))
    text = " ".join(haystack).lower()
    for domain, keywords in _DOMAIN_KEYWORDS:
        if any(kw in text for kw in keywords):
            return domain
    return "general"


def flush_pipeline_summary(correlation_id: str) -> None:
    """Write a single pipeline_summary record for buffered allowed controls."""
    with _buffer_lock:
        entry = _correlation_buffer.pop(correlation_id, None)
    if not entry:
        return
    controls   = entry["controls"]
    snap_phase = entry.get("phase") or ""
    snap_env   = entry.get("env") or "prod"
    snap_intent = entry.get("intent") or ""
    if _audit_write_fn is None:
        return
    try:
        from mltek.common.infra.context import (
            get_audit_session_id, get_request_id, get_active_agent,
        )
        domain = _classify_domain(snap_intent, controls)
        policy_names = ", ".join(g["policy_type"] for g in controls)
        earliest_ts = min((c.get("created_at") or 0) for c in controls) or None
        _audit_write_fn(
            policy_type="pipeline_summary",
            allowed=True,
            reason=f"{len(controls)} controls ALLOWED [{domain}]: {policy_names}",
            session_id=get_audit_session_id(),
            correlation_id=correlation_id,
            request_id=get_request_id() or None,
            operation=get_active_agent() or "pipeline_summary",
            env=snap_env,
            phase=snap_phase,
            metadata={"control_count": len(controls), "controls": controls, "intent_domain": domain},
            created_at=earliest_ts,
        )
        logger.info("flush_pipeline_summary exit correlation_id=%.8s domain=%s controls=%d", correlation_id, domain, len(controls))
    except Exception as exc:
        logger.warning("flush_pipeline_summary failed: %s", exc)


def _extract_entity(kwargs: dict, metadata: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Extract entity id/type from kwargs or metadata."""
    for kwarg_name, etype in _ENTITY_KWARG_MAP.items():
        val = kwargs.get(kwarg_name)
        if val and isinstance(val, str) and len(val) > 4:
            return val, etype
    for kwarg_name, etype in _ENTITY_KWARG_MAP.items():
        val = metadata.get(kwarg_name)
        if val and isinstance(val, str) and len(val) > 4:
            return val, etype
    return None, None


async def _persist(
    policy_type: str,
    operation: str,
    allowed: bool,
    reason: str,
    metadata: Dict[str, Any],
    correlation_id: str,
    session_id: str,
    env: str,
    phase: str,
    request_id: Optional[str],
    created_at: int,
    entity_id: Optional[str] = None,
    entity_type: Optional[str] = None,
) -> None:
    """Persist one policy audit record; errors are logged and ignored."""
    try:
        from mltek.common.infra.context import get_active_agent
        active_agent = get_active_agent()
        if allowed and policy_type in _DIRECT_ONLY_TYPES:
            pass  # always write directly regardless of agent
        elif allowed and active_agent not in _AUDIT_AGENTS:
            return  # non-boarding (servicing): only DENIED records persisted
        elif allowed and policy_type not in _ALWAYS_AUDIT_TYPES:
            _buffer_allowed_gate(correlation_id, policy_type, operation, reason, created_at, phase, env)
            return  # boarding allowed: defer to flush_pipeline_summary
        if _audit_write_fn is None:
            return
        _audit_write_fn(
            policy_type=policy_type,
            allowed=allowed,
            reason=reason,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            operation=operation,
            env=env,
            phase=phase,
            metadata=metadata,
            entity_id=entity_id,
            entity_type=entity_type,
            created_at=created_at,
        )
        logger.info("audit persist exit policy_type=%s operation=%s allowed=%s", policy_type, operation, allowed)
    except Exception as exc:
        logger.warning("audit_policy_decision persist failed policy_type=%s: %s", policy_type, exc)


def audit_policy_decision(
    policy_type: str,
    operation_arg: Optional[int] = None,
    operation_kwarg: Optional[str] = None,
) -> Callable:
    """Wrap policy evaluation to emit audit records."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Snapshot context and timestamp before any await.
            correlation_id, session_id, env, phase, request_id = _capture_context()
            created_at = int(time.time() * 1000)

            decision = await func(*args, **kwargs)

            # Extract operation name from args or kwargs, fall back to func name.
            operation = func.__name__
            try:
                if operation_kwarg and operation_kwarg in kwargs:
                    operation = str(kwargs[operation_kwarg])
                elif operation_kwarg:
                    # kwarg may have been passed positionally — skip silently
                    pass
                if operation == func.__name__ and operation_arg is not None:
                    if operation_arg < len(args):
                        operation = str(args[operation_arg])
            except Exception:
                pass

            metadata = dict(decision.metadata) if decision.metadata else {}
            entity_id, entity_type = _extract_entity(kwargs, metadata)

            _persist_kwargs = dict(
                policy_type=policy_type,
                operation=operation,
                allowed=decision.allowed,
                reason=decision.reason,
                metadata=metadata,
                correlation_id=correlation_id,
                session_id=session_id,
                env=env,
                phase=phase,
                request_id=request_id,
                created_at=created_at,
                entity_id=entity_id,
                entity_type=entity_type,
            )
            if policy_type in _ALWAYS_AUDIT_TYPES:
                # Await directly so the write is guaranteed to complete before
                # flush_pipeline_summary runs in the caller's finally block.
                # _persist swallows all errors so this cannot raise.
                await _persist(**_persist_kwargs)
            else:
                try:
                    asyncio.create_task(_persist(**_persist_kwargs))
                except RuntimeError:
                    # No running event loop (e.g. synchronous test context).
                    logger.debug(
                        "audit_policy_decision: no running event loop, skipping persist policy_type=%s",
                        policy_type,
                    )

            logger.info("audit_policy_decision exit policy_type=%s operation=%s allowed=%s", policy_type, operation, decision.allowed)
            return decision

        return wrapper
    return decorator


def audit_inline(
    policy_type: str,
    operation: str,
    allowed: bool,
    reason: str,
    metadata: Optional[Dict[str, Any]] = None,
    entity_id: Optional[str] = None,
    entity_type: Optional[str] = None,
    session_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> None:
    """Persist a policy audit record for non-policy middleware."""
    from mltek.common.infra.context import get_active_agent
    created_at = int(time.time() * 1000)
    if allowed and policy_type not in _ALWAYS_AUDIT_TYPES and policy_type not in _DIRECT_ONLY_TYPES:
        if get_active_agent() not in _AUDIT_AGENTS:
            return  # non-boarding allowed: skip
        ctx_correlation_id, _, env, phase, _ = _capture_context()
        _buffer_allowed_gate(ctx_correlation_id, policy_type, operation, reason, created_at, phase, env)
        return  # boarding allowed: defer to flush_pipeline_summary

    ctx_correlation_id, ctx_session_id, env, phase, request_id = _capture_context()
    correlation_id = correlation_id or ctx_correlation_id
    session_id = session_id or ctx_session_id
    _meta = metadata or {}
    _eid, _etype = entity_id, entity_type
    if not _eid:
        _eid, _etype = _extract_entity({}, _meta)

    async def _task():
        await _persist(
            policy_type=policy_type,
            operation=operation,
            allowed=allowed,
            reason=reason,
            metadata=_meta,
            correlation_id=correlation_id,
            session_id=session_id,
            env=env,
            phase=phase,
            request_id=request_id,
            created_at=created_at,
            entity_id=_eid,
            entity_type=_etype,
        )

    try:
        asyncio.create_task(_task())
    except RuntimeError:
        logger.debug("audit_inline: no running event loop, skipping persist policy_type=%s", policy_type)


def emit_audit_record(record: Any) -> None:
    """Fire-and-forget write of a pre-built audit record; safe to call from sync or async context."""
    if _audit_emit_fn is None:
        return
    async def _task():
        try:
            await asyncio.to_thread(_audit_emit_fn, record)
        except Exception as exc:
            logger.warning("emit_audit_record failed: %s", exc)
    try:
        asyncio.get_running_loop()
        asyncio.create_task(_task())
    except RuntimeError:
        try:
            _audit_emit_fn(record)
        except Exception as exc:
            logger.warning("emit_audit_record failed: %s", exc)
