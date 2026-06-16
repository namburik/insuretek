"""Backward-compatibility shim for workflow.infra.

The real implementation now lives in shared.infra.
"""

from pathlib import Path

__path__.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "infra"))
