"""Unified config-file loader: resolves format from file extension."""

import json
from pathlib import Path


def load_config(path: str | Path) -> dict:
    """Load a JSON or YAML config file, dispatching by extension.

    Raises:
        FileNotFoundError: if the file does not exist.
        ValueError: if the file cannot be parsed.
    """
    path = Path(path)
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise FileNotFoundError(f"Config file not found: {path}")

    if path.suffix in (".yaml", ".yml"):
        import yaml
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in {path}: {e}")

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {path}: {e}")
