"""The reattachment band, read from config and never inlined (ADR-024).

Two numbers, and the space between them is the whole of ADR-013 step 4:

    score >= REATTACH_ACCEPT       attach silently — this is the sentence it belongs to
    REATTACH_FLAG_FLOOR <= score   attach, but FLAG it: probably right, check it
                       < ACCEPT
    score <  REATTACH_FLAG_FLOOR   propose nothing. The anchor is surfaced as a user
                                   decision: keep here / move to… / remove

Collapsing the middle band into either neighbour is the failure this file exists to
prevent. Fold it upwards and uncertain placements go in silently; fold it downwards and
every slightly-reworded sentence becomes a modal dialogue the user learns to click
through. Neither is HR-5 being enforced — one is it being skipped, the other is it being
performed.

The values themselves live in `app/core/config.py` and are hypotheses to be swept against
T1's golden set. This module reads them; it does not own them, and it must never grow a
literal of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReattachmentBand:
    """`REATTACH_ACCEPT` and `REATTACH_FLAG_FLOOR`, carried together.

    Together because they are only meaningful as a pair: a threshold on its own cannot
    express "attached but uncertain", and that state is the one the user needs to see.
    """

    accept: float
    flag_floor: float

    def __post_init__(self) -> None:
        if self.flag_floor > self.accept:
            raise ValueError(
                f"reattachment flag floor {self.flag_floor} is above the accept threshold "
                f"{self.accept}, which leaves no band in which an anchor is attached-but-flagged"
            )

    @classmethod
    def from_settings(cls, settings: Any | None = None) -> ReattachmentBand:
        """The production band. `app/core/config.py` is the only source of these numbers."""
        if settings is None:
            from app.core.config import get_settings  # noqa: PLC0415 — must raise on missing keys

            settings = get_settings()
        return cls(accept=settings.reattach_accept, flag_floor=settings.reattach_flag_floor)

    def verdict_for(self, score: float | None) -> str:
        """`"accept"`, `"flag"` or `"surface"` for one similarity score."""
        if score is None:
            return "surface"
        if score >= self.accept:
            return "accept"
        if score >= self.flag_floor:
            return "flag"
        return "surface"


__all__ = ["ReattachmentBand"]
