"""
Handles tool fetching and splitting by capability.
"""

from typing import List
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from mltek.config import config
from mltek.common.log import setup_structured_logging
from mltek.common.infra import CorrelationContext
import concurrent.futures
from functools import lru_cache
from pathlib import Path

logger = setup_structured_logging(__name__)

_RESOLVER_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "resolver_config.yaml"


@lru_cache(maxsize=1)
def _load_agent_tools() -> dict[str, list[str]]:
    """Load tool lists keyed by capability from resolver_config.yaml. Cached after first load."""
    from mltek.common.infra import load_config
    cfg = load_config(_RESOLVER_CONFIG_PATH)
    result: dict[str, list[str]] = {}
    for node in cfg.get("graph", {}).get("nodes", []):
        cap = node.get("capability")
        tools = node.get("tools", [])
        if cap:
            result[cap] = tools
    guardian_tools = cfg.get("guardian", {}).get("tool_names", [])
    if guardian_tools:
        result["guardian"] = guardian_tools
    return result


class _SyncMCPClientAdapter:
    """Sync adapter around `MultiServerMCPClient` providing `list_tools_sync()`.

    This connects to the same MCP endpoint used by other helpers via
    `config.get_mcp_client_config()` and exposes a synchronous
    `list_tools_sync()` that runs the async `get_tools()` in a thread.
    """
    def __init__(self, client_config: dict):
        self._client_config = client_config

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def list_tools_sync(self):
        """Synchronously fetch tools from MCP by running async client in a thread."""
        def _run():
            import asyncio
            client = MultiServerMCPClient(self._client_config)
            return asyncio.run(client.get_tools())

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_run)
            return fut.result()


def build_mcp_client(db_path: str | None = None, mcp_command: str = "uvx") -> _SyncMCPClientAdapter:
    """Return an MCP client adapter that connects to the configured MCP server.

    If `db_path` is provided, this helper will still return an adapter that
    connects to the running MCP server (consistent with `get_tools()`), but
    callers that want to launch a local stdio-backed MCP server should call
    `MCPClient` directly.
    """
    # Always connect to the configured MCP server so behavior matches other helpers
    client_config = config.get_mcp_client_config()
    return _SyncMCPClientAdapter(client_config)


async def _fetch_from_server(server_name: str, server_cfg: dict) -> List[BaseTool]:
    """Fetch tools from a single MCP server. Raises on failure."""
    client = MultiServerMCPClient({server_name: server_cfg})
    return await client.get_tools()


async def get_tools(env: str = "prod") -> List[BaseTool]:
    """Fetch tools from all MCP servers, tolerating partial server failures.

    Connects to each server independently so that one unavailable server
    (e.g. semantic or workflow) does not block tools from the others.
    Only raises if every server fails.
    """
    correlation_id = CorrelationContext.get() or ""
    client_config = config.get_mcp_client_config(env=env)

    all_tools: List[BaseTool] = []
    errors: list[str] = []

    for server_name, server_cfg in client_config.items():
        try:
            tools = await _fetch_from_server(server_name, server_cfg)
            all_tools.extend(tools)
            logger.debug("[%s] %s: fetched %d tools", correlation_id, server_name, len(tools))
        except Exception as e:
            cause = e
            if hasattr(e, "exceptions") and e.exceptions:
                cause = e.exceptions[0]
            msg = str(cause)[:200]
            errors.append(f"{server_name}: {msg}")
            logger.warning(
                "[%s] Failed to fetch tools from %s: %s",
                correlation_id, server_name, msg,
                extra={"correlation_id": correlation_id},
            )

    if all_tools:
        if errors:
            logger.warning(
                "[%s] Partial tool fetch — unavailable servers: %s",
                correlation_id, "; ".join(errors),
                extra={"correlation_id": correlation_id},
            )
        logger.info("[%s] Fetched %d tools total", correlation_id, len(all_tools))
        return all_tools

    # Every server failed — raise with the combined errors
    raise RuntimeError(
        f"Failed to fetch tools from MCP server {config.MCP_CORE_URL}: "
        f"{'; '.join(errors)}\nCorrelation ID: {correlation_id}"
    )


def get_mcp_server_adapter_tools():
    """Context manager for MCPServerAdapter tool connection.
    
    Returns:
        MCPServerAdapter context manager
    """
    from crewai_tools import MCPServerAdapter
    
    server_params = {
        "url": config.MCP_CORE_URL,
        "transport": "streamable-http"
    }
    
    return MCPServerAdapter(server_params)


def tools_list_by_capability(tools: List[BaseTool]) -> dict[str, List[BaseTool]]:
    """Split tools into capability categories.

    Returns:
        Dict with keys: admin, merchant, workflow, guardian, semantic, general
    """
    agent_tools = _load_agent_tools()
    logger.debug("All tools received from MCP: %s", [t.name for t in tools])

    known: set[str] = set()
    for tool_names in agent_tools.values():
        known.update(tool_names)

    result = {
        cap: [t for t in tools if t.name in set(tool_names)]
        for cap, tool_names in agent_tools.items()
    }
    result["general"] = [t for t in tools if t.name not in known]
    logger.debug("Admin tools assigned: %s", [t.name for t in result.get("admin", [])])
    return result


def _extract_tool_params(tool) -> dict:
    """Extract parameter schema from too's args_schema or input_schema."""
    try:
        if hasattr(tool, "args_schema"):
            return tool.args_schema.model_json_schema()
    except Exception:
        pass

    try:
        if hasattr(tool, "input_schema"):
            return tool.input_schema
    except Exception:
        pass

    return {"type": "object", "properties": {}}


async def get_tool_schemas() -> tuple[list[dict], list[dict]]:
    """Fetch MCP tools and return (boarding_tool_schema, general_schemas).

    Extracts full parameter schemas from each tool's args_schema (LangChain-style)
    or input_schema (raw MCP tools) so the LLM receives proper property definitions
    and can construct valid tool calls.
    """
    try:
        raw_tools = await get_tools()
        schemas = []

        for t in raw_tools:
            params = _extract_tool_params(t)
            tool_schema = {
                "name": getattr(t, "name", "unnamed"),
                "description": getattr(t, "description", ""),
                "parameters": params,
            }
            if "required" in params:
                tool_schema["parameters"]["required"] = params["required"]
            schemas.append(tool_schema)

        admin_tools = set(_load_agent_tools().get("admin", []))
        boarding_tool_schema = [s for s in schemas if s["name"] in admin_tools]
        general_schemas = [s for s in schemas if s["name"] not in admin_tools]
        return boarding_tool_schema, general_schemas
    except Exception:
        return [], []


async def strands_mcp_tools():
    """
    Connects to the MCP server via HTTP and retrieves all available tools.
    """
    # 1. Initialize the client using your config URL
    # lambda is used as a factory for the transport connection
    from strands.tools.mcp import MCPClient
    from mcp.client.streamable_http import streamable_http_client
    import asyncio
    mcp_url = config.get_mcp_client_config()["http_tools_server"]["url"]
    
    mcp_client = MCPClient(lambda: streamable_http_client(mcp_url))

    # Using 'async with' handles the start/stop lifecycle automatically
    try:
        with mcp_client:
            return await mcp_client.load_tools()
    except Exception as e:
        # If already running, skip the 'with' and just load tools
        if "running" in str(e):
            return await mcp_client.load_tools()
        raise


        )