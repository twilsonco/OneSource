"""Lay out WIC labels (8in × 3in) on a 52in-wide print page and emit a PDF.

Reads a list of label codes from a text file (one per line; ``#`` introduces a
comment) and produces a continuous PDF suitable for printing and cutting,
alongside a metrics text report covering material yield and ink usage.

Layout summary (from the job's data file header):
    - Each label is 8in × 3in with 1/2in margins on all sides; hairline border.
    - Text is 2in tall bold Arial, compressed horizontally if needed.
    - Page is 52in wide with 1in margins on all four sides.
    - Each label is printed twice (2X).
    - Labels are organized into "sheets" of 3 columns × 8 rows (24 labels).
      Adjacent labels within a sheet share left/right and top/bottom edges.
    - Sheets are placed in row-major order: sheet 0 top-left, sheet 1
      top-right, sheet 2 below sheet 0, sheet 3 below sheet 1, and so on.
      Only two sheets fit side-by-side per sheet-row on a 52in page; the
      horizontal gap between them is derived from the page width.
    - Sheet rows stack vertically, separated by VERTICAL_GAP_IN.

Usage::

    uv run python scripts/wic_label_layout.py -i "data/2027-7-2 WIC.txt"
    uv run python scripts/wic_label_layout.py -i input.txt -o out/labels.pdf
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from fontTools.pens.areaPen import AreaPen
from fontTools.ttLib import TTFont as FTTTFont
from reportlab.lib.colors import black
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont as RLTTFont
from reportlab.pdfgen.canvas import Canvas

# --- Geometry (inches) --------------------------------------------------------

PAGE_W_IN: float = 52.0
PAGE_LEFT_MARGIN_IN: float = 1.0
PAGE_RIGHT_MARGIN_IN: float = 1.0
PAGE_TOP_MARGIN_IN: float = 1.0
PAGE_BOTTOM_MARGIN_IN: float = 1.0

LABEL_W_IN: float = 8.0
LABEL_H_IN: float = 3.0
LABEL_H_MARGIN_IN: float = 0.25  # left/right margin inside each label
LABEL_V_MARGIN_IN: float = 0.5  # top/bottom margin inside each label

# A sheet is a self-contained block of labels (3 cols × 8 rows), no internal gaps.
LABELS_PER_SHEET_ROW: int = 3
LABELS_PER_SHEET_COL: int = 8
SHEET_W_IN: float = LABEL_W_IN * LABELS_PER_SHEET_ROW  # 24in
SHEET_H_IN: float = LABEL_H_IN * LABELS_PER_SHEET_COL  # 24in
LABELS_PER_SHEET: int = LABELS_PER_SHEET_ROW * LABELS_PER_SHEET_COL  # 24

# How many sheets fit side-by-side per sheet-row, and the gap between them.
# For the default 52in page with 1in margins: 2 sheets × 24in = 48in, leaving 2in.
SHEETS_PER_ROW: int = int(
    (PAGE_W_IN - PAGE_LEFT_MARGIN_IN - PAGE_RIGHT_MARGIN_IN) // SHEET_W_IN
)
HORIZONTAL_GAP_IN: float = (
    PAGE_W_IN - PAGE_LEFT_MARGIN_IN - PAGE_RIGHT_MARGIN_IN - SHEETS_PER_ROW * SHEET_W_IN
)
VERTICAL_GAP_IN: float = 2.0  # gap between sheet rows

COPIES_PER_LABEL: int = 2

# Hairline border drawn around each label. Set to False to disable.
DRAW_BORDER: bool = True
BORDER_LINE_WIDTH_PT: float = 0.5  # ~0.5pt is the standard "hairline" weight

TEXT_HEIGHT_IN: float = 2.0  # cap height of the label text

# Cap-height-to-font-size ratio: Arial Bold reports HHeight = 728/1000 (0.728),
# Helvetica Bold is 718/1000 (0.718). We pick the larger so the cap height
# never exceeds TEXT_HEIGHT_IN; with Arial this yields a true 2.00in cap,
# with Helvetica it lands at ~2.01in.
CAP_HEIGHT_RATIO: float = 0.728
FONT_SIZE_PT: float = TEXT_HEIGHT_IN * 72.0 / CAP_HEIGHT_RATIO  # ~197.8pt

# --- Font registration -------------------------------------------------------

ARIAL_BOLD_NAME: str = "ArialBold"
FALLBACK_FONT_NAME: str = "Helvetica-Bold"

# Common locations for Arial Bold on macOS. Probed in order.
ARIAL_BOLD_CANDIDATES: tuple[str, ...] = (
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Arial Bold.ttf",
)


def _register_bold_font() -> tuple[str, str | None]:
    """Register bold Arial from disk if available.

    Returns ``(reportlab_font_name, font_path)``. ``font_path`` is the absolute
    path to the TTF file (needed by ``fontTools`` to compute ink area via
    ``AreaPen``); it is ``None`` when the fallback font is used, in which case
    ink area is estimated from the natural width instead.
    """
    for candidate in ARIAL_BOLD_CANDIDATES:
        if Path(candidate).exists():
            try:
                pdfmetrics.registerFont(RLTTFont(ARIAL_BOLD_NAME, candidate))
                return ARIAL_BOLD_NAME, candidate
            except Exception:
                continue
    return FALLBACK_FONT_NAME, None


# --- Parsing ------------------------------------------------------------------


def parse_labels(path: Path) -> list[str]:
    """Return the non-empty, non-comment label codes from ``path``."""
    labels: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        labels.append(line)
    return labels


# --- Layout helpers -----------------------------------------------------------


def sheet_origin_in(sheet_idx: int, page_h_in: float) -> tuple[float, float]:
    """Return (x_in, y_top_in) of the top-left corner of sheet ``sheet_idx``."""
    sheet_col = sheet_idx % SHEETS_PER_ROW
    sheet_row = sheet_idx // SHEETS_PER_ROW
    x_in = PAGE_LEFT_MARGIN_IN + sheet_col * (SHEET_W_IN + HORIZONTAL_GAP_IN)
    y_top_in = (
        page_h_in - PAGE_TOP_MARGIN_IN - sheet_row * (SHEET_H_IN + VERTICAL_GAP_IN)
    )
    return x_in, y_top_in


def page_height_in(num_instances: int) -> float:
    """Total page height (inches) including top and bottom page margins."""
    n_sheets = -(-num_instances // LABELS_PER_SHEET)  # ceil(num_instances / 24)
    n_sheet_rows = -(-n_sheets // SHEETS_PER_ROW)
    return (
        PAGE_TOP_MARGIN_IN
        + n_sheet_rows * SHEET_H_IN
        + max(0, n_sheet_rows - 1) * VERTICAL_GAP_IN
        + PAGE_BOTTOM_MARGIN_IN
    )


# --- Ink area calculation -----------------------------------------------------

# Typical ink fill factor for bold sans-serif glyphs (ink area / bbox area).
# Used as a fallback when the TTF path is unavailable.
_FALLBACK_INK_FILL_FACTOR: float = 0.45
# Average glyph advance as a fraction of cap height for bold sans-serif.
_FALLBACK_AVG_ADVANCE_RATIO: float = 0.60


def calculate_text_area(text: str, font_path: str | None, font_size_pt: float) -> float:
    """Return the ink area (sq inches) of ``text`` rendered at ``font_size_pt``.

    Uses ``fontTools.pens.areaPen.AreaPen`` (Green's theorem path integration)
    when a TTF path is available. Falls back to a bounding-box estimate when
    only the ReportLab fallback font is registered.
    """
    if font_path is None:
        cap_height_pt = font_size_pt * CAP_HEIGHT_RATIO
        est_width_pt = len(text) * cap_height_pt * _FALLBACK_AVG_ADVANCE_RATIO
        est_area_sq_pt = est_width_pt * cap_height_pt * _FALLBACK_INK_FILL_FACTOR
        return est_area_sq_pt / (72.0 * 72.0)

    font = FTTTFont(font_path)
    try:
        cmap = font.getBestCmap()
        glyph_set = font.getGlyphSet()
        units_per_em: float = float(font["head"].unitsPerEm)
        total_area_font_units_sq: float = 0.0
        for ch in text:
            cp = ord(ch)
            if cp not in cmap:
                continue
            glyph_name = cmap[cp]
            pen = AreaPen(glyph_set)
            glyph_set[glyph_name].draw(pen)
            total_area_font_units_sq += abs(float(pen.value))
        # Convert font units² → em² → pt² → in²
        scale_pt_per_unit = font_size_pt / units_per_em
        area_sq_in = total_area_font_units_sq * (scale_pt_per_unit / 72.0) ** 2
        return area_sq_in
    finally:
        font.close()


def compute_horizontal_scale(text: str, font_name: str) -> float:
    """Return the horizontal scale factor for ``text`` at ``FONT_SIZE_PT``.

    Mirrors the compression logic used by :func:`draw_label` so the metrics
    report and the PDF agree on the exact factor applied to each label.
    """
    text_w_in = LABEL_W_IN - 2 * LABEL_H_MARGIN_IN
    natural_w_pt = pdfmetrics.stringWidth(text, font_name, FONT_SIZE_PT)
    natural_w_in = natural_w_pt / 72.0
    if natural_w_in > 0:
        return min(1.0, text_w_in / natural_w_in)
    return 1.0


# --- Metrics ------------------------------------------------------------------


@dataclass(frozen=True)
class LabelMetrics:
    """Per-unique-label metrics. All areas are for a single instance."""

    text: str
    char_count: int
    horizontal_scale: float
    ink_area_sq_in: float  # already includes the horizontal scale factor


@dataclass(frozen=True)
class GlobalMetrics:
    """Aggregated metrics across all printed instances."""

    total_output_labels: int
    total_characters: int
    total_ink_area_sq_in: float
    total_label_material_sq_in: float
    total_substrate_sq_in: float
    material_yield_pct: float
    average_ink_coverage_pct: float
    page_height_in: float


def calculate_metrics(
    labels: list[str], font_name: str, font_path: str | None
) -> tuple[list[LabelMetrics], GlobalMetrics]:
    """Compute per-label and global metrics for ``labels``.

    The ink area per label is computed at the actual drawn scale (i.e. after
    horizontal compression), so summing it across instances yields the total
    ink area that will be deposited on the substrate.
    """
    per_label: list[LabelMetrics] = []
    total_ink_area = 0.0
    total_characters = 0

    for text in labels:
        scale = compute_horizontal_scale(text, font_name)
        unscaled_area = calculate_text_area(text, font_path, FONT_SIZE_PT)
        scaled_area = unscaled_area * scale
        per_label.append(
            LabelMetrics(
                text=text,
                char_count=len(text),
                horizontal_scale=scale,
                ink_area_sq_in=scaled_area,
            )
        )
        total_ink_area += scaled_area * COPIES_PER_LABEL
        total_characters += len(text) * COPIES_PER_LABEL

    total_output_labels = len(labels) * COPIES_PER_LABEL
    total_label_material = total_output_labels * (LABEL_W_IN * LABEL_H_IN)
    page_h_in = page_height_in(total_output_labels)
    total_substrate = PAGE_W_IN * page_h_in
    material_yield = (total_label_material / total_substrate) * 100.0
    average_ink_coverage = (total_ink_area / total_label_material) * 100.0

    global_metrics = GlobalMetrics(
        total_output_labels=total_output_labels,
        total_characters=total_characters,
        total_ink_area_sq_in=total_ink_area,
        total_label_material_sq_in=total_label_material,
        total_substrate_sq_in=total_substrate,
        material_yield_pct=material_yield,
        average_ink_coverage_pct=average_ink_coverage,
        page_height_in=page_h_in,
    )
    return per_label, global_metrics


# --- Report generation --------------------------------------------------------


def write_metrics_report(
    per_label: list[LabelMetrics],
    global_metrics: GlobalMetrics,
    out_path: Path,
    input_path: Path,
    pdf_path: Path,
) -> None:
    """Write a human-readable metrics report to ``out_path``."""
    from datetime import datetime

    linear_feet = global_metrics.total_substrate_sq_in / PAGE_W_IN / 12.0
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines: list[str] = []
    rule = "=" * 80
    subrule = "-" * 80
    lines.append(rule)
    lines.append("WIC LABEL PRINTING REPORT".center(80))
    lines.append(rule)
    lines.append(f"Generated:        {timestamp}")
    lines.append(f"Input File:       {input_path}")
    lines.append(f"Output PDF:       {pdf_path}")
    lines.append("")
    lines.append(subrule)
    lines.append("GLOBAL PRINTING METRICS")
    lines.append(subrule)
    lines.append(
        f"Total Substrate Required:  {global_metrics.total_substrate_sq_in:>10.2f} sq in"
        f"   ({linear_feet:.2f} linear feet of {PAGE_W_IN:.0f}in roll)"
    )
    lines.append(
        f"Total Label Area:          {global_metrics.total_label_material_sq_in:>10.2f} sq in"
    )
    lines.append(
        f"Material Yield:            {global_metrics.material_yield_pct:>10.2f} %"
    )
    lines.append("")
    lines.append(subrule)
    lines.append("INK USAGE METRICS")
    lines.append(subrule)
    lines.append(
        f"Total Ink Area:            {global_metrics.total_ink_area_sq_in:>10.2f} sq in"
    )
    lines.append(
        f"Average Ink Coverage:      {global_metrics.average_ink_coverage_pct:>10.2f} %"
    )
    lines.append(f"Total Character Count:     {global_metrics.total_characters:>10d}")
    lines.append("")
    lines.append(subrule)
    lines.append("PER-TAG BREAKDOWN")
    lines.append(subrule)
    lines.append(
        f"{'Label Code':<16} | {'Chars':>5} | {'Scale':>8} | {'Ink Area (sq in)':>16}"
    )
    lines.append(f"{'-' * 16}-+-{'-' * 5}-+-{'-' * 8}-+-{'-' * 18}")
    for m in per_label:
        lines.append(
            f"{m.text:<16} | {m.char_count:>5d} | {m.horizontal_scale:>8.3f} | {m.ink_area_sq_in:>16.4f}"
        )
    lines.append(rule)
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


# --- Drawing ------------------------------------------------------------------


def draw_label_border(c: Canvas, x_in: float, y_in: float) -> None:
    """Draw a hairline border around a single label at the given bottom-left (in inches)."""
    c.saveState()
    c.setLineWidth(BORDER_LINE_WIDTH_PT)
    c.setStrokeColor(black)
    c.rect(
        x_in * 72.0, y_in * 72.0, LABEL_W_IN * 72.0, LABEL_H_IN * 72.0, stroke=1, fill=0
    )
    c.restoreState()


def draw_label(
    c: Canvas,
    text: str,
    x_in: float,
    y_in: float,
    font_name: str,
    scale: float,
) -> None:
    """Draw a single label at the given bottom-left position (in inches).

    Within the label, the text is centered horizontally in the 7in-wide text
    area and its baseline sits flush with the inner edge of the bottom margin
    so the 2in cap height fills the 2in text area from bottom margin to top
    margin. ``scale`` is the precomputed horizontal compression factor (see
    :func:`compute_horizontal_scale`) — passing it in keeps the PDF and the
    metrics report in lockstep.
    """
    text_x0_in = x_in + LABEL_H_MARGIN_IN
    text_baseline_in = y_in + LABEL_V_MARGIN_IN
    text_w_in = LABEL_W_IN - 2 * LABEL_H_MARGIN_IN  # 7in when LABEL_H_MARGIN_IN = 0.5

    natural_w_pt = c.stringWidth(text, font_name, FONT_SIZE_PT)
    natural_w_in = natural_w_pt / 72.0
    drawn_w_in = natural_w_in * scale
    text_x_in = text_x0_in + (text_w_in - drawn_w_in) / 2.0  # horizontal center

    c.saveState()
    c.translate(text_x_in * 72.0, text_baseline_in * 72.0)
    c.scale(scale, 1.0)
    c.setFont(font_name, FONT_SIZE_PT)
    c.setFillColor(black)
    c.drawString(0, 0, text)
    c.restoreState()


def build_pdf(
    labels: list[str],
    out_path: Path,
    per_label: list[LabelMetrics],
) -> int:
    """Lay out ``labels`` (each printed COPIES_PER_LABEL times) into sheets and write the PDF.

    ``per_label`` provides the precomputed horizontal scale for each unique
    label so the PDF and the metrics report agree exactly.

    Returns the number of label instances written.
    """
    font_name, _font_path = _register_bold_font()
    scale_by_text = {m.text: m.horizontal_scale for m in per_label}
    instances = [lbl for lbl in labels for _ in range(COPIES_PER_LABEL)]
    h_in = page_height_in(len(instances))

    c = Canvas(str(out_path), pagesize=(PAGE_W_IN * 72.0, h_in * 72.0))

    for idx, label in enumerate(instances):
        sheet_idx, within = divmod(idx, LABELS_PER_SHEET)
        row_in_sheet, col_in_sheet = divmod(within, LABELS_PER_SHEET_ROW)
        sheet_x_in, sheet_y_top_in = sheet_origin_in(sheet_idx, h_in)
        label_x_in = sheet_x_in + col_in_sheet * LABEL_W_IN
        # Labels fill the sheet left-to-right, top-to-bottom.
        label_y_in = sheet_y_top_in - (row_in_sheet + 1) * LABEL_H_IN
        if DRAW_BORDER:
            draw_label_border(c, label_x_in, label_y_in)
        draw_label(c, label, label_x_in, label_y_in, font_name, scale_by_text[label])

    c.showPage()
    c.save()
    return len(instances)


# --- CLI ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Lay out WIC labels on a 52in-wide print page and emit a PDF.",
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        required=True,
        help="Path to the labels text file (one code per line; '#' comments are ignored).",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Path to write the PDF (default: <input>.pdf next to the input).",
    )
    args = parser.parse_args(argv)

    labels = parse_labels(args.input)
    if not labels:
        raise SystemExit(f"No labels found in {args.input}")

    out_path = args.output or args.input.with_suffix(".pdf")
    report_path = out_path.with_name(f"{out_path.stem}_report.txt")

    font_name, font_path = _register_bold_font()
    per_label, global_metrics = calculate_metrics(labels, font_name, font_path)
    n = build_pdf(labels, out_path, per_label)
    write_metrics_report(per_label, global_metrics, report_path, args.input, out_path)
    print(
        f"Wrote {n} label instances "
        f"({len(labels)} unique x {COPIES_PER_LABEL}) to {out_path}\n"
        f"Wrote metrics report to {report_path}"
    )


if __name__ == "__main__":
    main()
