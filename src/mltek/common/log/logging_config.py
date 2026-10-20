"""Structured logging configuration for mltek framework."""

import logging
import json
from datetime import datetime
from mltek.config.config_common import Config


def setup_logging(name: str = None) -> logging.Logger:
    """Configure logging for agent demos, suppressing noisy library loggers."""
    logging.basicConfig(
        level=Config.LOG_LEVEL,
        format="%(asctime)s - %(name)s - %(funcName)s - %(levelname)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("mcp.client.streamable_http").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logger = logging.getLogger(name or __name__)
    logger.setLevel(Config.LOG_LEVEL)
    return logger


class StructuredJsonFormatter(logging.Formatter):
    """JSON formatter that outputs structured logs for easy parsing and querying."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON.
        
        Args:
            record: LogRecord to format
            
        Returns:
            JSON string with all relevant log information
        """
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "function": record.funcName,
            "message": record.getMessage(),
            "line": record.lineno,
        }
        
        # Add correlation ID if available
        if hasattr(record, 'correlation_id'):
            log_data["correlation_id"] = record.correlation_id

        # Add node/app server metadata if available
        if hasattr(record, 'node'):
            log_data["node"] = record.node
        if hasattr(record, 'app_server'):
            log_data["app_server"] = record.app_server
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": self.formatException(record.exc_info),
            }
        
        # Add extra fields if present (custom context)
        if hasattr(record, "extra_fields"):
            log_data["context"] = record.extra_fields
        
        return json.dumps(log_data)


class StructuredLogger(logging.LoggerAdapter):
    """Adapter for adding structured context to logs.
    
    Usage:
        logger = StructuredLogger(base_logger, {"request_id": "123"})
        logger.info("Operation started", extra_fields={"duration_ms": 45})
    """

    def process(self, msg: str, kwargs):
        """Process log message and add structured context."""
        extra_fields = kwargs.pop("extra_fields", {})
        if extra_fields:
            self.logger.extra_fields = extra_fields
        return msg, kwargs


def setup_structured_logging(name_or_level: str = None, log_level: str = Config.LOG_LEVEL) -> StructuredLogger:
    """Configure structured logging with JSON formatter.

    Args:
        name_or_level: If this argument matches a valid logging level name, it is
            treated as the log level and the logger name defaults to get_app_name().
            Otherwise it is used as the logger name.
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).

    Returns:
        Configured StructuredLogger instance that supports extra_fields
    """
    from mltek.common.infra.context import get_app_name  # lazy — avoids circular import at module load
    upper = name_or_level.upper() if isinstance(name_or_level, str) else ""
    if upper in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}:
        logger_name = get_app_name()
        level_name = upper
    else:
        logger_name = name_or_level or get_app_name()
        level_name = log_level.upper()

    logger = logging.getLogger(logger_name)
    logger.setLevel(getattr(logging, level_name))

    # Clear existing handlers
    logger.handlers.clear()

    # Create console handler with JSON formatter
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredJsonFormatter())

    # Add correlation filter to inject correlation_id into all records
    from mltek.common.infra import CorrelationFilter  # lazy — avoids circular import at module load
    handler.addFilter(CorrelationFilter())

    logger.addHandler(handler)

    return StructuredLogger(logger, {})
