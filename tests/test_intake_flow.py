from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

try:
    import pytest
except ImportError:
    pytest = None

ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from bff_service.endpoints import generate_intake_stream
from mltek.policy import IntakePolicy
from bff_service.schema import ExtractionResult, IntakeSession
from bff_service.session import get_or_create_session, _sanitize_draft, process_turn
from core_data.common.validation.validators import MerchantValidator
from mltek.config import config


class SimpleMonkeyPatch:
    def __init__(self):
        self._patches: list[tuple[object, str, object]] = []

    def setattr(self, target, name, value):
        original = getattr(target, name)
        self._patches.append((target, name, original))
        setattr(target, name, value)

    def undo(self):
        for target, name, original in reversed(self._patches):
            setattr(target, name, original)
        self._patches.clear()


async def test_generate_intake_stream_submits_workflow_after_confirmation(monkeypatch=None):
    if monkeypatch is None:
        monkeypatch = SimpleMonkeyPatch()

    session_id = "test-confirmation-session"
    session = get_or_create_session(session_id)
    session.draft = {
        "account_name": "Test Merchant",
        "enterprise_identifier": "ENT-1234",
        "merchant_type": "SMB",
    }
    session.phase = "done"
    session.request_id = None

    async def fake_submit_merchant(draft):
        assert draft == session.draft
        return "req-123", None

    import bff_service.endpoints as endpoints
    monkeypatch.setattr(endpoints, "_submit_merchant", fake_submit_merchant)

    try:
        raw_lines = [line async for line in generate_intake_stream("confirm", session_id)]
        events = []
        for line in raw_lines:
            if not line.startswith("data: "):
                continue
            payload = line[len("data: "):].strip()
            events.append(json.loads(payload))

        assert any(evt.get("type") == "done" for evt in events)
        assert any(evt.get("request_id") == "req-123" for evt in events)
        assert session.request_id == "req-123"
    finally:
        monkeypatch.undo()


async def test_intake_intake_policy_servicing_allows_prompt_when_no_authorized_ids(monkeypatch=None):
    if monkeypatch is None:
        monkeypatch = SimpleMonkeyPatch()

    session = IntakeSession()
    policy = IntakePolicy()
    monkeypatch.setattr(config, "is_guardian_enabled", lambda: False)

    try:
        policy = await policy.validate_servicing(
            "Get workflow details for workflow wf_abcdef12",
            session,
            source="test",
        )
        assert policy.allowed is True
        assert policy.reply == ""
    finally:
        monkeypatch.undo()


async def test_intake_intake_policy_servicing_blocks_unauthorized_id_when_authorized_ids_exist(monkeypatch=None):
    if monkeypatch is None:
        monkeypatch = SimpleMonkeyPatch()

    session = IntakeSession()
    session.request_id = "req-123"
    session.merchant_id = "merchant-xyz"
    policy = IntakePolicy()
    monkeypatch.setattr(config, "is_guardian_enabled", lambda: False)

    try:
        policy = await policy.validate_servicing(
            "Get workflow details for workflow wf_abcdef12",
            session,
            source="test",
        )
        assert policy.allowed is False
        assert "Unauthorized identifier detected in servicing prompt" in policy.reply
    finally:
        monkeypatch.undo()


async def test_intake_intake_policy_onboarding_does_not_rewrite_prompt(monkeypatch=None):
    if monkeypatch is None:
        monkeypatch = SimpleMonkeyPatch()

    session = IntakeSession()
    policy = IntakePolicy()

    prompt = "Please onboard a merchant with name Acme and email ops@acme.com"
    try:
        policy = await policy.validate_onboarding(
            prompt,
            session,
            source="test",
        )
        assert policy.allowed is True
        assert policy.approved_message is None
    finally:
        monkeypatch.undo()


async def test_intake_intake_policy_onboarding_blocks_ssn_leaks(monkeypatch=None):
    if monkeypatch is None:
        monkeypatch = SimpleMonkeyPatch()

    session = IntakeSession()
    policy = IntakePolicy()

    try:
        policy = await policy.validate_onboarding(
            "Please onboard a merchant with SSN 123-45-6789 included",
            session,
            source="test",
        )
        assert policy.allowed is False
        assert "Sensitive data detected" in policy.reply
        assert "SSN" in policy.reply or "Sensitive" in policy.reply
    finally:
        monkeypatch.undo()


async def test_intake_intake_policy_blocks_sensitive_agent_response(monkeypatch=None):
    if monkeypatch is None:
        monkeypatch = SimpleMonkeyPatch()

    policy = IntakePolicy()

    try:
        policy = await policy.validate_agent_response(
            "The merchant SSN is 123-45-6789 and should not be exposed.",
            source="test",
        )
        assert policy.allowed is False
        assert "Sensitive data detected" in policy.reply
        assert "SSN" in policy.reply
    finally:
        monkeypatch.undo()


async def test_generate_intake_stream_includes_merchant_id_when_available(monkeypatch=None):
    if monkeypatch is None:
        monkeypatch = SimpleMonkeyPatch()

    session_id = "test-merchant-id-session"
    session = get_or_create_session(session_id)
    session.draft = {
        "account_name": "Test Merchant",
        "enterprise_identifier": "ENT-1234",
        "merchant_type": "SMB",
    }
    session.phase = "done"
    session.request_id = None
    session.merchant_id = "merchant-789"

    async def fake_submit_merchant(draft):
        assert draft == session.draft
        return "req-123", session.merchant_id

    import bff_service.endpoints as endpoints
    monkeypatch.setattr(endpoints, "_submit_merchant", fake_submit_merchant)

    try:
        raw_lines = [line async for line in generate_intake_stream("confirm", session_id)]
        events = []
        for line in raw_lines:
            if not line.startswith("data: "):
                continue
            payload = line[len("data: "):].strip()
            events.append(json.loads(payload))

        assert any(evt.get("type") == "done" for evt in events)
        assert any(evt.get("merchant_id") == "merchant-789" for evt in events)
        assert session.merchant_id == "merchant-789"
    finally:
        monkeypatch.undo()


def test_validate_merchant_json_accepts_partial_contact_info():
    merchant_json = {
        "account_name": "Test Merchant",
        "enterprise_identifier": "ENT-1234",
        "merchant_type": "SMB",
        "contact_info": {"email": "foo@example.com"},
    }
    is_valid, error = MerchantValidator.validate_merchant_json(merchant_json)
    assert is_valid is True
    assert error is None


def test_validate_merchant_json_accepts_partial_payout_account():
    merchant_json = {
        "account_name": "Test Merchant",
        "enterprise_identifier": "ENT-1234",
        "merchant_type": "SMB",
        "payout_account": {
            "account_number": "123456789",
            "account_type": "DDA",
        },
    }
    is_valid, error = MerchantValidator.validate_merchant_json(merchant_json)
    assert is_valid is True
    assert error is None


def test_sanitize_draft_normalizes_corp_account_type_to_other():
    sanitized = _sanitize_draft({
        "payout_account": {"account_type": "CORP"}
    })
    assert sanitized["payout_account"]["account_type"] == "OTHER"


def test_missing_flags_invalid_merchant_type_as_required():
    from bff_service.session import _missing
    draft = {
        "account_name": "Test Merchant",
        "enterprise_identifier": "ENT-1234",
        "merchant_type": "InvalidType",
    }
    missing = _missing(draft)
    assert "merchant_type" in missing



async def test_process_turn_does_not_submit_when_arriving_at_confirming_from_optional(monkeypatch=None):
    if monkeypatch is None:
        monkeypatch = SimpleMonkeyPatch()

    session_id = "test-offering-optional-confirmation"
    session = get_or_create_session(session_id)
    session.draft = {
        "account_name": "Test Merchant",
        "enterprise_identifier": "ENT-1234",
        "merchant_type": "SMB",
        "primary_platform_id": "direct",
        "location_address": {"line1": "1 Main St"},
        "contact_info": {"email": "foo@example.com"},
        "payout_account": {
            "account_number": "123456789",
            "routing_number": "987654321",
            "account_type": "DDA",
        },
        "account_data": {"merchant_account_setup": {"projected_monthly_volume": "1000"}},
    }
    session.phase = "offering_optional"

    async def fake_extract(message):
        return ExtractionResult(user_confirmed=True)

    import bff_service.session as session_module
    monkeypatch.setattr(session_module, "_extract", fake_extract)

    try:
        confirmed = await process_turn(session, "Yes")
        assert confirmed is False
        assert session.phase == "confirming"
    finally:
        monkeypatch.undo()


async def test_process_turn_submits_when_single_shot_boarding_style_is_valid(monkeypatch=None):
    if monkeypatch is None:
        monkeypatch = SimpleMonkeyPatch()

    session = IntakeSession()
    session.draft = {
        "account_name": "Test Merchant",
        "enterprise_identifier": "ENT-1234",
        "merchant_type": "SMB",
        "primary_platform_id": "direct",
        "location_address": {"line1": "1 Main St"},
        "contact_info": {"email": "foo@example.com"},
        "payout_account": {
            "account_number": "123456789",
            "routing_number": "987654321",
            "account_type": "DDA",
        },
        "account_data": {"merchant_account_setup": {"projected_monthly_volume": "1000"}},
    }
    session.phase = "collecting"

    async def fake_extract(message):
        return ExtractionResult()

    import bff_service.session as session_module
    monkeypatch.setattr(session_module, "_extract", fake_extract)

    try:
        confirmed = await process_turn(session, "Onboard this merchant in one shot.")
        assert confirmed is True
        assert session.phase == "done"
    finally:
        monkeypatch.undo()


async def test_process_turn_submits_when_custom_single_shot_prompt_is_valid(monkeypatch=None):
    if monkeypatch is None:
        monkeypatch = SimpleMonkeyPatch()

    session = IntakeSession()
    session.draft = {
        "account_name": "Test Merchant",
        "enterprise_identifier": "ENT-1234",
        "merchant_type": "SMB",
        "primary_platform_id": "direct",
        "location_address": {"line1": "1 Main St"},
        "contact_info": {"email": "foo@example.com"},
        "payout_account": {
            "account_number": "123456789",
            "routing_number": "987654321",
            "account_type": "DDA",
        },
        "account_data": {"merchant_account_setup": {"projected_monthly_volume": "1000"}},
    }
    session.phase = "collecting"

    async def fake_extract(message):
        return ExtractionResult()

    import bff_service.session as session_module
    monkeypatch.setattr(session_module, "_extract", fake_extract)

    try:
        confirmed = await process_turn(
            session,
            "Please onboard a new merchant in one shot without asking follow-up questions and submit directly.",
        )
        assert confirmed is True
        assert session.phase == "done"
    finally:
        monkeypatch.undo()


async def test_process_turn_does_not_submit_on_generic_onboarding_prompt(monkeypatch=None):
    if monkeypatch is None:
        monkeypatch = SimpleMonkeyPatch()

    session = IntakeSession()
    session.draft = {
        "account_name": "Test Merchant",
        "enterprise_identifier": "ENT-1234",
        "merchant_type": "SMB",
        "primary_platform_id": "direct",
        "location_address": {"line1": "1 Main St"},
        "contact_info": {"email": "foo@example.com"},
        "payout_account": {
            "account_number": "123456789",
            "routing_number": "987654321",
            "account_type": "DDA",
        },
        "account_data": {"merchant_account_setup": {"projected_monthly_volume": "1000"}},
    }
    session.phase = "collecting"

    async def fake_extract(message):
        return ExtractionResult(user_confirmed=True)

    import bff_service.session as session_module
    monkeypatch.setattr(session_module, "_extract", fake_extract)

    try:
        confirmed = await process_turn(session, "Please onboard a new merchant with the following details:")
        assert confirmed is False
        assert session.phase != "done"
    finally:
        monkeypatch.undo()


if pytest is not None:
    test_generate_intake_stream_submits_workflow_after_confirmation = pytest.mark.asyncio(
        test_generate_intake_stream_submits_workflow_after_confirmation
    )
    test_generate_intake_stream_includes_merchant_id_when_available = pytest.mark.asyncio(
        test_generate_intake_stream_includes_merchant_id_when_available
    )


def main():
    if pytest is None:
        asyncio.run(test_generate_intake_stream_submits_workflow_after_confirmation())
        print("PASSED")
        return

    raise SystemExit(pytest.main([__file__]))


if __name__ == "__main__":
    main()
