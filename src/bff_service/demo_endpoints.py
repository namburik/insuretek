"""Demo intake endpoints — real conversational flow with mocked submission.

Runs the full onboarding wizard (extraction + reply LLM calls) but intercepts
the /workflow/ submission and returns a canned demo response instead
of writing to any real infrastructure.
"""

import json as _json
import uuid
from typing import AsyncIterator

from shared.common.infra.context import CorrelationContext, set_jit_session, set_request_id
from shared.common.router.resolver import record_stream_tokens
from shared.common.log import setup_structured_logging
from shared.common.tools.format import normalize_tool_response_text
from bff_service.session import get_or_create_session, process_turn, stream_reply
from bff_service.prompt_validation_policy import PromptValidationPolicy

logger = setup_structured_logging(__name__)

_intake_policy = PromptValidationPolicy()

_DEMO_CTA = "\n\nReady to onboard for real? Start a new session to submit your application."


def _mock_submit(session) -> tuple[str, str]:
    """Return a canned confirmation without touching the workflow engine."""
    request_id = f"demo_req_{uuid.uuid4().hex[:8]}"
    applicant_id = f"demo_app_{uuid.uuid4().hex[:8]}"
    session.request_id = request_id
    session.applicant_id = applicant_id
    reply = (
        "Demo complete! In production this would submit your onboarding.\n\n"
        f"Demo Request ID: `{request_id}`\n"
        f"Demo Applicant ID: `{applicant_id}`"
        + _DEMO_CTA
    )
    logger.info("demo _mock_submit request_id=%s applicant_id=%s", request_id, applicant_id)
    return reply, request_id


def _already_submitted_reply(session) -> str:
    return (
        f"Demo already submitted.\n\n"
        f"Demo Request ID: `{session.request_id}`\n"
        f"Demo Applicant ID: `{session.applicant_id}`"
        + _DEMO_CTA
    )


async def generate_demo_stream(message: str, session_id: str) -> AsyncIterator[str]:
    CorrelationContext.get_or_generate()
    session = await get_or_create_session(session_id)
    set_jit_session(session.phase, session_id)
    set_request_id(session.request_id)

    async with session.lock:
        policy = await _intake_policy.validate_onboarding(message, session, source="demo")
        if not policy.allowed:
            yield f"data: {_json.dumps({'type': 'token', 'token': policy.reason})}\n\n"
            yield f"data: {_json.dumps({'type': 'done', 'session_id': session_id, 'draft': session.draft, 'draft_complete': False, '_id': None, 'request_id': None, 'phase': session.phase, 'reply': policy.reason})}\n\n"
            return
        if policy.metadata.get("approved_message") is not None:
            message = policy.metadata["approved_message"]

    async with session.lock:
        if session.request_id or session.applicant_id:
            reply = _already_submitted_reply(session)
            yield f"data: {_json.dumps({'type': 'token', 'token': reply})}\n\n"
            yield f"data: {_json.dumps({'type': 'done', 'session_id': session_id, 'draft': session.draft, 'draft_complete': True, '_id': session.applicant_id, 'request_id': session.request_id, 'phase': session.phase, 'reply': reply})}\n\n"
            return

        confirmed = await process_turn(session, message)
        if confirmed:
            reply, request_id = _mock_submit(session)
            yield f"data: {_json.dumps({'type': 'token', 'token': reply})}\n\n"
            yield f"data: {_json.dumps({'type': 'done', 'session_id': session_id, 'draft': session.draft, 'draft_complete': True, '_id': session.applicant_id, 'request_id': request_id, 'phase': session.phase, 'reply': reply})}\n\n"
            return

        reply_tokens: list[str] = []
        async for token in stream_reply(session):
            reply_tokens.append(token)
            yield f"data: {_json.dumps({'type': 'token', 'token': token})}\n\n"

        full_reply = normalize_tool_response_text("".join(reply_tokens))
        await record_stream_tokens(session_id, env=session.env)
        agent_policy = await _intake_policy.validate_agent_response(full_reply, source="demo")
        if not agent_policy.allowed:
            full_reply = agent_policy.reason
        yield f"data: {_json.dumps({'type': 'done', 'session_id': session_id, 'draft': session.draft, 'draft_complete': False, '_id': session.applicant_id, 'request_id': session.request_id, 'phase': session.phase, 'reply': full_reply})}\n\n"


async def get_demo_message_response(message: str, session_id: str) -> dict:
    CorrelationContext.get_or_generate()
    session = await get_or_create_session(session_id)
    set_jit_session(session.phase, session_id)
    set_request_id(session.request_id)

    async with session.lock:
        policy = await _intake_policy.validate_onboarding(message, session, source="demo")
        if not policy.allowed:
            return {
                "session_id": session_id,
                "reply": policy.reason,
                "draft": session.draft,
                "draft_complete": False,
                "_id": None,
                "request_id": None,
                "phase": session.phase,
            }
        if policy.metadata.get("approved_message") is not None:
            message = policy.metadata["approved_message"]

    async with session.lock:
        if session.request_id or session.applicant_id:
            reply = _already_submitted_reply(session)
            return {
                "session_id": session_id,
                "reply": reply,
                "draft": session.draft,
                "draft_complete": True,
                "_id": session.applicant_id,
                "request_id": session.request_id,
                "phase": session.phase,
            }

        confirmed = await process_turn(session, message)
        if confirmed:
            reply, request_id = _mock_submit(session)
        else:
            reply = normalize_tool_response_text(
                "".join([t async for t in stream_reply(session)])
            )
            await record_stream_tokens(session_id, env=session.env)
            agent_policy = await _intake_policy.validate_agent_response(reply, source="demo")
            if not agent_policy.allowed:
                reply = agent_policy.reason
            request_id = session.request_id

        return {
            "session_id": session_id,
            "reply": reply,
            "draft": session.draft,
            "draft_complete": bool(session.request_id),
            "_id": session.applicant_id,
            "request_id": request_id,
            "phase": session.phase,
        }
