"""Generic post-agent tool-output synthesis policy base."""

from __future__ import annotations

import asyncio
from typing import Any

from mltek.common.infra.context import with_token_phase
from mltek.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)


class SynthesisPolicyBase:
    """Scan agent result messages for registered tool names and dispatch to domain synthesis.

    Subclasses implement `_run_synthesis` to perform the actual LLM calls and
    result assembly. This base handles message scanning, content extraction, and
    the token-phase context.
    """

    def __init__(
        self,
        narrative_registry: dict[str, str],
        evidence_registry: dict[str, str] | None = None,
    ) -> None:
        self._narrative_registry = narrative_registry
        self._evidence_registry = evidence_registry or {}

    @staticmethod
    def _is_tool_msg(m: Any) -> bool:
        return hasattr(m, "tool_call_id") or "tool" in type(m).__name__.lower()

    def applies(self, result: dict) -> bool:
        """Return True if a registered, non-error tool message is present."""
        if not self._narrative_registry:
            return False
        return any(
            self._is_tool_msg(m)
            and getattr(m, "name", None) in self._narrative_registry
            and getattr(m, "status", "success") != "error"
            for m in result.get("messages", [])
        )

    @with_token_phase("synthesize")
    async def synthesize(self, result: dict) -> dict:
        msgs = result.get("messages", [])
        tool_msg = next(
            (
                m for m in reversed(msgs)
                if self._is_tool_msg(m)
                and getattr(m, "name", None) in self._narrative_registry
                and getattr(m, "status", "success") != "error"
            ),
            None,
        )
        if tool_msg is None:
            return result

        tool_name: str = getattr(tool_msg, "name")
        user_request: str = next(
            (getattr(m, "content", "") for m in reversed(msgs)
             if "human" in type(m).__name__.lower()),
            "",
        )
        content = tool_msg.content
        if isinstance(content, list):
            content = "\n".join(
                b.get("text", "") if isinstance(b, dict) else str(b) for b in content
            )
        return await self._run_synthesis(result, tool_name, user_request, content.strip())

    async def _run_synthesis(
        self,
        result: dict,
        tool_name: str,
        user_request: str,
        raw_context: str,
    ) -> dict:
        raise NotImplementedError
