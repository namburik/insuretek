"""Backward-compatibility shim for shared.common.

The real implementation now lives in shared.common.
"""

from pathlib import Path

__path__.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "common"))
