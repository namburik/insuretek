"""Infrastructure layer exports for logging, context, and health checking."""

from .context import CorrelationContext, CorrelationFilter, attach_correlation_to_trace, set_guardian_cleared, get_guardian_cleared, set_intent, get_intent
from .config_loader import load_config
from .audit_decorator import audit_policy_decision, audit_inline
from .output_adapter import OutputAdapter
from .request_context import RequestContext
from .setup import (
    extract_result_content,
    extract_tool_message_content,
    extract_json_from_text,
    extract_json_object,
    repair_truncated_json,
    extract_state_value,
    get_last_message_content,
    get_last_human_message_content,
)

# Health checking
from .health_check import (
    HealthChecker,
    HealthStatus,
    ServiceHealth,
    check_all_services,
    ensure_services_ready,
    print_health_report,
)

__all__ = [
    # Config loading
    "load_config",
    # Logging
    "setup_logging",
    "extract_result_content",
    "extract_tool_message_content",
    "extract_json_from_text",
    "extract_json_object",
    "repair_truncated_json",
    "extract_state_value",
    "get_last_message_content",
    "get_last_human_message_content",
    # Context and threading
    "CorrelationContext",
    "CorrelationFilter",
    "attach_correlation_to_trace",
    "set_guardian_cleared",
    "get_guardian_cleared",
    "set_intent",
    "get_intent",
    "RequestContext",
    "OutputAdapter",
    # Health
    "HealthChecker",
    "HealthStatus",
    "ServiceHealth",
    "check_all_services",
    "ensure_services_ready",
    "print_health_report",
]
