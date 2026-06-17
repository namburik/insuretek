"""Backward-compatibility shim for dbtek.config.

The real implementation now lives in shared.config.
"""

from pathlib import Path

__path__.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "config"))

from mltek.config.config_common import Config, config  # noqa: E402
