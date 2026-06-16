from pathlib import Path
from workflow_engine.config.gate_check_config import GATE_CHECK_ENDPOINTS

GRAPH_CONFIG_PATH = Path(__file__).parent / "workflow_graph_config.json"

__all__ = ["GATE_CHECK_ENDPOINTS", "GRAPH_CONFIG_PATH"]
