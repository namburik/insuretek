"""Unit tests for the Guardian Agent.

No MCP server or Ollama instance required — the LLM is mocked via
monkeypatching so decisions are returned as pre-canned GuardianDecision
objects, testing routing/decision logic in isolation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from shared.common.agents.bridge import AIMessage

ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from shared.common.agents.guardian import GuardianAgent, GuardianDecision


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mock_llm(decision: GuardianDecision):
    """Return a mock decision agent whose ainvoke() yields decision."""
    agent = MagicMock()
    agent.ainvoke = AsyncMock(return_value={"structured_response": decision.model_dump()})
    return agent


# ── Tests ──────────────────────────────────────────────────────────────────────

async def test_oversized_intent_is_blocked():
    """Intents >= 1000 tokens must be blocked for all agents, including read-only."""
    long_intent = "create merchant with account name Acme Corp " * 130
    for agent_id in ("admin_agent", "merchant_agent", "workflow_agent"):
        agent = GuardianAgent(MagicMock())
        decision = await agent.check(long_intent, agent_id)
        assert decision.allowed is False, f"{agent_id} should be blocked on oversized intent"
        assert decision.risk_level == "HIGH"
        assert "1000" in decision.reason


async def test_delete_without_uuid_is_blocked():
    canned = GuardianDecision(allowed=False, risk_level="HIGH",
                              reason="No UUID provided; ambiguous deletions are prohibited.")
    agent = GuardianAgent(_mock_llm(canned))
    decision = await agent.check("Delete the merchant named Acme Corp", "admin_agent")
    assert decision.allowed is False


async def test_create_with_proper_fields_is_allowed():
    canned = GuardianDecision(allowed=True, risk_level="LOW",
                              reason="Standard create with all required fields.")
    agent = GuardianAgent(_mock_llm(canned))
    decision = await agent.check(
        'Create a merchant with merchant_json={"account_name": "New Corp", "merchant_type": "SMB"}',
        "admin_agent",
    )
    assert decision.allowed is True
    assert decision.risk_level == "LOW"


async def test_create_with_drop_in_business_name_uses_llm_evaluation():
    canned = GuardianDecision(allowed=True, risk_level="LOW",
                              reason="Enterprise create allowed after LLM evaluation.")
    mock_agent = _mock_llm(canned)
    agent = GuardianAgent(mock_agent)
    decision = await agent.check(
        "Create a merchant with business_name: Drop Payments and enterprise_id: ENT-PTK-001",
        "admin_agent",
    )
    mock_agent.ainvoke.assert_awaited_once()
    assert decision.allowed is True
    assert decision.risk_level == "LOW"


async def test_create_with_drop_in_quoted_account_name_uses_llm_evaluation():
    canned = GuardianDecision(allowed=True, risk_level="LOW",
                              reason="Enterprise create allowed after LLM evaluation.")
    mock_agent = _mock_llm(canned)
    agent = GuardianAgent(mock_agent)
    decision = await agent.check(
        'Create a new merchant with "account_name": "Drop Payments", enterprise_identifier: ENT-PTK-001',
        "admin_agent",
    )
    mock_agent.ainvoke.assert_awaited_once()
    assert decision.allowed is True
    assert decision.risk_level == "LOW"


async def test_create_with_drop_in_business_name_quoted_json_uses_llm_evaluation():
    canned = GuardianDecision(allowed=True, risk_level="LOW",
                              reason="Enterprise create allowed after LLM evaluation.")
    mock_agent = _mock_llm(canned)
    agent = GuardianAgent(mock_agent)
    decision = await agent.check(
        'Create a merchant with {"business_name": "Drop Payments", "enterprise_identifier": "ENT-PTK-001"}',
        "admin_agent",
    )
    mock_agent.ainvoke.assert_awaited_once()
    assert decision.allowed is True
    assert decision.risk_level == "LOW"


async def test_pricing_deactivation_with_pricing_id_is_blocked():
    agent = GuardianAgent(_mock_llm(GuardianDecision(allowed=False, risk_level="HIGH", reason="blocked")))
    decision = await agent.check(
        "Deactivate pricing price_abc123def456abcd for merchant "
        "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "admin_agent",
    )
    assert decision.allowed is False
    assert decision.risk_level == "HIGH"


async def test_pricing_deactivation_without_pricing_id_is_blocked():
    agent = GuardianAgent(_mock_llm(GuardianDecision(allowed=False, risk_level="HIGH", reason="blocked")))
    decision = await agent.check(
        "Deactivate pricing for merchant a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "admin_agent",
    )
    assert decision.allowed is False


async def test_bulk_delete_is_blocked():
    canned = GuardianDecision(allowed=False, risk_level="HIGH",
                              reason="Bulk delete without explicit ID list is prohibited.")
    agent = GuardianAgent(_mock_llm(canned))
    decision = await agent.check("Delete all merchants in the system", "admin_agent")
    assert decision.allowed is False


async def test_read_only_agents_auto_allowed_without_llm_call():
    """Non-admin agents must be allowed immediately with no LLM invocation."""
    mock = MagicMock()
    mock.ainvoke = AsyncMock(side_effect=AssertionError("LLM must not be called for read-only agents"))
    agent = GuardianAgent(mock)
    for read_agent in ("merchant_agent", "platform_agent", "hierarchy_agent",
                       "workflow_agent", "sme_agent"):
        decision = await agent.check("List all merchants", read_agent)
        assert decision.allowed is True, f"{read_agent} should always be allowed"
        assert decision.risk_level == "LOW"


async def test_fail_open_on_llm_error():
    """LLM errors must allow through (fail-open) and not crash the dispatcher."""
    agent = GuardianAgent(MagicMock(ainvoke=AsyncMock(side_effect=RuntimeError("Ollama connection refused"))))
    decision = await agent.check(
        "Delete merchant a1b2c3d4-e5f6-7890-abcd-ef1234567890", "admin_agent"
    )
    assert decision.allowed is True
    assert "fail" in decision.reason.lower() or "guardian" in decision.reason.lower()


async def test_falls_back_to_json_in_ai_message_with_trailing_text():
    agent = GuardianAgent(MagicMock(ainvoke=AsyncMock(return_value={
        "messages": [
            AIMessage(content=(
                "Guardian analysis complete.\n"
                "Response follows:\n\n"
                "{\"allowed\": true, \"risk_level\": \"LOW\", \"reason\": \"Guarded operation allowed.\"}\n"
                "Notes: execution may continue."
            ))
        ]
    })))
    decision = await agent.check(
        "Delete merchant a1b2c3d4-e5f6-7890-abcd-ef1234567890", "admin_agent"
    )
    assert decision.allowed is True
    assert decision.risk_level == "LOW"
    assert decision.reason == "Guarded operation allowed."


async def test_simultaneous_deactivation_and_deletion_is_blocked():
    canned = GuardianDecision(
        allowed=False, risk_level="HIGH",
        reason="Combined deactivation and deletion requires separate authorization.",
    )
    agent = GuardianAgent(_mock_llm(canned))
    decision = await agent.check(
        "Deactivate all pricing and delete merchant "
        "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "admin_agent",
    )
    assert decision.allowed is False


async def test_update_is_blocked():
    """Update operations are temporarily hard-blocked."""
    agent = GuardianAgent(_mock_llm(GuardianDecision(allowed=False, risk_level="HIGH", reason="blocked")))
    decision = await agent.check(
        "Update the contact email for merchant 'Acme Corp' to ops@acme.com",
        "admin_agent",
    )
    assert decision.allowed is False
    assert decision.risk_level == "HIGH"


async def test_rotate_is_blocked():
    """Rotate (write verb) is temporarily hard-blocked."""
    agent = GuardianAgent(_mock_llm(GuardianDecision(allowed=False, risk_level="HIGH", reason="blocked")))
    decision = await agent.check(
        "Rotate the auth client secret for merchant "
        "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "admin_agent",
    )
    assert decision.allowed is False
    assert decision.risk_level == "HIGH"


async def test_enterprise_update_is_blocked():
    """Update on an at-risk enterprise is blocked before LLM."""
    agent = GuardianAgent(_mock_llm(GuardianDecision(allowed=False, risk_level="HIGH", reason="blocked")))
    decision = await agent.check(
        "Update contact info for enterprise ENT-9001",
        "admin_agent",
        enterprise_risk_ids={"ENT-9001", "ENT-8002"},
    )
    assert decision.allowed is False
    assert decision.risk_level == "HIGH"


async def test_enterprise_not_in_risk_list_is_blocked():
    """Update is blocked regardless of enterprise risk list membership."""
    agent = GuardianAgent(_mock_llm(GuardianDecision(allowed=False, risk_level="HIGH", reason="blocked")))
    decision = await agent.check(
        "Update merchant a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "admin_agent",
        enterprise_risk_ids={"ENT-9001"},
    )
    assert decision.allowed is False


def main():
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))


if __name__ == "__main__":
    main()
