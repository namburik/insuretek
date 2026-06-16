from pathlib import Path

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from shared.common.log import setup_structured_logging
from shared.common.starlette_app import serve_html_no_cache
from shared.config import config
from core_data.repositories.login_activity_repository import LoginActivityRepository
from core_data.repositories.portal_access_repository import PortalAccessRepository
from web_gateway.routes.helpers import extract_json_body, get_client_info

logger = setup_structured_logging(__name__)

# Tracking is skipped when DYNAMODB_ENDPOINT is set (local dev with local DynamoDB).
_TRACKING_ENABLED = not bool(config.DYNAMODB_ENDPOINT)

_login_repo: LoginActivityRepository | None = None
_portal_repo: PortalAccessRepository | None = None


def _get_login_repo() -> LoginActivityRepository:
    global _login_repo
    if _login_repo is None:
        _login_repo = LoginActivityRepository()
    return _login_repo


def _get_portal_repo() -> PortalAccessRepository:
    global _portal_repo
    if _portal_repo is None:
        _portal_repo = PortalAccessRepository()
    return _portal_repo


def _record_login(request: Request, provider: str, identity: str) -> None:
    if _TRACKING_ENABLED:
        _get_login_repo().record(
            provider=provider,
            identity=identity,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

_CALLBACK_HTML = Path(__file__).parent.parent / "static" / "auth" / "callback.html"

serve_auth_callback = serve_html_no_cache(_CALLBACK_HTML)

GITHUB_CLIENT_ID = config.GITHUB_CLIENT_ID
GITHUB_CLIENT_SECRET = config.GITHUB_CLIENT_SECRET
LINKEDIN_CLIENT_ID = config.LINKEDIN_CLIENT_ID
LINKEDIN_CLIENT_SECRET = config.LINKEDIN_CLIENT_SECRET


async def auth_config(request: Request) -> JSONResponse:
    return JSONResponse({
        "github_enabled": bool(GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET),
        "github_client_id": GITHUB_CLIENT_ID,
        "linkedin_enabled": bool(LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET),
        "linkedin_client_id": LINKEDIN_CLIENT_ID,
    })


async def github_exchange_token(request: Request) -> JSONResponse:
    body, error = await extract_json_body(request)
    if error:
        return error

    code = body.get("code")
    if not code:
        return JSONResponse({"error": "No code provided"}, status_code=400)
    if not GITHUB_CLIENT_SECRET:
        return JSONResponse({"error": "Server misconfiguration: GITHUB_CLIENT_SECRET not set"}, status_code=500)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            token_resp = await client.post(
                "https://github.com/login/oauth/access_token",
                json={"client_id": GITHUB_CLIENT_ID, "client_secret": GITHUB_CLIENT_SECRET, "code": code},
                headers={"Accept": "application/json"},
            )
            token_data = token_resp.json()
            if "error" in token_data:
                return JSONResponse(
                    {"error": token_data.get("error_description", token_data["error"])}, status_code=400
                )

            access_token = token_data["access_token"]
            user_resp = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            user = user_resp.json()
    except Exception as exc:
        logger.exception("GitHub OAuth error: %s", exc)
        return JSONResponse({"error": "Authentication failed", "details": str(exc)}, status_code=500)

    _record_login(request, "github", user.get("login") or user.get("email") or "unknown")
    return JSONResponse({
        "login": user.get("login"),
        "email": user.get("email"),
        "avatar_url": user.get("avatar_url"),
        "token": access_token,
    })


async def linkedin_exchange_token(request: Request) -> JSONResponse:
    body, error = await extract_json_body(request)
    if error:
        return error

    code = body.get("code")
    redirect_uri = body.get("redirect_uri")
    if not code:
        return JSONResponse({"error": "No code provided"}, status_code=400)
    if not LINKEDIN_CLIENT_SECRET:
        return JSONResponse({"error": "Server misconfiguration: LINKEDIN_CLIENT_SECRET not set"}, status_code=500)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            token_resp = await client.post(
                "https://www.linkedin.com/oauth/v2/accessToken",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": LINKEDIN_CLIENT_ID,
                    "client_secret": LINKEDIN_CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            token_data = token_resp.json()
            if "error" in token_data:
                return JSONResponse(
                    {"error": token_data.get("error_description", token_data["error"])}, status_code=400
                )

            access_token = token_data["access_token"]
            user_resp = await client.get(
                "https://api.linkedin.com/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            user = user_resp.json()
    except Exception as exc:
        logger.exception("LinkedIn OAuth error: %s", exc)
        return JSONResponse({"error": "Authentication failed", "details": str(exc)}, status_code=500)

    _record_login(request, "linkedin", user.get("email") or "unknown")
    return JSONResponse({
        "email": user.get("email"),
        "firstName": user.get("given_name"),
        "lastName": user.get("family_name"),
        "token": access_token,
    })


async def check_ref(request: Request) -> JSONResponse:
    """Decode hex ref, check against portal_access allowlist, and record login activity."""
    hex_ref = request.query_params.get("ref", "").strip()
    if not hex_ref:
        return JSONResponse({"allowed": False, "reason": "no ref"}, status_code=400)
    try:
        email = bytes.fromhex(hex_ref).decode()
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"allowed": False, "reason": "invalid ref"}, status_code=400)
    allowed = _get_portal_repo().is_allowed(email)
    if allowed:
        _record_login(request, "portal", email)
    return JSONResponse({"allowed": allowed, "email": email if allowed else None})


async def track_visit(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    referrer = body.get("referrer") or request.headers.get("referer") or "direct"
    _record_login(request, "portfolio", referrer)
    return JSONResponse({"ok": True})


AUTH_ROUTES = [
    Route("/auth/callback", serve_auth_callback, methods=["GET"]),
    Route("/api/auth/config", auth_config, methods=["GET"]),
    Route("/api/auth/exchange-token", github_exchange_token, methods=["POST"]),
    Route("/api/auth/linkedin-exchange-token", linkedin_exchange_token, methods=["POST"]),
    Route("/api/auth/check-ref", check_ref, methods=["GET"]),
    Route("/api/auth/track-visit", track_visit, methods=["POST"]),
]
