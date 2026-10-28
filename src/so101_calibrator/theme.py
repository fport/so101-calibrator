"""Colours. One dark theme, two accents' worth of meaning: orange = what to do now, grey = everything else."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    bg: str
    surface: str  # fills, empty bar track
    dim: str  # secondary text, the parts of the arm you do not touch
    fg: str  # primary text
    accent: str  # the joint to move, the key to press, progress
    error: str  # failed checks only

    def sprite(self, kind: str = "step") -> dict[str, str | None]:
        """Colour of every sprite role (see assets.ROLES). 'step': grey arm, orange joint to move.
        'hero': the same with a bright outline (home screen)."""
        bright = kind == "hero"
        return {
            "empty": None,
            "part": self.surface,
            "part_dot": self.dim,
            "motor": self.bg,
            "motor_dot": self.dim,
            "hi": self.accent,
            "hi_dot": self.bg,
            "hi_motor": self.accent,
            "hi_motor_dot": self.bg,
            "outline": self.fg if bright else self.dim,
            "separator": self.bg,
        }


GRAPHITE = Theme(bg="#0b0b0b", surface="#1c1c1c", dim="#5e5e5e", fg="#ededed", accent="#ff7a1a", error="#ff5f56")
