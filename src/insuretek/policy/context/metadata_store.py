"""InsureTek concrete MetadataStore — domain-specific static metadata for enrichment."""

from __future__ import annotations

from typing import Any

from mltek.policy.context.metadata_enrichment_policy import MetadataStore


class StaticMetadataStore(MetadataStore):
    """Enum values, field constraints, and tool sequencing rules derived from actual domain models.

    All values are sourced from core_data enums and constants.
    """

    _RELATIONAL: dict[str, dict] = {
        "semantic": {
            "applicant → kg_node":     "1:1 — every applicant has a KG representation",
            "kg_node → similar_nodes": "0:N — similarity edges with weight ≥ 0.72",
        },
    }

    async def get_statistical_profile(self, domain: str) -> dict[str, Any]:
        try:
            from insuretek.domain.enums import PolicyType
            from insuretek.domain.constants import (
                ApplicantJsonFields, LocationAddressFields,
                ContactInfoFields, ServerDefaults,
            )
        except Exception:
            return {}

        if domain == "underwriting":
            return {
                "required_fields":           ApplicantJsonFields.required(),
                "required_location_address": LocationAddressFields.required(),
                "required_contact_info":     ContactInfoFields.required(),
                "enum_policy_type":          [e.value for e in PolicyType],
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
