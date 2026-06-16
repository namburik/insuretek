import inspect
import json
import typing
from typing import Optional

from pydantic import BaseModel

from core_data.domain.enums import PolicyType
from bff_service.schema import DraftUpdates

INTAKE_REQUIRED_LABELS = {
    "applicant_name":   "Applicant / business name",
    "policy_type":      f"Policy type  ({' | '.join(e.value for e in PolicyType)})",
    "coverage_request": "Coverage amount and deductible",
    "declared_value":   "Declared property / asset value",
    "contact_info":     "Contact email address",
    "location_address": "Property / risk address",
    "risk_profile":     "Property type, year built, prior claims count",
}

_FIELD_ENUM_MAP: dict[str, type] = {
    "policy_type": PolicyType,
}


def _unwrap_optional(annotation: type) -> type:
    """Strip Optional[X] / X | None → inner type X."""
    args = typing.get_args(annotation)
    if args:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _nested_subfields(model_cls: type) -> dict[str, list[str]]:
    """Return {field_name: [subfield_names]} for BaseModel-typed fields in model_cls."""
    result = {}
    for name, field_info in model_cls.model_fields.items():
        inner = _unwrap_optional(field_info.annotation)
        if inspect.isclass(inner) and issubclass(inner, BaseModel):
            result[name] = list(inner.model_fields.keys())
    return result


def build_extraction_system_prompt() -> str:
    top_fields = "\n".join(f"  {f}" for f in DraftUpdates.model_fields)

    nested_hints = "\n".join(
        f"  {parent}: {', '.join(subfields)}"
        for parent, subfields in _nested_subfields(DraftUpdates).items()
    )

    enum_lines = "\n".join(
        f"  {path:<28} → {' | '.join(e.value for e in enum_cls)}"
        for path, enum_cls in _FIELD_ENUM_MAP.items()
    )

    return (
        "You are a data extraction assistant for insurance underwriting intake.\n\n"
        "Extract applicant field values from the user's message. Only populate fields the user explicitly stated in the current message.\n"
        "Do not invent values or guess beyond what is clearly provided.\n"
        'Set user_confirmed=true ONLY when the user explicitly confirms a complete applicant summary (for example: "yes", "confirm", "submit").\n\n'
        "Return exactly one valid JSON object with these fields:\n"
        "  - updates: object\n"
        "  - user_confirmed: boolean\n\n"
        "If no applicant fields were provided, return:\n"
        '  {"updates": {}, "user_confirmed": false}\n\n'
        "Use these exact field names when populating updates:\n"
        f"{top_fields}\n\n"
        "CRITICAL: nested fields (contact_info, location_address, coverage_request, risk_profile) MUST be nested objects in the JSON — never place their subfields at the top level of updates.\n"
        "IMPORTANT: The user's email address belongs strictly inside 'contact_info' under the key 'email'. Do not look for a top-level email field.\n"
        "If the user provides nested contact, address, coverage, or risk data, include only the relevant nested keys:\n"
        f"{nested_hints}\n\n"
        "Valid enum values:\n"
        f"{enum_lines}\n\n"
        "Example 1:\n"
        '{"updates": {"applicant_name": "Smith Family Trust"}, "user_confirmed": false}\n\n'
        "Example 2:\n"
        '{"updates": {"policy_type": "HOME"}, "user_confirmed": false}\n\n'
        "Example 3:\n"
        '{"updates": {"declared_value": "$450,000"}, "user_confirmed": false}\n\n'
        "Example 4 (nested contact_info — email MUST be nested under contact_info):\n"
        '{"updates": {"contact_info": {"email": "applicant@example.com"}}, "user_confirmed": false}\n\n'
        "Example 5 (nested location_address):\n"
        '{"updates": {"location_address": {"line1": "123 Oak St", "city": "Austin", "state_or_province_code": "TX", "postal_code": "78701", "country_code": "US"}}, "user_confirmed": false}\n\n'
        "Example 6 (nested coverage_request):\n"
        '{"updates": {"coverage_request": {"coverage_amount": "$300,000", "deductible": "$2,500"}}, "user_confirmed": false}\n\n'
        "Example 7 (nested risk_profile):\n"
        '{"updates": {"risk_profile": {"property_type": "single_family", "year_built": "1998", "prior_claims_count": "1"}}, "user_confirmed": false}\n\n'
        "Example 8 (user confirms):\n"
        '{"updates": {}, "user_confirmed": true}\n'
    )


extraction_system_prompt = build_extraction_system_prompt()


def reply_system_prompt(draft: dict, phase: str, validation_error: Optional[str]) -> str:
    draft_str = json.dumps(draft, indent=2) if draft else "(nothing collected yet)"
    missing = [f for f in INTAKE_REQUIRED_LABELS if not draft.get(f)]

    if phase == "collecting":
        if missing:
            need = "\n".join(f"  • {INTAKE_REQUIRED_LABELS[f]}" for f in missing)
            directive = (
                f"Still required:\n{need}\n\n"
                "Ask for the NEXT missing field only. Be natural and brief. "
                "Do NOT ask for all fields at once."
            )
        else:
            directive = (
                f"Draft invalid: {validation_error}\n\n"
                "One field value is invalid. Ask the user only for the corrected value. "
                "Use the exact allowed enum values when responding. "
                "Do NOT say the draft is complete until the corrected value is provided."
            )
    elif phase == "offering_optional":
        # No optional fields in insurance intake — go straight to confirmation summary.
        directive = (
            "All required fields are now collected. Present the summary as '* Field: value' "
            "bullet lines, then ask the user to confirm by replying 'yes' or 'confirm', "
            "or to say what they want to change. Do NOT say the request is being submitted yet."
        )
    else:  # confirming
        directive = (
            "Summarize the collected fields as '* Field: value' bullet lines, one per line. "
            "Then ask the user to confirm by replying 'yes' or 'confirm', or to say what they want to change. "
            "Do NOT say the request is being submitted yet."
        )

    return f"""You are an insurance underwriting intake assistant.

Current draft:
{draft_str}

{directive}

Respond in plain conversational text only. No JSON, no markdown code fences."""


def build_create_applicant_prompt(draft: dict) -> str:
    return (
        "Create a new insurance applicant using the create_applicant tool.\n\n"
        "Use this JSON for the applicant_json parameter:\n"
        f"{json.dumps(draft, indent=2)}\n\n"
        "Call the tool and tell me the applicant_id that was assigned."
    )
