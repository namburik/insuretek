import re
from typing import Any

from shared.config import config


def bedrock_inline_context_to_human(messages: list) -> list:
    """Bedrock-only: convert injected SystemMessages into a <context> prefix on the
    following HumanMessage.

    ChatBedrockConverse extracts every SystemMessage from the messages list and
    merges it into Bedrock's system parameter. When an inline metadata-context
    SystemMessage is merged with the agent's own system prompt, Amazon Nova Pro
    sometimes echoes the combined system content as its reply instead of answering
    the user. Converting the metadata block to a tagged HumanMessage prefix keeps
    it out of the system parameter so Nova Pro treats it as user-provided context.

    No-op for non-Bedrock providers.
    """
    if not config.is_using_bedrock():
        return messages

    from langchain_core.messages import SystemMessage, HumanMessage

    result: list = []
    pending: list[str] = []

    for msg in messages:
        if isinstance(msg, SystemMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            pending.append(content)
        elif isinstance(msg, HumanMessage) and pending:
            ctx_block = "\n\n".join(pending)
            pending = []
            human_content = msg.content if isinstance(msg.content, str) else str(msg.content)
            result.append(HumanMessage(content=f"<context>\n{ctx_block}\n</context>\n\n{human_content}"))
        else:
            if pending:
                from langchain_core.messages import SystemMessage as _SM
                result.extend(_SM(content=c) for c in pending)
                pending = []
            result.append(msg)

    if pending:
        from langchain_core.messages import SystemMessage as _SM
        result.extend(_SM(content=c) for c in pending)

    return result


# Known single-line metadata keys Google prepends before the text: payload.
# Each entry is a lowercase prefix without prefix stars.
_METADATA_LINE_PREFIXES = {"id", "type", "index", "name", "usage"}

# Matches a LangChain content-block ID line followed by a text: line anywhere
# in the string.  Google sometimes prefixes the block with its own labels
# Pattern: "id: lc_<uuid>\ntext:\n" with optional surrounding whitespace.
_LC_BLOCK_RE = re.compile(
    r"\bid:\s+lc_[0-9a-f\-]+\s*\ntext:\s*\n?",
    re.IGNORECASE,
)

# Strip <think>…</think> blocks that reasoning models embed in string content
# when the Ollama `reasoning` flag is not honoured (belt-and-suspenders guard).
_THINK_TAG_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


def _strip_metadata_line(line: str) -> bool:
    candidate = line.strip()
    match = re.match(r"^\*{0,2}([a-z_]+)\*{0,2}\s*:", candidate.lower())
    return bool(match and match.group(1) in _METADATA_LINE_PREFIXES)


def unwrap_provider_text(text: str) -> str:
    """Strip provider wrapper metadata from tool response text.

    Handles Google patterns such as:
    - ``text: actual payload``
    - ``**text** actual payload``
    - ``id: <uuid>\\ntext:\\nactual payload``
    - ``type: text\\ntext: actual payload``
    - ``id: <uuid>\\ntype: text\\ntext:\\nactual payload``
    - plain text from Ollama (no-op)
    """
    if not text:
        return ""

    # Preserve leading whitespace so that streaming tokens like " got" keep
    # their word-boundary space.  Only discard it when we actually strip a
    # metadata prefix or wrapper.
    leading = text[: len(text) - len(text.lstrip())]
    text = text.lstrip()

    if not text:
        # Preserve whitespace-only tokens such as standalone '\n' emitted by
        # Google's streaming — dropping them collapses multi-line replies.
        return leading

    # Remove any <think>…</think> blocks before further processing.
    text = _THINK_TAG_RE.sub("", text).lstrip()
    if not text:
        return ""

    # Strip any leading metadata lines (id:, type:, index:, …) one at a time.
    # Also handle starred variants like **id**: and **type**:.
    lines = text.splitlines(True)
    stripped_metadata = False
    while lines:
        if _strip_metadata_line(lines[0]):
            lines = lines[1:]
            text = "".join(lines).lstrip()
            stripped_metadata = True
            continue
        break

    if not text:
        return ""

    # Google Gemini sometimes prefixes the LangChain content block with its own
    # the "id: lc_<uuid>\ntext:\n" block header.  The leading-line loop above
    # stops at those labels, leaving the block header buried in the text.
    # Detect that pattern and extract only what comes after "text:\n".
    m = _LC_BLOCK_RE.search(text)
    if m:
        after = text[m.end():]
        return _strip_text_prefix(after.lstrip("\n"))

    # Handle wrappers like **text** and text:
    result = _strip_text_prefix(text)

    # Nothing was stripped — this is a plain token; restore the leading space.
    if not stripped_metadata and result == text:
        return leading + result
    return result


def _strip_text_prefix(text: str) -> str:
    text = text.lstrip()
    if text.lower().startswith("**text**"):
        stripped = text[len("**text**"):].lstrip()
        if stripped.startswith(":"):
            stripped = stripped[1:].lstrip()
        return _strip_text_prefix(stripped)

    if text.lower().startswith("text:"):
        stripped = text[len("text:"):].lstrip()
        # If the remaining text starts with a newline, consume it too
        if stripped.startswith("\n"):
            stripped = stripped[1:]
        return _strip_text_prefix(stripped)

    return text


_SKIP_BLOCK_TYPES = {"tool_use", "tool_result", "image", "image_url", "function_call", "thinking", "executable_code", "code_execution_result"}


def normalize_stream_chunk(raw: Any) -> str:
    """Normalize a streaming chunk according to the active provider.

    Ollama: raw is a plain string.
    Google/Anthropic: raw is a list of typed content blocks; only "text" blocks
    are yielded — tool_use, tool_result, image, etc. are silently dropped so
    they never bleed into the streamed reply.
    """
    if raw is None:
        return ""

    if isinstance(raw, str):
        return unwrap_provider_text(raw)

    if isinstance(raw, list):
        parts = []
        for item in raw:
            if isinstance(item, dict) and item.get("type") in _SKIP_BLOCK_TYPES:
                continue
            parts.append(normalize_stream_chunk(item))
        return "".join(parts)

    if isinstance(raw, dict):
        block_type = raw.get("type")
        if block_type in _SKIP_BLOCK_TYPES:
            return ""
        if "text" in raw and isinstance(raw["text"], str):
            return unwrap_provider_text(raw["text"])
        if "content" in raw:
            return normalize_stream_chunk(raw["content"])
        return ""

    return unwrap_provider_text(str(raw))


def normalize_llm_content(content: Any) -> str:
    """Normalize LLM response content to a plain string across all providers.

    Ollama / Google: content is already a str.
    Bedrock (Nova): ChatBedrockConverse returns content as a list of typed blocks,
    e.g. [{"type": "text", "text": "..."}].  Callers that do ``result.content``
    and then call ``.strip()`` will raise AttributeError for Bedrock without this
    normalization, causing the caller to silently fall back to a default value.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", "") or "")
            else:
                parts.append(str(block))
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


def sanitize_tool_schema_for_bedrock(schema: dict) -> dict:
    """Flatten Pydantic-generated JSON Schema constructs that Bedrock rejects.

    Bedrock's Converse API tool inputSchema does not support:
      - ``$defs`` / ``$ref`` — reference resolution
      - ``anyOf`` / ``oneOf`` — union types
      - ``allOf`` — intersection types

    This function recursively resolves ``$ref``s against ``$defs``, then drops
    the unsupported keywords and replaces them with their first concrete
    alternative (for anyOf/oneOf) or with the merged type (for allOf).

    No-op for non-Bedrock providers.
    """
    if not config.is_using_bedrock():
        return schema

    import copy
    schema = copy.deepcopy(schema)
    defs = schema.pop("$defs", {})
    return _sanitize_node(schema, defs)


def _sanitize_node(node: Any, defs: dict) -> Any:
    if not isinstance(node, dict):
        return node

    # Resolve $ref first
    if "$ref" in node:
        ref_path = node["$ref"]
        # Only handle local #/$defs/<name> references
        if ref_path.startswith("#/$defs/"):
            def_name = ref_path[len("#/$defs/"):]
            resolved = _sanitize_node(dict(defs.get(def_name, {})), defs)
            extras = {k: v for k, v in node.items() if k != "$ref"}
            resolved.update(extras)
            return resolved
        node.pop("$ref")

    # Flatten anyOf / oneOf — pick the first non-null concrete alternative
    for key in ("anyOf", "oneOf"):
        if key in node:
            alternatives = node.pop(key)
            chosen = next(
                (a for a in alternatives if isinstance(a, dict) and a.get("type") != "null"),
                alternatives[0] if alternatives else {},
            )
            chosen = _sanitize_node(chosen, defs)
            node.update(chosen)

    # Flatten allOf — merge all alternatives into the current node
    if "allOf" in node:
        for part in node.pop("allOf"):
            node.update(_sanitize_node(part, defs))

    # Recurse into properties
    if "properties" in node:
        node["properties"] = {
            k: _sanitize_node(v, defs) for k, v in node["properties"].items()
        }

    # Recurse into array items
    if "items" in node:
        node["items"] = _sanitize_node(node["items"], defs)

    # Remove keywords Bedrock doesn't understand
    for key in ("$defs", "$schema", "title", "default", "examples"):
        node.pop(key, None)

    return node


def _object_or_string_type() -> list[str]:
    types = ["object", "null"]
    if config.is_using_google():
        types.insert(0, "string")
    return types


def applicant_json_property(schema: dict) -> dict:
    if config.is_using_google():
        return {
            "type": ["object", "string"],
            "description": "Complete applicant intake data as an object or JSON string.",
            "properties": schema["properties"],
        }
    return {
        "oneOf": [
            {**schema},
            {
                "type": "string",
                "description": "Complete applicant intake data as JSON string",
            },
        ]
    }
