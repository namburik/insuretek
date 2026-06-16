from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from workflow_engine.repository.dynamodb.workflow_checkpointer import _normalize_checkpoint_metadata


def test_normalize_checkpoint_metadata_converts_decimal_to_native_types():
    metadata = {
        "source": "loop",
        "step": Decimal("1"),
        "parents": {
            "input": "abc",
            "child_step": Decimal("2"),
        },
        "numbers": [Decimal("3"), Decimal("4.5")],
    }

    normalized = _normalize_checkpoint_metadata(metadata)

    assert normalized["step"] == 1
    assert isinstance(normalized["step"], int)
    assert normalized["parents"]["child_step"] == 2
    assert isinstance(normalized["parents"]["child_step"], int)
    assert normalized["numbers"] == [3, 4.5]
    assert isinstance(normalized["numbers"][0], int)
    assert isinstance(normalized["numbers"][1], float)
