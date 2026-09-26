"""Ink area and horizontal text compression.

Ink area is the real printed ink footprint of a label's text, computed by
integrating the TrueType glyph outlines. It drives both the ink-usage metrics
and the ink line of the cost model, so it is kept in one place.
"""

from __future__ import annotations

from fontTools.pens.areaPen import AreaPen
from fontTools.ttLib import TTFont as FTTTFont
from reportlab.pdfbase import pdfmetrics

__all__ = ["horizontal_scale", "text_ink_area"]

# Typical ink fill factor for bold sans-serif glyphs (ink area / bbox area).
# Used as a fallback when the TTF path is unavailable.
_FALLBACK_INK_FILL_FACTOR: float = 0.45
# Average glyph advance as a fraction of cap height for bold sans-serif.
_FALLBACK_AVG_ADVANCE_RATIO: float = 0.60

_PT_PER_IN: float = 72.0


def text_ink_area(
    text: str, font_path: str | None, font_size_pt: float, cap_height_ratio: float
) -> float:
    """Return the ink area (sq inches) of ``text`` rendered at ``font_size_pt``.

    Uses ``fontTools.pens.areaPen.AreaPen`` (Green's theorem path integration)
    when a TTF path is available. Falls back to a bounding-box estimate when
    only the ReportLab fallback font is registered.
    """
    if font_path is None:
        cap_height_pt = font_size_pt * cap_height_ratio
        est_width_pt = len(text) * cap_height_pt * _FALLBACK_AVG_ADVANCE_RATIO
        est_area_sq_pt = est_width_pt * cap_height_pt * _FALLBACK_INK_FILL_FACTOR
        return est_area_sq_pt / (_PT_PER_IN * _PT_PER_IN)

    font = FTTTFont(font_path)
    try:
        cmap = font.getBestCmap()
        glyph_set = font.getGlyphSet()
        units_per_em: float = float(font["head"].unitsPerEm)
        total_area_font_units_sq: float = 0.0
        for ch in text:
            cp = ord(ch)
            if cmap is None or cp not in cmap:
                continue
            pen = AreaPen(glyph_set)
            glyph_set[cmap[cp]].draw(pen)
            total_area_font_units_sq += abs(float(pen.value))
        # Convert font units² → em² → pt² → in²
        scale_pt_per_unit = font_size_pt / units_per_em
        return total_area_font_units_sq * (scale_pt_per_unit / _PT_PER_IN) ** 2
    finally:
        font.close()


def horizontal_scale(
    text: str, font_name: str, text_area_w_in: float, font_size_pt: float
) -> float:
    """Return the horizontal scale factor for ``text`` at ``font_size_pt``.

    Text wider than ``text_area_w_in`` is compressed to fit, never enlarged, so
    the result is always at most 1.0. Mirrors the compression applied when
    drawing so the metrics report and the PDF agree on each label's factor.
    """
    natural_w_pt = pdfmetrics.stringWidth(text, font_name, font_size_pt)
    natural_w_in = natural_w_pt / _PT_PER_IN
    if natural_w_in > 0:
        return min(1.0, text_area_w_in / natural_w_in)
    return 1.0
