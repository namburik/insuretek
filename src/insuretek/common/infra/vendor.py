"""InsureTek-specific schema and vendor utilities."""

from mltek.common.infra.vendor import entity_json_property


def applicant_json_property(schema: dict) -> dict:
    """InsureTek applicant-specific schema for object-or-string JSON.

    Handles model differences between Google (string-aware) and other providers.
    """
    return entity_json_property(
        schema,
        entity_name="applicant",
        description="Complete applicant intake data as an object or JSON string."
    )


__all__ = ["applicant_json_property"]
