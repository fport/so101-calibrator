"""User preferences kept next to the calibration history: <store>/config.json (assistant, language)."""

from __future__ import annotations

import json
import os
from pathlib import Path


def path(store_root: Path) -> Path:
    return Path(store_root) / "config.json"


def load(store_root: Path) -> dict:
    p = path(store_root)
    try:
        data = json.loads(p.read_text()) if p.exists() else {}
    except json.JSONDecodeError:
        return {}  # a broken preferences file must never stop the tool
    return data if isinstance(data, dict) else {}


def update(store_root: Path, **values) -> None:
    """Set (or, with None, remove) preferences; written atomically."""
    data = load(store_root)
    for k, v in values.items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = v
    p = path(store_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, p)
