"""Label size helpers.

A label size is its ``(width, height)`` design dimensions in inches. Vertical
labels keep the *unrotated* design size so counts consolidate across
orientations.
"""

from __future__ import annotations

from collections import Counter

__all__ = ["LabelSize", "first_label_size", "format_label_size", "label_sizes_to_json"]

LabelSize = tuple[float, float]


def format_label_size(size: LabelSize) -> str:
    """Render a ``(width_in, height_in)`` pair as e.g. ``8x3``."""
    width_in, height_in = size
    return f"{width_in:g}x{height_in:g}"


def label_sizes_to_json(sizes: Counter[LabelSize]) -> list[dict[str, float | int]]:
    """Convert a :data:`LabelSize` counter to a JSON-friendly list sorted by size."""
    return [
        {"width_in": width_in, "height_in": height_in, "labels": count}
        for (width_in, height_in), count in sorted(sizes.items())
    ]


def first_label_size(sizes: Counter[LabelSize]) -> LabelSize | None:
    """Return the smallest size in ``sizes``, or ``None`` when empty.

    A job prints a single design size, so this is how callers recover "the"
    size of a job from its size counter; sorting makes the choice stable.
    """
    for size in sorted(sizes):
        return size
    return None
