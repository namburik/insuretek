"""Shared constants for agent creation and configuration.

Domain-specific prompts and agent mappings are provided by the domain system
via DomainConfigProvider. This module contains only generic utilities.
"""


# ── Shared prompt fragments (reusable across domains) ────────────────────────

_TOOL_USE_ONLY = (
    "TOOL USE ONLY: Never write or generate code — always call a tool to fetch or act on data.\n\n"
)

_SCHEMA = (
    "SCHEMA: Only use parameters defined in the inputSchema. "
    "Do not invent names; respect required vs optional; match types exactly.\n\n"
)

_ONE_TOOL = (
    "ONE TOOL CALL: Call exactly one tool per request. "
    "Do NOT call another tool after receiving the result.\n\n"
)

_OUTPUT = (
    "OUTPUT: Present the tool result as flat key: value lines, one field per line. "
    "Write each field directly without wrapper keys. "
    "Skip any field whose value is null, empty, or not set. "
    "Do not use JSON, code blocks, or any structured data format. "
    "On error, quote the full error and stop."
)

_OUTPUT_ADMIN = (
    "OUTPUT: Confirm the action with the exact result keyword (created/updated/deleted/bound/cancelled), "
    "then list only the key result fields as plain text key: value lines. "
    "Do not dump the full response. On error, quote the full error and stop. "
    "Do not add closing remarks, follow-up offers, email promises, or any phrase beyond the confirmation and fields."
)

_OUTPUT_GUARDIAN = (
    "OUTPUT: Respond with a single JSON object only — "
    "keys: allowed (bool), risk_level ('LOW'|'MEDIUM'|'HIGH'), reason (str, one sentence). "
    "No explanation, no markdown, no extra text."
)

_OUTPUT_STRUCTURED = (
    "OUTPUT: Present results as key: value lines, one field per line. "
    "For a single entity, write all fields at the top level. "
    "For multiple results, use indented nested format — one labelled block per item. "
    "Skip null or empty fields. "
    "Do not use JSON, code blocks, or any structured data format. "
    "On error, quote the full error and stop."
)

