"""OutputAdapter — normalize tool/agent results to multiple formats.

Consolidates scattered result-handling logic:
- extract_result_content() → to_text()
- _summarize_result() → to_summary()
- Raw structured data → to_dict()
"""

from typing import Any


class OutputAdapter:
    """Normalize results from tools, agents, and policies to standard formats."""

    def __init__(self, result: Any):
        self.result = result
        self.is_dict = isinstance(result, dict)
        self.is_message_format = self.is_dict and "messages" in result

    def to_dict(self) -> dict:
        """Return result as dict (or empty dict if not dict-like)."""
        if self.is_dict:
            return self.result
        if hasattr(self.result, "__dict__"):
            return self.result.__dict__
        return {}

    def to_summary(self, max_keys: int = 10) -> str:
        """Compact summary for logging/monitoring."""
        if self.is_dict:
            keys = list(self.result.keys())
            if len(keys) > max_keys:
                keys = keys[:max_keys] + ["..."]
            summary = ["{...dict...}", f"keys={keys}"]

            messages = self.result.get("messages")
            if isinstance(messages, list):
                summary.append(f"messages={len(messages)}")

            metadata = self.result.get("metadata")
            if isinstance(metadata, dict):
                summary.append(f"metadata_domain={metadata.get('domain', 'unknown')}")

            return " ".join(summary)

        if hasattr(self.result, "content"):
            return str(self.result.content)[:100]
        return str(self.result)[:100]

    def to_text(self, dominant: str = "ai") -> str:
        """Extract readable content string from result.

        Args:
            dominant: "ai" (prefer last assistant message) or "tool" (prefer last tool message)

        Returns:
            Extracted text content or empty string if not found.
        """
        if not self.is_message_format:
            if hasattr(self.result, "content"):
                return str(self.result.content)
            return str(self.result) if self.result else ""

        messages = self.result.get("messages", [])
        if not messages:
            return ""

        # Filter by message type
        filtered = [
            (m, getattr(m, "content", ""))
            for m in messages
            if hasattr(m, "type") and m.type not in ("system", "human")
            and getattr(m, "content", "")
        ]

        if not filtered:
            return ""

        if dominant == "tool":
            # Prefer last tool message
            tool_msgs = [(m, c) for m, c in filtered if hasattr(m, "type") and m.type == "tool"]
            if tool_msgs:
                return str(tool_msgs[-1][1])

        # Default: prefer last AI/assistant message
        return str(filtered[-1][1])

    def to_list(self) -> list:
        """Return result as list if applicable."""
        if isinstance(self.result, list):
            return self.result
        if self.is_dict:
            # For dicts with "items" or "results" key
            for key in ("items", "results", "data"):
                if key in self.result and isinstance(self.result[key], list):
                    return self.result[key]
        return []

    def get_field(self, key: str, default: Any = None) -> Any:
        """Safe field access for dict results."""
        if self.is_dict:
            return self.result.get(key, default)
        if hasattr(self.result, key):
            return getattr(self.result, key, default)
        return default

    def has_messages(self) -> bool:
        """Check if result has message format."""
        return self.is_message_format

    def message_count(self) -> int:
        """Count of messages if in message format."""
        if self.is_message_format:
            return len(self.result.get("messages", []))
        return 0
