"""Shared MCP tool result formatting."""

import json
import re
from dataclasses import is_dataclass, asdict
from decimal import Decimal
from enum import Enum

from mcp.types import TextContent


def _serialize(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return value.__dict__
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _scrub_timestamps(value):
    if isinstance(value, dict):
        return {
            k: _scrub_timestamps(v)
            for k, v in value.items()
            if k not in {"created_at", "created_date", "updated_at", "completed_at"}
        }
    if isinstance(value, list):
        return [_scrub_timestamps(item) for item in value]
    return value


def format_tool_result(result: object | None, indent: int | None = 4) -> list[TextContent]:
    if isinstance(result, TextContent):
        return [result]
    if isinstance(result, list) and all(isinstance(item, TextContent) for item in result):
        return result
    if isinstance(result, str):
        return [TextContent(type="text", text=result)]

    sanitized = _scrub_timestamps(result) if result is not None else {"error": "not found"}
    text = json.dumps(sanitized, indent=indent, sort_keys=True, default=_serialize)
    return [TextContent(type="text", text=text)]


def format_text_result(text: str | None) -> list[TextContent]:
    """Wrap a pre-formatted string as a TextContent list for MCP responses."""
    return [TextContent(type="text", text=text or '{"error": "not found"}')]


_TEXT_FIELD_PRIORITIES = [
    "text",
    "content",
    "message",
    "response",
    "reply",
    "output",
    "result",
]


def _is_blank(value) -> bool:
    """Return True for values that contribute nothing to readable output."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (dict, list)):
        return len(value) == 0
    return False


def format_json_as_text(parsed, indent: int = 0) -> str:
    spacing = " " * indent
    if isinstance(parsed, dict):
        lines = []
        for key, value in parsed.items():
            if _is_blank(value):
                continue
            if isinstance(value, (dict, list)):
                body = format_json_as_text(value, indent + 2)
                if body.strip():
                    lines.append(f"{spacing}{key}:")
                    lines.append(body)
                # skip the key entirely when the nested section renders empty
            else:
                lines.append(f"{spacing}{key}: {value}")
        return "\n".join(lines)

    if isinstance(parsed, list):
        if not parsed:
            return ""
        lines = []
        for idx, item in enumerate(parsed):
            if _is_blank(item):
                continue
            if isinstance(item, (dict, list)):
                body = format_json_as_text(item, indent + 2)
                if body.strip():
                    lines.append(f"{spacing}item {idx + 1}:")
                    lines.append(body)
            else:
                lines.append(f"{spacing}- {item}")
        return "\n".join(lines)

    return str(parsed)


def _extract_text_from_json(parsed) -> str:
    if isinstance(parsed, str):
        return normalize_tool_response_text(parsed)

    if isinstance(parsed, dict):
        for key in _TEXT_FIELD_PRIORITIES:
            if key in parsed:
                return normalize_tool_response_text(parsed[key])

        # If the dict is a single-key wrapper (e.g. {"merchant": {...}}), flatten it:
        # format the inner value directly so the wrapper label is not emitted.
        non_blank = {k: v for k, v in parsed.items() if not _is_blank(v)}
        if len(non_blank) == 1:
            sole_value = next(iter(non_blank.values()))
            if isinstance(sole_value, (dict, list)):
                return format_json_as_text(sole_value)

        return format_json_as_text(parsed)

    if isinstance(parsed, list):
        if parsed and all(isinstance(item, dict) for item in parsed):
            return json.dumps(parsed, ensure_ascii=False)
        extracted_items = [
            _extract_text_from_json(item)
            for item in parsed
            if isinstance(item, (str, dict, list))
        ]
        extracted_items = [item for item in extracted_items if item]
        return "\n".join(extracted_items)

    return str(parsed)


def _parse_json_text(text: str):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # Attempt to fix common invalid JSON issues like trailing commas.
    cleaned = re.sub(r",(\s*[}\]])", r"\1", text)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # Try to find the first embedded JSON object/array and parse that.
    decoder = json.JSONDecoder()
    for start_char in ("{", "["):
        start = text.find(start_char)
        while start != -1:
            try:
                obj, idx = decoder.raw_decode(text, start)
                return obj
            except json.JSONDecodeError:
                start = text.find(start_char, start + 1)
    return None


_CODE_FENCE_RE  = re.compile(r"^```(?:\w+)?\n?([\s\S]*?)```\s*$", re.MULTILINE)
_BOLD_KEY_RE    = re.compile(r"^\*{1,2}([^*\n]+?)\*{1,2}\s*:", re.MULTILINE)
_MD_HEADER_RE   = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)
_BULLET_LINE_RE = re.compile(r"^\s*[-*]\s+", re.MULTILINE)
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def _strip_code_fence(text: str) -> str | None:
    """Return the content inside a markdown code fence, or None if not fenced."""
    m = _CODE_FENCE_RE.match(text.strip())
    return m.group(1).strip() if m else None


def _normalize_google_text(text: str) -> str:
    """Strip Google Gemini markdown patterns that break key: value grid rendering."""
    text = _BOLD_KEY_RE.sub(r"\1:", text)       # **key**: → key:
    text = _MD_HEADER_RE.sub("", text)           # ## Headers → removed
    text = _BULLET_LINE_RE.sub("", text)         # - bullet prefix → removed
    text = _MULTI_BLANK_RE.sub("\n\n", text)     # 3+ blank lines → one blank line
    return text.strip()


def normalize_tool_response_text(text: str | object) -> str:
    """Normalize raw tool/provider output into clean text.

    This handles:
    - Google wrapper prefixes such as **text** / text:
    - Markdown code fences (e.g. ```json ... ```) emitted by Google Gemini
    - Nested JSON payloads that contain the actual text under common keys
    - Lists of text or object values emitted by providers
    """
    from mltek.common.infra.vendor import unwrap_provider_text

    if text is None:
        return ""
    if isinstance(text, (dict, list)):
        return _extract_text_from_json(text)
    if not isinstance(text, str):
        text = str(text)

    text = unwrap_provider_text(text).strip()
    if not text:
        return ""

    # Strip markdown code fences before JSON-checking (Google Gemini often wraps
    # responses in ```json ... ``` which would otherwise bypass the JSON parser).
    if text.startswith("```"):
        inner = _strip_code_fence(text)
        if inner is not None:
            return normalize_tool_response_text(inner)

    if text.startswith("{") or text.startswith("["):
        parsed = _parse_json_text(text)
        if parsed is not None:
            return _normalize_google_text(_extract_text_from_json(parsed))

    return _normalize_google_text(text)

