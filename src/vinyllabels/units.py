"""Unit conversion helpers shared across the label tools.

Internal areas are tracked in square inches and lengths in inches; only the
cost model and the reports speak in square feet, so conversion happens here at
the boundary.
"""

from __future__ import annotations

__all__ = ["SQ_IN_PER_SQ_FT", "sq_ft", "with_sq_ft"]

SQ_IN_PER_SQ_FT: float = 144.0


def sq_ft(sq_in: float) -> float:
    """Convert an area from square inches to square feet."""
    return sq_in / SQ_IN_PER_SQ_FT


def with_sq_ft(values: dict[str, object]) -> dict[str, object]:
    """Return ``values`` with a ``*_sq_ft`` sibling for every ``*_sq_in`` entry.

    Keeps the JSON reports in lockstep with the text reports, which show both
    units; consumers can read whichever they prefer.
    """
    extended = dict(values)
    for key, value in values.items():
        if key.endswith("_sq_in") and isinstance(value, int | float):
            extended[f"{key[: -len('in')]}ft"] = sq_ft(float(value))
    return extended
