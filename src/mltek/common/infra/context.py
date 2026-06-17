"""Request correlation context for tracing requests across async call chains.

Provides thread-safe, async-safe context variables for tracking requests
through the entire system - from UI to agents to services to database.

Usage:
    from common.context import CorrelationContext
    
    # Generate and set correlation ID
    request_id = CorrelationContext.generate()
    CorrelationContext.set(request_id)
    
    # Downstream code automatically inherits the same ID
    # (no need to pass as parameter)
    correlation_id = CorrelationContext.get()
    
    # Clean up after request
    CorrelationContext.clear()
"""

import contextvars
import uuid
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Thread-safe, async-safe context variable
# Works correctly with asyncio, threading, and multiprocessing
_correlation_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'correlation_id',
    default=None
)


class CorrelationContext:
    """Manage request correlation ID across async call chains.
    
    Correlation ID is a unique identifier that follows a request through
    the entire system:
    
    User Request
        ↓
    UI generates correlation_id (ABC-123)
        ↓
    Agent functions (inherit via context)
        ↓
    Service methods (inherit via context)
        ↓
    Repository queries (inherit via context)
        ↓
    Database (logged in trace)
    
    Benefits:
    - Find all logs for a single request: grep "ABC-123"
    - Correlate LangSmith traces by correlation_id
    - Support team can trace user request through entire stack
    - Audit trail for compliance/debugging
    """
    
    @staticmethod
    def generate() -> str:
        """Generate new correlation ID.

        Returns 8-character hex string (e.g., "a3f2c1b9").
        Use this at the start of each new request.
        """
        return uuid.uuid4().hex[:16]
    
    @staticmethod
    def set(correlation_id: str) -> None:
        """Set correlation ID for current async context.
        
        All downstream async functions will inherit this ID.
        
        Args:
            correlation_id: Unique request identifier (e.g., "abc-123")
        """
        if not correlation_id:
            logger.warning("Attempted to set empty correlation_id")
            return
        
        _correlation_id.set(correlation_id)
        logger.debug(f"Correlation context set: {correlation_id}")
    
    @staticmethod
    def get() -> Optional[str]:
        return _correlation_id.get()
    
    @staticmethod
    def get_or_generate() -> str:
        """Get current correlation ID, or generate new one if not set.

        Returns:
            Current correlation ID, or newly generated one.
        """
        current = _correlation_id.get()
        if current:
            return current

        new_id = CorrelationContext.generate()
        _correlation_id.set(new_id)
        logger.warning("correlation_id not set in context; generated fallback id=%s", new_id)
        return new_id
    
    @staticmethod
    def clear() -> None:
        """Clear correlation ID after request complete.
        
        Call this in a finally block to ensure cleanup.
        """
        _correlation_id.set(None)
        logger.debug("Correlation context cleared")
    
    @staticmethod
    def copy_context():
        """Get current context copy for spawning new async tasks.
        
        Useful when spawning background tasks that should inherit
        the correlation ID.
        
        Returns:
            contextvars.Context object (for asyncio.create_task)
        """
        return contextvars.copy_context()


# ── system name (set once at startup by the domain system) ─────────────────
_app_name: str = "mltek"


def set_app_name(name: str) -> None:
    """Set the system name used in structured log output.

    Call once at domain system startup (e.g. `set_app_name("insuretek")`).
    """
    global _app_name
    _app_name = name or "mltek"


def get_app_name() -> str:
    """Return the system name configured at startup."""
    return _app_name


# ── Environment context (shadow vs prod) ──────────────────────────────────────
_env_var: contextvars.ContextVar[str] = contextvars.ContextVar("env", default="prod")


def set_env(env: str) -> contextvars.Token:
    """Set the active environment for the current async/thread context."""
    return _env_var.set(env if env in ("shadow", "prod") else "prod")


def get_env() -> str:
    """Return the active environment ('prod' or 'shadow')."""
    return _env_var.get()


# ── Active agent context ──────────────────────────────────────────────────────
_active_agent: contextvars.ContextVar[str] = contextvars.ContextVar("active_agent", default="")


def set_active_agent(agent_id: str) -> None:
    """Set the resolved agent for the current request. Used to gate audit writes."""
    _active_agent.set(agent_id or "")


def get_active_agent() -> str:
    """Return the resolved agent ID, or empty string before routing."""
    return _active_agent.get()


# ── Guardian clearance context ─────────────────────────────────────────────────
_guardian_cleared: contextvars.ContextVar[bool] = contextvars.ContextVar("guardian_cleared", default=False)


def set_guardian_cleared(cleared: bool) -> None:
    _guardian_cleared.set(cleared)


def get_guardian_cleared() -> bool:
    return _guardian_cleared.get()


# ── Workflow request ID context ───────────────────────────────────────────────
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")


def set_request_id(request_id: str | None) -> None:
    """Set the workflow request ID for the current async context."""
    _request_id.set(request_id or "")


def get_request_id() -> str:
    """Return the workflow request ID, or empty string if not yet assigned."""
    return _request_id.get()


# ── JIT session context ────────────────────────────────────────────────────────
_JIT_VALID_PHASES = frozenset({"collecting", "offering_optional", "confirming", "done"})

_jit_session_phase: contextvars.ContextVar[str] = contextvars.ContextVar(
    "jit_session_phase", default="collecting"
)
_jit_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "jit_session_id", default=""
)


def set_jit_session(phase: str, session_id: str) -> None:
    """Set JIT session context before resolver invocation."""
    _jit_session_phase.set(phase if phase in _JIT_VALID_PHASES else "collecting")
    _jit_session_id.set(session_id or "")


def get_jit_session() -> tuple[str, str, str]:
    """Return (phase, session_id, env). env is read from the shared _env_var."""
    return _jit_session_phase.get(), _jit_session_id.get(), _env_var.get()


# ── Token phase context ───────────────────────────────────────────────────────
_token_phase: contextvars.ContextVar[str] = contextvars.ContextVar("token_phase", default="unknown")

# _parent_task_id propagates INTO child tasks (LangChain callback tasks created via
# asyncio.gather). Callbacks read it to find the right bucket in _task_tokens and
# write there — the parent's own dict entry — bypassing the ContextVar write-back barrier.
_parent_task_id: contextvars.ContextVar[int] = contextvars.ContextVar("_parent_task_id", default=0)
_task_tokens: dict[int, int] = {}


def get_token_phase() -> str:
    return _token_phase.get()


def get_request_tokens() -> int:
    """Return accumulated LLM tokens for the current request task."""
    import asyncio
    task = asyncio.current_task()
    return _task_tokens.get(id(task), 0) if task else 0


def add_request_tokens(n: int) -> None:
    """Add n to the parent task's token bucket. Called from callback child tasks."""
    parent_id = _parent_task_id.get()
    if parent_id:
        _task_tokens[parent_id] = _task_tokens.get(parent_id, 0) + n


def with_token_phase(phase: str):
    """Decorator: sets the token phase and logs tokens consumed by the wrapped call."""
    import asyncio, functools, inspect
    def decorator(fn):
        def _setup_phase():
            _token_phase.set(phase or "unknown")
            task = asyncio.current_task()
            task_id = id(task) if task else 0
            _parent_task_id.set(task_id)
            if task_id and task_id not in _task_tokens:
                _task_tokens[task_id] = 0
                if task:
                    task.add_done_callback(lambda t: _task_tokens.pop(id(t), None))
            return task_id

        if inspect.isasyncgenfunction(fn):
            @functools.wraps(fn)
            async def gen_wrapper(*args, **kwargs):
                task_id = _setup_phase()
                before = _task_tokens.get(task_id, 0)
                from mltek.common.log import setup_structured_logging as _slog
                _logger = _slog(__name__)
                async for item in fn(*args, **kwargs):
                    yield item
                consumed = _task_tokens.get(task_id, 0) - before
                agent = _active_agent.get()
                _logger.info("token_phase=%s fn=%s agent=%s consumed=%d", phase, fn.__qualname__, agent or "—", consumed)
            return gen_wrapper

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            task_id = _setup_phase()
            before = _task_tokens.get(task_id, 0)
            from mltek.common.log import setup_structured_logging as _slog
            _logger = _slog(__name__)
            result = await fn(*args, **kwargs)
            consumed = _task_tokens.get(task_id, 0) - before
            agent = _active_agent.get()
            _logger.info("token_phase=%s fn=%s agent=%s consumed=%d", phase, fn.__qualname__, agent or "—", consumed)
            return result
        return wrapper
    return decorator


# ── User intent context ───────────────────────────────────────────────────────
_user_intent: contextvars.ContextVar[str] = contextvars.ContextVar("user_intent", default="")


def set_intent(intent: str) -> None:
    """Set the user's intent for the current agent run. Called by middleware before tool scoring."""
    _user_intent.set(intent or "")


def get_intent() -> str:
    """Return the user's intent, or empty string if not yet captured."""
    return _user_intent.get()


# ── Audit session context (survives set_jit_session overrides) ────────────────
_audit_session_id: contextvars.ContextVar[str] = contextvars.ContextVar("audit_session_id", default="")


def set_audit_session_id(session_id: str) -> None:
    """Pin the session_id used by audit writes. Not overridden by set_jit_session."""
    _audit_session_id.set(session_id or "")


def get_audit_session_id() -> str:
    """Return the pinned audit session_id, falling back to the JIT session_id."""
    pinned = _audit_session_id.get()
    if pinned:
        return pinned
    _, jit_session, _ = get_jit_session()
    return jit_session


class CorrelationFilter(logging.Filter):
    """Logging filter that injects correlation ID into all log records.
    
    Usage in logging config:
        handler.addFilter(CorrelationFilter())
        
    Then use in formatter:
        formatter = logging.Formatter(
            "[%(correlation_id)s] %(name)s - %(message)s"
        )
    """
    
    def filter(self, record: logging.LogRecord) -> bool:
        """Add correlation_id to log record.
        
        Args:
            record: Log record to augment
            
        Returns:
            Always True (don't skip records)
        """
        record.correlation_id = CorrelationContext.get_or_generate()
        return True


def attach_correlation_to_trace(correlation_id: str) -> None:
    try:
        from langsmith.run_helpers import get_current_run_tree
        run = get_current_run_tree()
        if run:
            run.extra = run.extra or {}
            run.extra["correlation_id"] = correlation_id
    except (ImportError, AttributeError):
        pass


class CorrelationFormatter(logging.Formatter):
    """Logging formatter that includes correlation ID in output.
    
    Usage:
        formatter = CorrelationFormatter(
            fmt="[%(correlation_id)s] %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)
    """
    
    def __init__(self, fmt=None, datefmt=None, style='%'):
        """Initialize formatter with correlation ID support.
        
        Args:
            fmt: Format string (should include %(correlation_id)s)
            datefmt: Date format
            style: Format style ('$' or '%', default '%')
        """
        # Default format includes correlation ID
        if fmt is None:
            fmt = "[%(correlation_id)s] %(asctime)s - %(name)s - %(levelname)s - %(message)s"
        
        super().__init__(fmt=fmt, datefmt=datefmt, style=style)
    
    def format(self, record: logging.LogRecord) -> str:
        """Format record with correlation ID.
        
        Args:
            record: Log record to format
            
        Returns:
            Formatted log message
        """
        # Ensure correlation_id is set on record
        if not hasattr(record, 'correlation_id'):
            record.correlation_id = CorrelationContext.get_or_generate()
        
        return super().format(record)
