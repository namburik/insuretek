from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from mltek.common.log import setup_structured_logging
from mltek.common.starlette_app import serve_html_no_cache
from mltek.config import config
from bff_service.endpoints import generate_intake_stream, get_intake_message_response
from web_gateway.routes.helpers import extract_json_body, validate_intake_request

_UI_PATH = Path(__file__).parent.parent / "static" / "intake_chat.html"
serve_intake_ui = serve_html_no_cache(_UI_PATH)

logger = setup_structured_logging(__name__)
_cfg = config.get_llm_config_no_think()
logger.info("Intake router ready provider=%s model=%s", config.MODEL_PROVIDER, _cfg.get("model") or _cfg.get("model_id"))


async def intake_stream(request: Request) -> StreamingResponse | JSONResponse:
    """POST /intake/stream — SSE streaming version of intake_message."""
    body, error = await extract_json_body(request)
    if error:
        return error

    message, session_id, correlation_id, caller_id, mode, env, error = validate_intake_request(body)
    if error:
        return error

    logger.info("intake_stream caller=%s session_id=%s correlation_id=%s mode=%s env=%s", caller_id or "—", session_id, correlation_id, mode, env)

    return StreamingResponse(
        generate_intake_stream(message, session_id, mode=mode, env=env, correlation_id=correlation_id, caller_id=caller_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def intake_message(request: Request) -> JSONResponse:
    """POST /intake/message — non-streaming intake response."""
    body, error = await extract_json_body(request)
    if error:
        return error

    message, session_id, correlation_id, caller_id, mode, env, error = validate_intake_request(body)
    if error:
        return error

    logger.info("intake_message caller=%s session_id=%s correlation_id=%s mode=%s env=%s", caller_id or "—", session_id, correlation_id, mode, env)

    try:
        payload = await get_intake_message_response(
            message,
            session_id,
            mode=mode,
            source="react",
            env=env,
            correlation_id=correlation_id,
            caller_id=caller_id,
        )
    except Exception as exc:
        logger.exception("intake_message graph error: %s", exc)
        return JSONResponse({"error": f"Graph error: {exc}"}, status_code=500)

    return JSONResponse(payload)


INTAKE_ROUTES = [
    Route("/intake/stream", intake_stream, methods=["POST"]),
    Route("/intake/message", intake_message, methods=["POST"]),
]
