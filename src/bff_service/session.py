"""
Applicant intake session handler.

Two LLM calls per turn:
  1. Extraction call  — structured output (ExtractionResult), non-streaming.
  2. Reply call       — plain text, streamed token-by-token to the client.

Session state lives in-process in a dict; swap for Redis/DB in production.

Phases:
  collecting        → ask for missing required fields one at a time
  offering_optional → all required fields present; offer optional fields
  confirming        → provide summary, ask to confirm
  done              → confirmed; caller should POST to /workflow/applicant
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Literal

from shared.common.agents.bridge import AIMessage, HumanMessage, SystemMessage

from shared.common.agents.lang_agents import call_chat_agent, get_chat_llm, parse_llm_json
from shared.common.infra.context import with_token_phase, set_active_agent
from shared.common.log import setup_structured_logging
from shared.common.infra.setup import deep_merge
from shared.common.infra.vendor import normalize_stream_chunk
from bff_service.prompts import INTAKE_REQUIRED_LABELS
from shared.config import config
from core_data.domain.enums import PolicyType
from bff_service.schema import ExtractionResult, IntakeSession
from bff_service.prompts import extraction_system_prompt, reply_system_prompt

logger = setup_structured_logging(__name__)

Phase = Literal["collecting", "offering_optional", "confirming", "done"]

_MAX_HISTORY = 30
_SESSION_TTL_SECONDS = 60 * 60

_sessions: dict[str, IntakeSession] = {}
_sessions_lock = asyncio.Lock()


# ── Session management ─────────────────────────────────────────────────────────

def _cleanup_expired_sessions(max_age_seconds: int = _SESSION_TTL_SECONDS) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    removed = [sid for sid, s in _sessions.items() if s.last_active < cutoff]
    for sid in removed:
        _sessions.pop(sid, None)
    if removed:
        logger.info("Removed %d stale intake session(s)", len(removed))


async def get_or_create_session(session_id: str) -> IntakeSession:
    async with _sessions_lock:
        _cleanup_expired_sessions()
        session = _sessions.get(session_id)
        if session is None:
            logger.info("Created intake session session_id=%s", session_id)
            session = IntakeSession()
            _sessions[session_id] = session
        else:
            session.last_active = datetime.now(timezone.utc)
        return session


# ── Phase helpers ──────────────────────────────────────────────────────────────

def _missing(draft: dict) -> list[str]:
    required = []
    valid_policy_types = {pt.value for pt in PolicyType}
    for f in INTAKE_REQUIRED_LABELS:
        value = draft.get(f)
        if not value or (isinstance(value, str) and not value.strip()):
            required.append(f)
            continue
        if f == "policy_type" and value not in valid_policy_types:
            required.append(f)
    return required


def _advance_phase(current: Phase, draft: dict, is_valid: bool) -> Phase:
    if current == "done":
        return "done"
    if current == "collecting":
        # No optional fields in insurance intake — skip directly to confirming.
        return "confirming" if (is_valid and not _missing(draft)) else "collecting"
    return current  # "offering_optional" and "confirming" stay until explicit signals


def _sanitize_draft(draft: dict) -> dict:
    cleaned: dict = {}
    for key, value in draft.items():
        if key in {"contact_info", "location_address", "coverage_request", "risk_profile"}:
            if not isinstance(value, dict):
                cleaned[key] = value
                continue
            nested = {
                nested_key: nested_value
                for nested_key, nested_value in value.items()
                if nested_value is not None and (
                    not isinstance(nested_value, str) or nested_value.strip()
                )
            }
            if nested:
                cleaned[key] = nested
            continue
        cleaned[key] = value
    return cleaned


def _is_confirmation_reply(text: str) -> bool:
    return bool(re.fullmatch(
        r"(?:yes|yep|yeah|ok(?:ay)?|sure|sounds good|sounds great|looks good|looks fine|"
        r"that works|all set|please proceed|proceed|submit|ready to submit|go ahead|"
        r"give the green light|thank you|sure thank you|thanks)(?:[.!?]|\s+please)?",
        (text or "").strip(),
        re.IGNORECASE,
    ))


def _is_skip_optional_reply(text: str) -> bool:
    return bool(re.fullmatch(
        r"(?:skip|no|no thanks|nah|not now|dont need|don\'t need|nope|no thank you)(?:[.!?])?",
        (text or "").strip(),
        re.IGNORECASE,
    ))


def _is_done_with_optionals_reply(text: str) -> bool:
    """User explicitly indicates they are finished providing optional values."""
    return bool(re.fullmatch(
        r"(?:that'?s (?:all|it)|no more|done|all done|all good|proceed|please proceed|"
        r"continue|next|go ahead|ready to submit|submit)(?:[.!?]|\s+please)?",
        (text or "").strip(),
        re.IGNORECASE,
    ))


def _is_direct_submit_reply(text: str) -> bool:
    """User wants to submit immediately, skipping optionals/confirm."""
    return bool(re.fullmatch(
        r"(?:submit|finish|complete|done|that'?s all|no more|skip(?: optional)?(?: and submit)?|skip and submit|submit now)(?:[.!?]|\s+please)?",
        (text or "").strip(),
        re.IGNORECASE,
    ))


def _is_single_shot_prompt(text: str) -> bool:
    return bool(re.search(
        r"\b(?:single[ -]?shot|one[ -]?shot|one shot|single shot|submit directly|direct submit|do not ask follow-up|do not ask follow up|no follow[- ]?up|no follow up|submit in one shot|one-shot|single-shot)\b",
        (text or "").lower(),
    ))


# ── Extraction call ────────────────────────────────────────────────────────────

@with_token_phase("extraction")
async def _extract(message: str) -> ExtractionResult:
    set_active_agent("extraction")
    try:
        raw = await call_chat_agent(message, system_prompt=extraction_system_prompt)
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if not m:
            logger.warning("_extract: no JSON in response len=%d", len(raw))
            return ExtractionResult()
        data = parse_llm_json(m.group(0), context="ExtractionResult")
        result = ExtractionResult.model_validate(data)
        logger.debug("_extract: updates=%s confirmed=%s", result.updates.to_dict(), result.user_confirmed)
        return result
    except Exception as exc:
        logger.warning("_extract error: %s", exc)
        return ExtractionResult()


# ── process_turn ───────────────────────────────────────────────────────────────

async def process_turn(session: IntakeSession, message: str) -> bool:
    """Extract field values, update session state, return True when confirmed and ready to submit."""
    extraction = await _extract(message)
    updates = extraction.updates.to_dict()

    if updates:
        session.draft = deep_merge(session.draft, updates)

    session.draft = _sanitize_draft(session.draft)
    session.history.append(HumanMessage(content=message))

    if len(session.history) > _MAX_HISTORY:
        logger.info(
            "process_turn: history flush length=%d→1 request_id=%s",
            len(session.history),
            session.request_id or "none",
        )
        session.history = [session.history[-1]]

    is_valid = bool(session.draft) and not _missing(session.draft)

    phase_before = session.phase
    session.phase = _advance_phase(session.phase, session.draft, is_valid)
    if phase_before == "offering_optional" and (
        _is_skip_optional_reply(message) or _is_done_with_optionals_reply(message)
    ):
        session.phase = "confirming"

    confirmed = False
    # Only allow a structured confirmation to complete submission if the session
    # is already on the confirming screen, or if the user explicitly provided
    # a one-shot prompt.
    if _is_single_shot_prompt(message) and is_valid:
        confirmed = True
        session.phase = "done"
    elif extraction.user_confirmed and is_valid and session.phase == "confirming" and phase_before == "confirming":
        confirmed = True
        session.phase = "done"
    elif is_valid and _is_direct_submit_reply(message):
        confirmed = True
        session.phase = "done"
    elif session.phase == "confirming" and phase_before == "confirming":
        text_confirms = _is_confirmation_reply(message)
        confirmed = is_valid and text_confirms
        if confirmed:
            session.phase = "done"

    if updates or phase_before != session.phase or confirmed:
        log_method = logger.info if session.phase == "done" else logger.debug
        log_method(
            "process_turn: phase %s→%s confirmed=%s request_id=%s draft_keys=%s missing=%s",
            phase_before,
            session.phase,
            confirmed,
            session.request_id or "none",
            list(session.draft.keys()),
            _missing(session.draft) if session.phase == "collecting" else [],
        )
    return confirmed


# ── stream_reply ───────────────────────────────────────────────────────────────

@with_token_phase("stream_reply")
async def stream_reply(session: IntakeSession) -> AsyncIterator[str]:
    """Stream the assistant reply for the current session state, token by token."""
    logger.debug(
        "stream_reply entered phase=%s draft_keys=%s history_length=%d",
        session.phase,
        list(session.draft.keys()),
        len(session.history),
    )
    if session.request_id:
        msg = f"This intake session already has an active request_id: {session.request_id}."
        if session.applicant_id:
            msg += f" Applicant ID: {session.applicant_id}."
        msg += " To submit a new insurance application, start a new session."
        session.history.append(AIMessage(content=msg))
        yield msg
        return
    missing_fields = _missing(session.draft) if session.draft else list(INTAKE_REQUIRED_LABELS.keys())
    is_valid = not missing_fields
    validation_error = f"Missing: {', '.join(missing_fields)}" if missing_fields else None
    system = reply_system_prompt(
        session.draft, session.phase,
        validation_error if not missing_fields else None,
    )

    messages = [SystemMessage(content=system)] + session.history
    logger.debug("stream_reply prepared %d messages for astream", len(messages))

    llm = get_chat_llm()
    full_reply = ""
    try:
        async for chunk in llm.astream(messages):
            raw = getattr(chunk, "content", None)
            if raw is None:
                raw = ""
            token = normalize_stream_chunk(raw)
            if token:
                full_reply += token
                yield token
    except Exception as exc:
        logger.exception("stream_reply error: %s", exc)
        full_reply = "Sorry, I had a processing error. Could you repeat that?"
        yield full_reply

    session.history.append(AIMessage(content=full_reply))
