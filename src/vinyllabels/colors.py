"""Color parsing for label drawing options.

Accepts the same three spellings everywhere a color is configurable: a preset
name, ``r,g,b`` (optionally parenthesised) with components in ``[0, 255]``, or
six bare hex digits.
"""

from __future__ import annotations

from reportlab.lib.colors import Color
from reportlab.lib.colors import HexColor as RLHexColor

__all__ = ["PRESET_COLOR_NAMES", "parse_color"]

# Preset color names mapping to reportlab colors (defined with precise hex codes).
_PRESET_COLORS: dict[str, Color] = {
    "cyan": RLHexColor("#00FFFF"),
    "c": RLHexColor("#00FFFF"),
    "magenta": RLHexColor("#FF00FF"),
    "m": RLHexColor("#FF00FF"),
    "yellow": RLHexColor("#FFFF00"),
    "y": RLHexColor("#FFFF00"),
    "black": RLHexColor("#000000"),
    "k": RLHexColor("#000000"),
    "red": RLHexColor("#FF0000"),
    "r": RLHexColor("#FF0000"),
    "green": RLHexColor("#00FF00"),
    "g": RLHexColor("#00FF00"),
    "blue": RLHexColor("#0000FF"),
    "b": RLHexColor("#0000FF"),
    "orange": RLHexColor("#FFA500"),
    "o": RLHexColor("#FFA500"),
    "violet": RLHexColor("#7F00FF"),
    "v": RLHexColor("#7F00FF"),
}

#: Preset names, for building ``argparse`` help text.
PRESET_COLOR_NAMES: str = (
    "black/k, blue/b, green/g, red/r, orange/o, yellow/y, violet/v, magenta/m"
)


def parse_color(color_str: str) -> Color:
    """Parse a color specification into a reportlab color object.

    Accepts:

    - Preset names: black, k, blue, b, green, g, red, r, orange, o, yellow, y,
      violet, v, magenta, m
    - RGB format: ``r,g,b`` or ``(r,g,b)`` where r,g,b are ints in [0,255]
    - Hex format: ``aabbcc`` (6 hex digits, no number-sign)

    Raises ``ValueError`` if the color specification is invalid.
    """
    if color_str in _PRESET_COLORS:
        return _PRESET_COLORS[color_str]

    # Try RGB format: r,g,b or (r,g,b). Strip optional parentheses first.
    rgb_candidate = color_str
    if rgb_candidate.startswith("(") and rgb_candidate.endswith(")"):
        rgb_candidate = rgb_candidate[1:-1]

    if "," in rgb_candidate:
        try:
            parts = [p.strip() for p in rgb_candidate.split(",")]
            if len(parts) != 3:
                raise ValueError(
                    f"RGB color must have 3 components, got {len(parts)}: {color_str}"
                )
            r, g, b = (int(part) for part in parts)
            if not all(0 <= val <= 255 for val in (r, g, b)):
                raise ValueError(
                    f"RGB values must be in [0, 255], got r={r}, g={g}, b={b}"
                )
            return RLHexColor(f"#{r:02x}{g:02x}{b:02x}")
        except ValueError as exc:
            raise ValueError(f"Invalid RGB color format {color_str}: {exc}") from exc

    # Try hex format: aabbcc (6 hex digits, no number-sign).
    try:
        if len(color_str) != 6:
            raise ValueError(
                f"Hex color must be 6 digits (got {len(color_str)}): {color_str}"
            )
        # RLHexColor raises ValueError on invalid hex.
        return RLHexColor(f"#{color_str}")
    except ValueError as exc:
        raise ValueError(f"Invalid hex color format {color_str}: {exc}") from exc
