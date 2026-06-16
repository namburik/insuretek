import json as _json
from functools import wraps
from typing import AsyncIterator

from shared.common.router.resolver import call_resolver_safe, record_stream_tokens
from shared.common.infra import extract_result_content, extract_tool_message_content
from mcp_server.utilities.constants import TOOL_DESCRIPTIONS

_WORKFLOW_TOOL_NAMES: set[str] = set(TOOL_DESCRIPTIONS.keys())
from shared.common.infra.context import CorrelationContext, set_jit_session, set_request_id
from shared.common.log import setup_structured_logging
from shared.common.tools.format import normalize_tool_response_text
from shared.common.http_client import poll_applicant_id, submit_workflow_request
from shared.config import config
from bff_service.prompts import build_create_applicant_prompt
from bff_service.schema import IntakeSession
from bff_service.session import get_or_create_session, process_turn, stream_reply
from bff_service.prompt_validation_policy import PromptValidationPolicy

logger = setup_structured_logging(__name__)

_intake_policy = PromptValidationPolicy()


async def _refresh_session_applicant_id(session: IntakeSession) -> None:
    if not session.request_id or session.applicant_id:
        return
    applicant_id = await poll_applicant_id(session.request_id)
    if applicant_id:
        session.applicant_id = applicant_id


async def _process_and_submit(session: IntakeSession, message: str, env: str = "prod") -> tuple[str | None, str | None]:
    """Advance the session and POST to workflow if the draft is confirmed.

    Returns (reply, request_id). Returns (None, None) when not yet confirmed —
    caller should stream the conversational reply instead.
    """
    logger.info(
        "Processing intake submit phase=%s has_request_id=%s",
        session.phase,
        bool(session.request_id),
    )
    if session.request_id or session.applicant_id:
        rid = session.request_id or "—"
        aid = session.applicant_id or "—"
        return (
            f"This applicant's underwriting intake has already been submitted.\n\n"
            f"Applicant ID: `{aid}`\n"
            f"Request ID: `{rid}`\n"
            f"Use Servicing → Track Status → By Request ID to follow progress.",
            session.request_id,
        )

    # Phase "done" with no request_id means a previous submission failed; retry.
    confirmed = True if (session.phase == "done" and not session.request_id) else await process_turn(session, message)

    if not confirmed:
        return None, None

    request_id = None
    try:
        from shared.common.infra.context import get_jit_session as _get_jit
        _, _sid, _ = _get_jit()
        request_id, applicant_id = await submit_workflow_request(
            session.draft, build_create_applicant_prompt(session.draft), env=env, session_id=_sid, correlation_id=CorrelationContext.get_or_generate()
        )
        session.request_id = request_id
        if applicant_id:
            session.applicant_id = applicant_id
        reply = (
            f"Insurance underwriting submitted!\n\n"
            f"Request ID: `{request_id}`\n"
            f"Use Servicing → Track Status → By Request ID to follow progress."
        )
    except Exception as exc:
        logger.warning("submit_workflow_request failed: %s", exc)
        reply = f"Insurance workflow failed: {exc}"

    return reply, request_id


async def generate_intake_stream(message: str, session_id: str, mode: str = 'onboarding', env: str = "prod", correlation_id: str = "", caller_id: str = "") -> AsyncIterator[str]:
    if correlation_id:
        CorrelationContext.set(correlation_id)
    else:
        correlation_id = CorrelationContext.get_or_generate()
    session = await get_or_create_session(session_id)
    set_jit_session("done" if mode == "servicing" else session.phase, session_id)
    set_request_id(session.request_id)
    async with session.lock:
        await _refresh_session_applicant_id(session)
        if mode == 'servicing':
            policy = await _intake_policy.validate_servicing(message, session, source="react")
        else:
            policy = await _intake_policy.validate_onboarding(message, session, source="react")
        if not policy.allowed:
            yield f"data: {_json.dumps({'type': 'token', 'token': policy.reason})}\n\n"
            yield f"data: {_json.dumps({'type': 'done', 'session_id': session_id, 'correlation_id': correlation_id, 'draft': session.draft, 'draft_complete': bool(session.request_id), '_id': session.applicant_id, 'request_id': session.request_id, 'phase': session.phase, 'reply': policy.reason})}\n\n"
            return
        if policy.metadata.get("approved_message") is not None:
            message = policy.metadata["approved_message"]

    if mode == 'servicing':
        response = await get_servicing_message_response(message, session_id, source='react', env=env, caller_id=caller_id)
        yield f"data: {_json.dumps({'type': 'token', 'token': response['reply']})}\n\n"
        yield f"data: {_json.dumps({'type': 'done', 'session_id': session_id, 'correlation_id': correlation_id, 'draft': response['draft'], 'draft_complete': response['draft_complete'], '_id': response['_id'] or session.applicant_id, 'request_id': response['request_id'] or session.request_id, 'phase': 'servicing', 'reply': response['reply']})}\n\n"
        return

    async with session.lock:
        reply, request_id = await _process_and_submit(session, message, env=env)

        if reply is not None:
            yield f"data: {_json.dumps({'type': 'token', 'token': reply})}\n\n"
            yield f"data: {_json.dumps({'type': 'done', 'session_id': session_id, 'correlation_id': correlation_id, 'draft': session.draft, 'draft_complete': bool(request_id), '_id': session.applicant_id, 'request_id': request_id, 'phase': session.phase, 'reply': reply})}\n\n"
            return

        reply_tokens: list[str] = []
        async for token in stream_reply(session):
            reply_tokens.append(token)
            yield f"data: {_json.dumps({'type': 'token', 'token': token})}\n\n"

        full_reply = normalize_tool_response_text("".join(reply_tokens))
        await record_stream_tokens(session_id, env=env)
        agent_policy = await _intake_policy.validate_agent_response(full_reply, source='react')
        if not agent_policy.allowed:
            full_reply = agent_policy.reason
        yield f"data: {_json.dumps({'type': 'done', 'session_id': session_id, 'correlation_id': correlation_id, 'draft': session.draft, 'draft_complete': False, '_id': session.applicant_id, 'request_id': session.request_id, 'phase': session.phase, 'reply': full_reply})}\n\n"


def _extract_source_arg(*args, **kwargs) -> str:
    if 'source' in kwargs:
        return kwargs['source']
    if len(args) >= 3:
        return args[2]
    return 'unknown'


def _validate_resolver_response(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        response = await func(*args, **kwargs)
        source = _extract_source_arg(*args, **kwargs)
        if isinstance(response, dict) and 'reply' in response:
            agent_policy = await _intake_policy.validate_agent_response(response['reply'], source=source)
            if not agent_policy.allowed:
                response['reply'] = agent_policy.reason
        return response

    return wrapper


async def get_intake_message_response(message: str, session_id: str, mode: str = 'onboarding', source: str = "unknown", env: str = "prod", correlation_id: str = "", caller_id: str = "") -> dict:
    if correlation_id:
        CorrelationContext.set(correlation_id)
    else:
        correlation_id = CorrelationContext.get_or_generate()
    session = await get_or_create_session(session_id)
    set_jit_session("done" if mode == "servicing" else session.phase, session_id)
    set_request_id(session.request_id)
    async with session.lock:
        await _refresh_session_applicant_id(session)
        if mode == 'servicing':
            policy = await _intake_policy.validate_servicing(message, session, source=source)
        else:
            policy = await _intake_policy.validate_onboarding(message, session, source=source)
        if not policy.allowed:
            return {
                "session_id": session_id,
                "correlation_id": correlation_id,
                "reply": policy.reason,
                "draft": session.draft if mode != 'servicing' else {},
                "draft_complete": bool(session.request_id) if mode != 'servicing' else False,
                "_id": session.applicant_id,
                "request_id": session.request_id,
                "phase": session.phase if mode != 'servicing' else 'servicing',
            }
        if policy.metadata.get("approved_message") is not None:
            message = policy.metadata["approved_message"]

    if mode == 'servicing':
        logger.info("get_intake_message_response servicing mode message=%r session_id=%s env=%s caller=%s", message, session_id, env, caller_id or "—")
        result = await get_servicing_message_response(message, session_id, source=source, env=env, caller_id=caller_id)
        result.setdefault("_id", None)
        if not result["_id"]:
            result["_id"] = session.applicant_id
        result.setdefault("request_id", session.request_id)
        return result

    async with session.lock:
        reply, _ = await _process_and_submit(session, message, env=env)
        if reply is None:
            reply = normalize_tool_response_text(
                "".join([t async for t in stream_reply(session)])
            )
            await record_stream_tokens(session_id, env=env)
        agent_policy = await _intake_policy.validate_agent_response(reply, source=source)
        if not agent_policy.allowed:
            reply = agent_policy.reason
        return {
            "session_id": session_id,
            "correlation_id": correlation_id,
            "reply": reply,
            "draft": session.draft,
            "draft_complete": bool(session.request_id),
            "_id": session.applicant_id,
            "request_id": session.request_id,
            "phase": session.phase,
        }


@_validate_resolver_response
async def get_servicing_message_response(message: str, session_id: str, source: str = "unknown", env: str = "prod", caller_id: str = "") -> dict:
    set_jit_session("done", session_id)
    set_request_id(None)
    logger.info("get_servicing_message_response source=%s env=%s caller=%s", source, env, caller_id or "—")
    result, error_reply = await call_resolver_safe(message, config.RESOLVER_TIMEOUT_SECONDS, source=source, env=env, session_id=session_id, correlation_id=CorrelationContext.get_or_generate(), caller_id=caller_id)
    if error_reply:
        reply = error_reply
    elif isinstance(result, dict):
        from shared.common.infra.vendor import unwrap_provider_text
        isar = result.get("isar_synthesis") or ""
        reply = (
            unwrap_provider_text(isar).strip() if isar else None
        ) or extract_tool_message_content(result, workflow_tool_names=_WORKFLOW_TOOL_NAMES) or extract_result_content(result)
    else:
        reply = normalize_tool_response_text(str(result))

    response = {
        "session_id": session_id,
        "reply": reply,
        "draft": {},
        "draft_complete": False,
        "_id": None,
        "request_id": None,
    }
    if isinstance(result, dict):
        for key in ("request_id", "status", "_id", "steps", "error", "guardian", "reflection"):
            if key in result:
                response[key] = result[key]

    return response
