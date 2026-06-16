import asyncio
import functools

from shared.common.log.logging_config import setup_structured_logging
from shared.common.infra.exceptions import DatabaseException

_log = setup_structured_logging(__name__)


def best_effort(_func=None, *, message: str | None = None, log_level: str = "warning"):
    """Decorator: runs the function, logs and suppresses any exception.

    Use for optional/idempotent operations where failure should not propagate
    (e.g. DB index provisioning, cache warm-up, audit flushes).

    Usage:
        @best_effort
        @best_effort(message="Could not provision indexes")
        @best_effort(message="...", log_level="error")
    """
    def decorator(func):
        msg = message or f"Could not complete {func.__name__.replace('_', ' ')}"
        _logger = setup_structured_logging(func.__module__)
        _log_fn = getattr(_logger, log_level, _logger.warning)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                _log_fn("%s: %s", msg, exc)

        return wrapper

    if _func is not None:
        return decorator(_func)
    return decorator


def handle_mcp_tool_errors(func):
    """Decorator for MCP call_tool handlers.

    - ValueError (not found, validation): returned as a TextContent result so
      the LLM can reason about the failure instead of seeing a tool error.
    - All other exceptions: re-raised as ValueError with a generic message.
    """
    from mcp.types import TextContent  # local import to avoid circular deps

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        tool_name = args[0] if args else getattr(func, "__name__", "unknown")
        try:
            return await func(*args, **kwargs)
        except ValueError as e:
            _log.warning("Tool '%s' client error: %s", tool_name, e)
            return [TextContent(type="text", text=str(e))]
        except Exception as e:
            _log.error("Tool '%s' unexpected error: %s", tool_name, e, exc_info=True)
            raise ValueError(f"Error processing {tool_name}: {e}") from e

    return wrapper


def handle_db_errors(_func=None, *, operation: str | None = None):
    """Decorator for DB layer methods.

    Logs the exception (with traceback) then re-raises the original exception
    unchanged so repository callers can catch the right type.

    Usage:
        @handle_db_errors
        @handle_db_errors(operation="scan")
    """
    def decorator(func):
        op = operation or func.__name__.replace("_", " ")
        _logger = setup_structured_logging(func.__module__)

        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    table = getattr(args[0], "table_name", "") if args else ""
                    suffix = f" {table}" if table else ""
                    _logger.error(f"Failed to {op}{suffix}: {exc}", exc_info=True)
                    raise
        else:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    table = getattr(args[0], "table_name", "") if args else ""
                    suffix = f" {table}" if table else ""
                    _logger.error(f"Failed to {op}{suffix}: {exc}", exc_info=True)
                    raise

        return wrapper

    if _func is not None:
        return decorator(_func)
    return decorator


def handle_repo_errors(_func=None, *, operation: str | None = None):
    """Decorator for repository methods.

    Logs and wraps unexpected exceptions in DatabaseException.
    DatabaseException and ValueError propagate unchanged.

    Usage:
        @handle_repo_errors
        @handle_repo_errors(operation="list applicants")
    """
    def decorator(func):
        op = operation or func.__name__.replace("_", " ")
        _logger = setup_structured_logging(func.__module__)

        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def wrapper(self, *args, **kwargs):
                try:
                    return await func(self, *args, **kwargs)
                except (DatabaseException, ValueError):
                    raise
                except Exception as exc:
                    _logger.error(f"Failed to {op}: {exc}")
                    raise DatabaseException(str(exc)) from exc
        else:
            @functools.wraps(func)
            def wrapper(self, *args, **kwargs):
                try:
                    return func(self, *args, **kwargs)
                except (DatabaseException, ValueError):
                    raise
                except Exception as exc:
                    _logger.error(f"Failed to {op}: {exc}")
                    raise DatabaseException(str(exc)) from exc

        return wrapper

    if _func is not None:
        return decorator(_func)
    return decorator


def handle_tool_errors(_func=None, *, failure_message: str | None = None):
    """Decorator for handler tool methods.

    Logs the underlying exception and re-raises it as a ValueError with
    a consistent "Failed to ..." message.

    Usage:
        @handle_tool_errors
        @handle_tool_errors("Failed to save applicant")
        @handle_tool_errors(failure_message="Failed to save applicant")

    ValueError raised intentionally inside the method propagates unchanged.
    """
    if isinstance(_func, str):
        failure_message = _func
        _func = None

    def decorator(func):
        tool_name = func.__name__
        default_message = failure_message or f"Failed to {tool_name.replace('_', ' ')}"
        _logger = setup_structured_logging(func.__module__)

        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                try:
                    return await func(*args, **kwargs)
                except ValueError:
                    raise
                except Exception as exc:
                    _logger.error(f"Error in {tool_name}: {exc}", exc_info=True)
                    raise ValueError(f"{default_message}: {exc}") from exc
        else:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                try:
                    return func(*args, **kwargs)
                except ValueError:
                    raise
                except Exception as exc:
                    _logger.error(f"Error in {tool_name}: {exc}", exc_info=True)
                    raise ValueError(f"{default_message}: {exc}") from exc

        return wrapper

    if _func is not None:
        return decorator(_func)
    return decorator
