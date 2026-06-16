"""Common helpers for web gateway routes."""

from starlette.requests import Request
from starlette.responses import JSONResponse

from shared.common.infra.context import CorrelationContext
from shared.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)


async def extract_json_body(request: Request) -> tuple[dict, JSONResponse | None]:
    """Extract and parse JSON from request. Returns (body, error_response)."""
    try:
        body = await request.json()
    except Exception:
        return {}, JSONResponse({"error": "invalid JSON body"}, status_code=400)

    return body, None


def validate_intake_request(body: dict) -> tuple[str, str, str, str, str, str, JSONResponse | None]:
    """
    Validate intake request and extract fields.
    Returns (message, session_id, correlation_id, caller_id, mode, env, error_response).
    """
    # Extract fields
    correlation_id = body.get("correlation_id") or CorrelationContext.get() or CorrelationContext.generate()
    CorrelationContext.set(correlation_id)
    caller_id = "portal" if body.get("X-Auth-Provider") == "portal" else ""
    mode = (body.get("mode", "onboarding") or "onboarding").strip().lower()
    env = (body.get("env", "prod") or "prod").strip().lower()
    message = (body.get("message") or "").strip()
    session_id = body.get("session_id")

    # Validation
    if mode not in {"onboarding", "servicing"}:
        return "", "", "", "", "", "", JSONResponse({"error": f"Invalid mode '{mode}'."}, status_code=400)

    if env not in {"prod", "shadow"}:
        return "", "", "", "", "", "", JSONResponse({"error": f"Invalid env '{env}'."}, status_code=400)

    if not message:
        return "", "", "", "", "", "", JSONResponse({"error": "message is required"}, status_code=400)

    if not session_id:
        logger.warning("Missing session_id; generating new session")
        session_id = CorrelationContext.generate()

    return message, session_id, correlation_id, caller_id, mode, env, None


def get_client_info(request: Request) -> tuple[str | None, str | None]:
    """Extract client IP and user agent from request. Returns (ip, user_agent)."""
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return ip, user_agent
