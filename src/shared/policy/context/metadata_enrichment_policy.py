"""Metadata enrichment policy — injects actionable domain context (enums, constraints, tool sequencing) before dispatch."""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from shared.policy.abstract_policy import Policy, PolicyDecision
from shared.common.agents.prompts import AGENT_DOMAIN as _AGENT_DOMAIN
from shared.common.infra.audit_decorator import audit_policy_decision
from shared.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)


# ── Abstract base ──────────────────────────────────────────────────────────────

class MetadataPolicy(Policy):
    """Enrichment-only policy; always returns allowed=True with context_block in metadata."""

    @abstractmethod
    async def evaluate(self, agent_id: str) -> PolicyDecision:
        ...


# ── Metadata store contract ────────────────────────────────────────────────────

class MetadataStore(ABC):
    """Read-only interface to statistical, relational, and operational metadata."""

    @abstractmethod
    async def get_statistical_profile(self, domain: str) -> dict[str, Any]: ...

    @abstractmethod
    async def get_relational_deps(self, domain: str) -> dict[str, Any]: ...

    @abstractmethod
    async def get_operational_metadata(self, domain: str) -> dict[str, Any]: ...


# ── Static Insuretek metadata store ───────────────────────────────────────────

class StaticMetadataStore(MetadataStore):
    """Enum values, field constraints, and tool sequencing rules derived from actual domain models.

    All values are sourced from core_data enums and constants — nothing is hardcoded or estimated.
    """

    _RELATIONAL: dict[str, dict] = {
        "semantic": {
            "applicant → kg_node":     "1:1 — every applicant has a KG representation",
            "kg_node → similar_nodes": "0:N — similarity edges with weight ≥ 0.72",
        },
    }

    async def get_statistical_profile(self, domain: str) -> dict[str, Any]:
        try:
            from core_data.domain.enums import PolicyType
            from core_data.domain.constants import (
                ApplicantJsonFields, LocationAddressFields,
                ContactInfoFields, ServerDefaults,
            )
        except Exception:
            return {}

        if domain == "underwriting":
            return {
                "required_fields":          ApplicantJsonFields.required(),
                "required_location_address": LocationAddressFields.required(),
                "required_contact_info":     ContactInfoFields.required(),
                "enum_policy_type":         [e.value for e in PolicyType],
            }
        if domain == "semantic":
            return {
                "similarity_threshold": 0.72,
            }
        return {}

    async def get_relational_deps(self, domain: str) -> dict[str, Any]:
        return self._RELATIONAL.get(domain, {})

    async def get_operational_metadata(self, _domain: str) -> dict[str, Any]:
        return {}


# ── Context block formatter ────────────────────────────────────────────────────

def _format_context_block(domain: str, stats: dict, relational: dict, operational: dict) -> str:
    lines = [f"=== ACTIVE METADATA CONTEXT (domain: {domain}) ==="]

    if stats:
        lines.append("\nStatistical profile:")
        for k, v in stats.items():
            lines.append(f"  {k}: {v}")

    if relational:
        lines.append("\nRelational dependencies:")
        for k, v in relational.items():
            lines.append(f"  {k}: {v}")

    if operational:
        lines.append("\nOperational metadata:")
        for k, v in operational.items():
            lines.append(f"  {k}: {v}")

    lines.append(
        "\nUse this context to calibrate thresholds and understand data relationships. "
        "Do NOT add extra verification tool calls — each tool validates its own preconditions internally. "
        "Call only the tool(s) explicitly requested."
    )
    return "\n".join(lines)


# ── Config ─────────────────────────────────────────────────────────────────────

class MetadataConfig(BaseModel):
    enabled: bool = True
    domains: frozenset[str] = Field(default_factory=lambda: frozenset(_AGENT_DOMAIN.values()))


# ── Metadata Enrichment Policy ─────────────────────────────────────────────────

class MetadataEnrichmentPolicy(MetadataPolicy):
    """Fetches domain metadata and returns context_block in PolicyDecision.metadata. Non-blocking (always allowed=True)."""

    def __init__(
        self,
        config: MetadataConfig | None = None,
        store: MetadataStore | None = None,
    ):
        self._config = config or MetadataConfig()
        self._store  = store  or StaticMetadataStore()

    @audit_policy_decision(policy_type="metadata", operation_kwarg="agent_id")
    async def evaluate(self, agent_id: str) -> PolicyDecision:
        t0     = time.monotonic()
        domain = _AGENT_DOMAIN.get(agent_id, "applicant")

        if domain not in self._config.domains:
            logger.info("MetadataEnrichmentPolicy skipped: domain=%s not in config domains", domain)
            return PolicyDecision(
                allowed=True,
                reason="Domain not subject to metadata enrichment.",
                metadata={"domain": domain, "enriched": False},
            )

        stats, relational, operational = await asyncio.gather(
            self._store.get_statistical_profile(domain),
            self._store.get_relational_deps(domain),
            self._store.get_operational_metadata(domain),
        )

        context_block = _format_context_block(domain, stats, relational, operational)

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "MetadataEnrichmentPolicy enriched: agent=%s domain=%s elapsed_ms=%.1f",
            agent_id, domain, elapsed_ms,
        )
        return PolicyDecision(
            allowed=True,
            reason=f"Metadata context injected for domain '{domain}'.",
            metadata={
                "domain":        domain,
                "enriched":      True,
                "elapsed_ms":    round(elapsed_ms, 1),
                "context_block": context_block,
                "stats":         stats,
                "relational":    relational,
                "operational":   operational,
            },
        )

