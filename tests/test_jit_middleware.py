from __future__ import annotations
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from langchain_core.messages import ToolMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mltek.common.infra.context import set_env, set_jit_session
from mltek.policy.jit_elevation_policy import JitElevationPolicy
from mltek.common.agents.jit_middleware import JitPrivilegeMiddleware

def _make_request(name, args={}):
    r = MagicMock()
    r.tool_call = {"name": name, "args": args, "id": "tid-1"}
    return r

async def test_unrestricted_passes_through():
    mw = JitPrivilegeMiddleware()
    handler = AsyncMock(return_value=ToolMessage(content="ok", tool_call_id="tid-1"))
    result = await mw.awrap_tool_call(_make_request("get_merchant"), handler)
    assert handler.called
    assert "[JIT" not in result.content

async def test_restricted_collecting_denied():
    set_jit_session("collecting", "sess-1")
    set_env("prod")
    mw = JitPrivilegeMiddleware()
    result = await mw.awrap_tool_call(_make_request("create_merchant"), AsyncMock())
    assert "[JIT_DENIED]" in result.content

async def test_restricted_confirming_executes_immediately():
    set_jit_session("confirming", "sess-2")
    set_env("prod")
    mw = JitPrivilegeMiddleware()
    handler = AsyncMock(return_value=ToolMessage(content="created", tool_call_id="tid-1"))

    result = await mw.awrap_tool_call(_make_request("create_merchant"), handler)

    assert handler.called
    assert "[JIT" not in result.content
    assert result.content == "created"

async def test_shadow_always_denied():
    set_jit_session("confirming", "sess-3")
    set_env("shadow")
    mw = JitPrivilegeMiddleware()
    result = await mw.awrap_tool_call(_make_request("create_merchant"), AsyncMock())
    assert "[JIT_DENIED]" in result.content
    assert "shadow" in result.content

if __name__ == "__main__":
    for fn in [test_unrestricted_passes_through, test_restricted_collecting_denied,
               test_restricted_confirming_granted_then_consumed, test_shadow_always_denied]:
        asyncio.run(fn())
        print(f"PASS  {fn.__name__}")
