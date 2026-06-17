"""JSON-driven LangGraph StateGraph builder.

Separates topology (what connects to what) from behaviour (what each node does).
The JSON config declares nodes, edges, and conditional routes by name.
The caller supplies Python callables bound to those names via registries.

Usage:
    graph = GraphBuilder.from_file(
        path="workflow_graph_config.json",
        state_schema=MyState,
        nodes={"initiate": my_init_fn, ...},
        routers={"route_after_webhook": my_router_fn, ...},
        checkpointer=MySaver(),
    ).build()

JSON schema
───────────
{
  "graph": "<name>",

  "nodes": [
    {"name": "<node_name>", "handler": "<key in nodes registry>"}
  ],

  "edges": [
    {"from": "<node|__start__>", "to": "<node|__end__>"}
  ],

  "conditional_edges": [
    {
      "from": "<source_node>",
      "router": "<key in routers registry>",
      "routes": {"<router_return_value>": "<target_node|__end__>"}
    }
  ]
}
"""

from pathlib import Path
from typing import Any, Callable, Dict, Optional, Type

from mltek.common.agents.bridge import END, START, StateGraph, CompiledStateGraph
from mltek.common.infra import load_config
from mltek.common.log import setup_structured_logging

logger = setup_structured_logging(__name__)

_SENTINEL_START = "__start__"
_SENTINEL_END   = "__end__"


class GraphBuilder:
    """Assembles a compiled LangGraph from a JSON topology config and two
    plain-Python registries: one for node handlers, one for edge routers.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        state_schema: Type,
        nodes: Dict[str, Callable],
        routers: Dict[str, Callable],
        checkpointer: Optional[Any] = None,
    ) -> None:
        self._config = config
        self._state_schema = state_schema
        self._nodes = nodes
        self._routers = routers
        self._checkpointer = checkpointer

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        state_schema: Type,
        nodes: Dict[str, Callable],
        routers: Dict[str, Callable],
        checkpointer: Optional[Any] = None,
    ) -> "GraphBuilder":
        config = load_config(path)
        return cls(
            config=config,
            state_schema=state_schema,
            nodes=nodes,
            routers=routers,
            checkpointer=checkpointer,
        )

    def build(self) -> CompiledStateGraph:
        builder = StateGraph(self._state_schema)
        graph_name = self._config.get("graph", "unnamed")

        registered_nodes = set()
        for node_cfg in self._config.get("nodes", []):
            name    = node_cfg["name"]
            handler = node_cfg["handler"]
            if handler not in self._nodes:
                raise KeyError(f"[{graph_name}] node handler '{handler}' not in registry")
            registered_nodes.add(name)
            builder.add_node(name, self._nodes[handler])

        for edge in self._config.get("edges", []):
            src = START if edge["from"] == _SENTINEL_START else edge["from"]
            dst = END   if edge["to"]   == _SENTINEL_END   else edge["to"]
            if src not in (START, END) and src not in registered_nodes:
                raise KeyError(f"[{graph_name}] edge source '{src}' not found in node registry")
            if dst not in (START, END) and dst not in registered_nodes:
                raise KeyError(f"[{graph_name}] edge target '{dst}' not found in node registry")
            builder.add_edge(src, dst)

        for cond in self._config.get("conditional_edges", []):
            router_key = cond["router"]
            if router_key not in self._routers:
                raise KeyError(f"[{graph_name}] router '{router_key}' not in registry")
            source = cond["from"]
            if source not in registered_nodes:
                raise KeyError(f"[{graph_name}] conditional edge source '{source}' not found in node registry")
            routes = {}
            for k, v in cond["routes"].items():
                target = END if v == _SENTINEL_END else v
                if target not in (START, END) and target not in registered_nodes:
                    raise KeyError(f"[{graph_name}] conditional edge target '{target}' not found in node registry")
                routes[k] = target
            builder.add_conditional_edges(source, self._routers[router_key], routes)

        logger.debug("Graph compiled from JSON config", extra={"graph": graph_name})
        return builder.compile(checkpointer=self._checkpointer)
