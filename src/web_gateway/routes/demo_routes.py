"""Demo routes — prospect gating + sandboxed onboarding embed for marketing.

Flow:
  1. POST /demo/gate     — prospect submits email/name/company; receives demo_token
  2. POST /demo/intake/stream   — gated SSE streaming onboarding wizard
  3. POST /demo/intake/message  — gated non-streaming onboarding wizard

demo_token is an in-memory UUID valid for 24 hours. Session IDs are prefixed
with "demo_" to isolate them from production sessions in the shared store.
"""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from shared.common.infra.context import CorrelationContext
from shared.common.log import setup_structured_logging
from shared.common.starlette_app import serve_html_no_cache
from shared.config import config
from core_data.repositories.login_activity_repository import LoginActivityRepository
from bff_service.demo_endpoints import generate_demo_stream, get_demo_message_response
from web_gateway.routes.helpers import extract_json_body, get_client_info

_TRACKING_ENABLED = not bool(config.DYNAMODB_ENDPOINT)
_login_repo: LoginActivityRepository | None = None

def _get_login_repo() -> LoginActivityRepository:
    global _login_repo
    if _login_repo is None:
        _login_repo = LoginActivityRepository()
    return _login_repo

_DEMO_HTML = Path(__file__).parent.parent / "static" / "demo.html"
serve_demo_ui = serve_html_no_cache(_DEMO_HTML)

logger = setup_structured_logging(__name__)

# In-memory prospect store: token -> prospect dict
_DEMO_TOKENS: dict[str, dict] = {}
_TOKEN_TTL_HOURS = 24


def _validate_demo_token(token: str | None) -> bool:
    if not token or token not in _DEMO_TOKENS:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_TOKEN_TTL_HOURS)
    if _DEMO_TOKENS[token]["created_at"] < cutoff:
        _DEMO_TOKENS.pop(token, None)
        return False
    return True


async def demo_gate(request: Request) -> JSONResponse:
    """POST /demo/gate — capture prospect info and return a short-lived demo_token."""
    body, error = await extract_json_body(request)
    if error:
        return error

    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return JSONResponse({"error": "valid email is required"}, status_code=400)

    name = (body.get("name") or "").strip()
    company = (body.get("company") or "").strip()
    referrer = (body.get("referrer") or "").strip()
    ip, user_agent = get_client_info(request)

    token = f"demo_{uuid.uuid4().hex}"
    _DEMO_TOKENS[token] = {
        "email": email,
        "name": name,
        "company": company,
        "referrer": referrer,
        "ip": ip,
        "user_agent": user_agent,
        "created_at": datetime.now(timezone.utc),
    }
    logger.info("demo_gate prospect registered email=%s company=%s referrer=%s", email, company, referrer)
    if _TRACKING_ENABLED:
        identity = f"{name} <{email}>" if name else email
        _get_login_repo().record(provider="demo", identity=identity, ip=ip, user_agent=user_agent)
    return JSONResponse({"demo_token": token, "ok": True})


def _demo_session_id(raw: str | None) -> str:
    sid = raw or CorrelationContext.generate()
    return sid if sid.startswith("demo_") else f"demo_{sid}"


def _parse_demo_request(body: dict) -> tuple[str, str, str, JSONResponse | None]:
    """Validate demo token and message; return (token, message, session_id, error_response)."""
    demo_token = (body.get("demo_token") or "").strip()
    if not _validate_demo_token(demo_token):
        return "", "", "", JSONResponse(
            {"error": "valid demo_token required — submit your details at /demo/gate first"},
            status_code=401,
        )
    message = (body.get("message") or "").strip()
    if not message:
        return "", "", "", JSONResponse({"error": "message is required"}, status_code=400)
    return demo_token, message, _demo_session_id(body.get("session_id")), None


async def demo_stream(request: Request) -> StreamingResponse | JSONResponse:
    """POST /demo/intake/stream — gated SSE streaming demo of the onboarding wizard."""
    body, error = await extract_json_body(request)
    if error:
        return error

    demo_token, message, session_id, error = _parse_demo_request(body)
    if error:
        return error

    logger.info("demo_stream demo_token=%s session_id=%s", demo_token, session_id)

    return StreamingResponse(
        generate_demo_stream(message, session_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def demo_message(request: Request) -> JSONResponse:
    """POST /demo/intake/message — gated non-streaming demo of the onboarding wizard."""
    body, error = await extract_json_body(request)
    if error:
        return error

    demo_token, message, session_id, error = _parse_demo_request(body)
    if error:
        return error

    logger.info("demo_message demo_token=%s session_id=%s", demo_token, session_id)

    try:
        payload = await get_demo_message_response(message, session_id)
    except Exception as exc:
        logger.exception("demo_message error: %s", exc)
        return JSONResponse({"error": f"Demo error: {exc}"}, status_code=500)

    return JSONResponse(payload)


DEMO_ROUTES = [
    Route("/demo", serve_demo_ui, methods=["GET"]),
    Route("/demo/gate", demo_gate, methods=["POST"]),
    Route("/demo/intake/stream", demo_stream, methods=["POST"]),
    Route("/demo/intake/message", demo_message, methods=["POST"]),
]
