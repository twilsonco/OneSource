"""Color parsing for label drawing options.

Accepts the same three spellings everywhere a color is configurable: a preset
name, ``r,g,b`` (optionally parenthesised) with components in ``[0, 255]``, or
six bare hex digits.

Spot colors for cut-ready PDF generation (Roland VersaWorks integration) are
provided as factory functions using ReportLab's CMYKColorSep with spotName.
"""

from __future__ import annotations

from reportlab.lib.colors import CMYKColor, CMYKColorSep, Color
from reportlab.lib.colors import (
    blue,
    green,
    orange,
    red,
)
from reportlab.lib.colors import HexColor as RLHexColor

__all__ = [
    "PRESET_COLOR_NAMES",
    "cutcontour_spot_color",
    "parse_color",
    "perfcutcontour_spot_color",
]

# Preset color names mapping to reportlab colors.
# CMYK process primaries emit as CMYK operators in PDF for correct color reproduction.
_PRESET_COLORS: dict[str, Color] = {
    "cyan": CMYKColor(1, 0, 0, 0),
    "c": CMYKColor(1, 0, 0, 0),
    "magenta": CMYKColor(0, 1, 0, 0),
    "m": CMYKColor(0, 1, 0, 0),
    "yellow": CMYKColor(0, 0, 1, 0),
    "y": CMYKColor(0, 0, 1, 0),
    "black": CMYKColor(0, 0, 0, 1),
    "k": CMYKColor(0, 0, 0, 1),
    "red": red,
    "r": red,
    "green": green,
    "g": green,
    "blue": blue,
    "b": blue,
    "orange": orange,
    "o": orange,
    "violet": RLHexColor("#8B00FF"),  # violet (not in standard reportlab)
    "v": RLHexColor("#8B00FF"),
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


def cutcontour_spot_color() -> CMYKColorSep:
    """Return a CutContour spot color for Roland VersaWorks plotter integration.

    CutContour is a standard spot color name recognized by RIP software for
    kiss-cuts (partial cuts that don't go through the backing material). The
    color is hidden from the printer's ink nozzles and routed directly to the
    plotter blade. This is implemented using ReportLab's CMYKColorSep with
    Magenta CMYK (0, 1, 0, 0) as the visual fallback if the RIP software
    doesn't recognize the spot name.

    Returns a new CMYKColorSep object on each call.
    """
    return CMYKColorSep(cyan=0, magenta=1, yellow=0, black=0, spotName="CutContour")


def perfcutcontour_spot_color() -> CMYKColorSep:
    """Return a PerfCutContour spot color for perforated/through cuts.

    PerfCutContour is a spot color name for perforated cuts (full cuts through
    backing material). Like CutContour, it is routed to the plotter blade and
    hidden from ink nozzles. This is implemented using ReportLab's CMYKColorSep
    with Yellow CMYK (0, 0, 1, 0) as the visual fallback.

    Returns a new CMYKColorSep object on each call.
    """
    return CMYKColorSep(cyan=0, magenta=0, yellow=1, black=0, spotName="PerfCutContour")
