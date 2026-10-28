"""SO-101 step animations (rendered from the official MuJoCo model by tools/render_illustrations.py).

Each clip is an animated WebP with transparency in `so101/`. Steps name the clip they want through
`Step.illustration()`; front ends load it here.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources

FPS = 12


def clip_names() -> list[str]:
    folder = resources.files(__package__) / "so101"
    return sorted(p.name.removesuffix(".webp") for p in folder.iterdir() if p.name.endswith(".webp"))


@lru_cache(maxsize=32)
def frames(name: str, size: tuple[int, int] | None = None) -> tuple:
    """(RGBA image, duration in ms) for every frame of a clip, scaled to fit `size` (w, h) keeping the
    aspect ratio.

    Returns () when the clip does not exist, so a front end can simply show nothing.
    """
    from PIL import Image, ImageSequence

    path = resources.files(__package__) / "so101" / f"{name}.webp"
    if not path.is_file():
        return ()
    with resources.as_file(path) as p, Image.open(p) as im:
        out = []
        for f in ImageSequence.Iterator(im):
            duration = int(f.info.get("duration") or 1000 / FPS)
            f = f.convert("RGBA")
            if size:
                f.thumbnail(size, Image.LANCZOS)
            out.append((f, duration))
    return tuple(out)
