"""SO-101 pixel-art clips, rendered from the official MuJoCo model by tools/render_illustrations.py.

A clip is a paletted PNG sheet (frames stacked vertically) whose pixel values are *roles*, not colours;
a theme maps roles to colours (tui.theme.Theme.sprite). Each clip exists in three sizes: "xl", "l" and "s".
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

ROLES = ("empty", "part", "part_dot", "motor", "motor_dot", "hi", "hi_dot", "hi_motor", "hi_motor_dot",
         "outline", "separator")
SIZES = ("xl", "l", "s")  # largest first


def _dir():
    return resources.files(__package__) / "so101"


@lru_cache(maxsize=1)
def index() -> dict:
    """clip -> {"fps": int, "frames": int, "xl"/"l"/"s": [w, h]}"""
    return json.loads((_dir() / "index.json").read_text())


def clip_names() -> list[str]:
    return sorted(index())


@lru_cache(maxsize=64)
def frames(name: str, size: str) -> tuple[tuple[bytes, ...], int, int]:
    """(frames as rows of role indices, width, height); ((), 0, 0) for an unknown clip."""
    from PIL import Image

    info = index().get(name)
    if not info:
        return (), 0, 0
    w, h = info[size]
    with resources.as_file(_dir() / f"{name}-{size}.png") as p, Image.open(p) as sheet:
        data = sheet.tobytes()  # mode "P": one byte (role index) per pixel
    rows = tuple(data[i * w:(i + 1) * w] for i in range(len(data) // w))
    return tuple(b"".join(rows[k * h:(k + 1) * h]) for k in range(info["frames"])), w, h


def best_size(name: str, max_w: int, max_h: int) -> str | None:
    """The largest size of a clip that fits in max_w x max_h pixels (None if none does)."""
    info = index().get(name)
    if not info:
        return None
    return next((s for s in SIZES if info[s][0] <= max_w and info[s][1] <= max_h), None)
