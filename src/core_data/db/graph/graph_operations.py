"""Graph-based hierarchy and analytics using Neo4j."""

import threading
from typing import List, Dict, Any, Optional, Callable, TypeVar

from core_data.db.graph.neo4j_client import get_neo4j_client
from mltek.common.log.logging_config import setup_structured_logging

logger = setup_structured_logging(__name__)

T = TypeVar("T")


class GraphOperations:

    def __init__(self):
        self.neo4j = get_neo4j_client()

    def is_available(self) -> bool:
        return self.neo4j.is_connected()

    def _run(self, fn: Callable[[], T], default: T) -> T:
        """Run fn if Neo4j is connected; return default on unavailability or error."""
        if not self.neo4j.is_connected():
            return default
        try:
            return fn()
        except Exception as e:
            logger.error(f"Graph operation failed: {e}")
            return default





_graph_operations: Optional[GraphOperations] = None
_graph_operations_lock = threading.Lock()


def get_graph_operations() -> GraphOperations:
    global _graph_operations
    if _graph_operations is None:
        with _graph_operations_lock:
            if _graph_operations is None:
                _graph_operations = GraphOperations()
    return _graph_operations
