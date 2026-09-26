"""Bold font registration for ReportLab and fontTools.

The labels are drawn with Arial Bold when it can be found, because its cap
height ratio matches :data:`~vinyllabels.layout.CAP_HEIGHT_RATIO`. When it is
absent the code falls back to ReportLab's built-in Helvetica-Bold, which needs
no file — but then ink area has to be estimated instead of integrated.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont as RLTTFont

__all__ = [
    "ARIAL_BOLD_CANDIDATES",
    "ARIAL_BOLD_NAME",
    "FALLBACK_FONT_NAME",
    "register_bold_font",
]

ARIAL_BOLD_NAME: str = "ArialBold"
FALLBACK_FONT_NAME: str = "Helvetica-Bold"

# Common locations for Arial Bold on macOS. Probed in order.
ARIAL_BOLD_CANDIDATES: tuple[str, ...] = (
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Arial Bold.ttf",
)


def register_bold_font(preferred_path: Path | None = None) -> tuple[str, str | None]:
    """Register bold Arial from disk if available.

    ``preferred_path`` (from ``--font``) is used exclusively when given;
    otherwise the standard macOS locations are probed in order.

    Returns ``(reportlab_font_name, font_path)``. ``font_path`` is the absolute
    path to the TTF file (needed by ``fontTools`` to compute ink area via
    ``AreaPen``); it is ``None`` when the fallback font is used, in which case
    ink area is estimated from the natural width instead.
    """
    if preferred_path is not None:
        candidate = str(preferred_path)
        if not Path(candidate).exists():
            raise SystemExit(f"Font file not found: {preferred_path}")
        try:
            pdfmetrics.registerFont(RLTTFont(ARIAL_BOLD_NAME, candidate))
        except Exception as exc:
            raise SystemExit(
                f"Could not register font {preferred_path}: {exc}"
            ) from exc
        return ARIAL_BOLD_NAME, candidate

    for candidate in ARIAL_BOLD_CANDIDATES:
        if Path(candidate).exists():
            try:
                pdfmetrics.registerFont(RLTTFont(ARIAL_BOLD_NAME, candidate))
                return ARIAL_BOLD_NAME, candidate
            except Exception:
                continue
    return FALLBACK_FONT_NAME, None
