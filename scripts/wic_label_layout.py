"""Lay out WIC labels (8in × 3in) on a 52in-wide print page and emit a PDF.

Reads a list of label codes from a text file (one per line; ``#`` introduces a
comment) and produces a continuous PDF suitable for printing and cutting,
alongside a metrics text report (plus a machine-readable JSON sibling with the
same data, for consolidating totals across jobs) covering material yield and
ink usage.

Layout summary (from the job's data file header):
    - Each label is 8in × 3in with 1/2in margins on all sides; hairline border.
    - Text is 2in tall bold Arial, compressed horizontally if needed.
    - Page is 52in wide with 1in margins on all four sides.
    - Each label is printed twice (2X).
    - Labels are organized into "sheets" (by default a single sheet spans the
      page width, filled with as many 8in columns as fit between the margins,
      and 8 rows down). Adjacent labels within a sheet share left/right and
      top/bottom edges.
    - Sheets are placed in row-major order: sheet 0 top-left, sheet 1
      top-right, sheet 2 below sheet 0, sheet 3 below sheet 1, and so on.
      How many sheets fit side-by-side is derived from the page width; the
      horizontal gap between them absorbs the leftover width.
    - Sheet rows stack vertically, separated by VERTICAL_GAP_IN.
    - A sheet size of 0 means "auto": 0 columns (the default) makes a single
      sheet fill the page width with no horizontal gap; 0 rows makes a single
      sheet of unbounded height with no vertical gap.
    - --vertical-labels rotates each label's border and text 90 degrees
      clockwise (text reads top-to-bottom). The label keeps its internal
      design, so --label-width/--label-height keep describing the unrotated
      label and the on-page footprint swaps width/height (8x3 labels become
      3x8 footprints). When both sheet counts are fixed, the grid transposes
      too (a 3x8 grid becomes 8x3); an auto (0) count instead stays on its own
      page axis, so the labels still fill the page width / flow unbounded.

Every value above is only a default: each option constant in this module is
also exposed as an optional command-line flag (see :func:`parse_args`).

Usage::

    uv run python scripts/wic_label_layout.py -i "data/2027-7-2 WIC.txt"
    uv run python scripts/wic_label_layout.py -i input.txt -o out/labels.pdf
    uv run python scripts/wic_label_layout.py -i input.txt --label-height 4 --copies 3
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from fontTools.pens.areaPen import AreaPen
from fontTools.ttLib import TTFont as FTTTFont
from reportlab.lib.colors import black
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont as RLTTFont
from reportlab.pdfgen.canvas import Canvas

# --- Geometry (inches) --------------------------------------------------------

# These constants are the script's defaults; each one can be overridden from
# the command line (see :func:`parse_args`).

PAGE_W_IN: float = 52.0
PAGE_LEFT_MARGIN_IN: float = 1.0
PAGE_RIGHT_MARGIN_IN: float = 1.0
PAGE_TOP_MARGIN_IN: float = 1.0
PAGE_BOTTOM_MARGIN_IN: float = 1.0

LABEL_W_IN: float = 8.0
LABEL_H_IN: float = 3.0
LABEL_H_MARGIN_IN: float = 0.25  # left/right margin inside each label
LABEL_V_MARGIN_IN: float = 0.5  # top/bottom margin inside each label

# When True, each label's border and text are rotated 90 degrees clockwise so
# the text reads top-to-bottom (turn your head clockwise to read it). The label
# keeps its internal design (text area, margins, cap height) and is rotated as
# a unit, so LABEL_W_IN/LABEL_H_IN stay the *design* dimensions used for text
# layout while FOOT_W_IN/FOOT_H_IN are the on-page footprint used for
# positioning: with vertical labels the footprint swaps width/height, and when
# both sheet counts are fixed the grid transposes (columns <-> rows). An auto
# (0) count is a page-space directive and stays on its own axis.
VERTICAL_LABELS: bool = False
FOOT_W_IN: float = LABEL_W_IN  # footprint width (== label height when vertical)
FOOT_H_IN: float = LABEL_H_IN  # footprint height (== label width when vertical)

# A sheet is a self-contained block of labels, no internal gaps. A size of 0
# means "auto": 0 columns makes a single sheet that fills the usable page width
# (no horizontal gap); 0 rows makes a single sheet of unbounded height (no
# vertical gap). These defaults yield one sheet across the page, 8 rows down.
LABELS_PER_SHEET_ROW: int = 0  # columns per sheet (0 = fill page width)
LABELS_PER_SHEET_COL: int = 8  # rows per sheet (0 = single unbounded sheet)


def _resolve_sheet_cols(
    labels_per_sheet_row: int, usable_w_in: float, label_w_in: float
) -> int:
    """Return columns per sheet, expanding the auto value 0 to fill ``usable_w_in``."""
    if labels_per_sheet_row == 0:
        return int(usable_w_in // label_w_in)
    return labels_per_sheet_row


_USABLE_PAGE_W_IN: float = PAGE_W_IN - PAGE_LEFT_MARGIN_IN - PAGE_RIGHT_MARGIN_IN
# SHEET_COLS is the resolved (non-auto) number of label columns per sheet;
# LABELS_PER_SHEET_ROW keeps the raw value so 0 remains detectable.
SHEET_COLS: int = _resolve_sheet_cols(
    LABELS_PER_SHEET_ROW, _USABLE_PAGE_W_IN, LABEL_W_IN
)
SHEET_W_IN: float = LABEL_W_IN * SHEET_COLS
SHEET_H_IN: float = LABEL_H_IN * LABELS_PER_SHEET_COL  # 0 when rows are auto
LABELS_PER_SHEET: int = SHEET_COLS * LABELS_PER_SHEET_COL  # 0 when rows are auto

# How many sheets fit side-by-side per sheet-row, and the gap between them. With
# auto columns there is a single sheet across the page, so the gap is 0.
SHEETS_PER_ROW: int = (
    1 if LABELS_PER_SHEET_ROW == 0 else int(_USABLE_PAGE_W_IN // SHEET_W_IN)
)
HORIZONTAL_GAP_IN: float = (
    0.0
    if LABELS_PER_SHEET_ROW == 0
    else _USABLE_PAGE_W_IN - SHEETS_PER_ROW * SHEET_W_IN
)
VERTICAL_GAP_IN: float = 2.0  # gap between sheet rows (unused when rows are auto)

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

# --- Configuration ------------------------------------------------------------


@dataclass(frozen=True)
class JobConfig:
    """Every tunable option of a print job, as resolved from the command line.

    Field defaults are the option constants above, so ``JobConfig()`` reproduces
    the documented layout exactly. Derived quantities are exposed as properties
    and written back into the module-level constants by
    :func:`apply_layout_config`.
    """

    input_path: Path
    output_path: Path | None = None
    font_path: Path | None = None

    page_w_in: float = PAGE_W_IN
    page_left_margin_in: float = PAGE_LEFT_MARGIN_IN
    page_right_margin_in: float = PAGE_RIGHT_MARGIN_IN
    page_top_margin_in: float = PAGE_TOP_MARGIN_IN
    page_bottom_margin_in: float = PAGE_BOTTOM_MARGIN_IN

    label_w_in: float = LABEL_W_IN
    label_h_in: float = LABEL_H_IN
    label_h_margin_in: float = LABEL_H_MARGIN_IN
    label_v_margin_in: float = LABEL_V_MARGIN_IN

    labels_per_sheet_row: int = LABELS_PER_SHEET_ROW
    labels_per_sheet_col: int = LABELS_PER_SHEET_COL
    vertical_gap_in: float = VERTICAL_GAP_IN

    copies_per_label: int = COPIES_PER_LABEL

    draw_border: bool = DRAW_BORDER
    border_line_width_pt: float = BORDER_LINE_WIDTH_PT

    text_height_in: float = TEXT_HEIGHT_IN
    cap_height_ratio: float = CAP_HEIGHT_RATIO

    vertical_labels: bool = VERTICAL_LABELS

    @property
    def usable_page_w_in(self) -> float:
        """Page width left after the left and right page margins."""
        return self.page_w_in - self.page_left_margin_in - self.page_right_margin_in

    # --- Effective (post-rotation) geometry -----------------------------------
    # ``label_w_in``/``label_h_in`` and the two sheet counts are always the
    # *design* values (they drive text layout, margins and ink area, which do
    # not change under ``--vertical-labels``). The properties below give the
    # on-page footprint and grid used for positioning: with vertical labels the
    # footprint swaps width/height and the grid transposes (across <-> down).

    @property
    def foot_w_in(self) -> float:
        """On-page footprint width (== label height when vertical)."""
        return self.label_h_in if self.vertical_labels else self.label_w_in

    @property
    def foot_h_in(self) -> float:
        """On-page footprint height (== label width when vertical)."""
        return self.label_w_in if self.vertical_labels else self.label_h_in

    @property
    def _transpose_grid(self) -> bool:
        """Whether vertical labels transpose the sheet grid.

        Only fixed counts transpose (they describe a design-space rectangle).
        An auto (0) count is a page-space directive — "fill the page width" or
        "unbounded height" — and stays on its own page axis, so when either
        count is auto the grid is used as given and only the footprint and the
        label content rotate.
        """
        return (
            self.vertical_labels
            and self.labels_per_sheet_row != 0
            and self.labels_per_sheet_col != 0
        )

    @property
    def eff_per_sheet_row(self) -> int:
        """Labels across a sheet-row (raw, may be 0=auto); swapped when transposing."""
        if self._transpose_grid:
            return self.labels_per_sheet_col
        return self.labels_per_sheet_row

    @property
    def eff_per_sheet_col(self) -> int:
        """Labels down a sheet-column (raw, may be 0=auto); swapped when transposing."""
        if self._transpose_grid:
            return self.labels_per_sheet_row
        return self.labels_per_sheet_col

    @property
    def sheet_cols(self) -> int:
        """Resolved label columns per sheet (0 expands to fill the page width)."""
        return _resolve_sheet_cols(
            self.eff_per_sheet_row, self.usable_page_w_in, self.foot_w_in
        )

    @property
    def sheet_w_in(self) -> float:
        return self.foot_w_in * self.sheet_cols

    @property
    def sheet_h_in(self) -> float:
        """Sheet height; 0 when rows are auto (one unbounded vertical sheet)."""
        return self.foot_h_in * self.eff_per_sheet_col

    @property
    def labels_per_sheet(self) -> int:
        """Labels per sheet; 0 when rows are auto (sheets have no fixed size)."""
        return self.sheet_cols * self.eff_per_sheet_col

    @property
    def sheets_per_row(self) -> int:
        """Sheets side-by-side per sheet-row; 1 when columns are auto."""
        if self.eff_per_sheet_row == 0:
            return 1
        return int(self.usable_page_w_in // self.sheet_w_in)

    @property
    def horizontal_gap_in(self) -> float:
        """Gap between adjacent sheets in a sheet-row; 0 when columns are auto."""
        if self.eff_per_sheet_row == 0:
            return 0.0
        return self.usable_page_w_in - self.sheets_per_row * self.sheet_w_in

    @property
    def font_size_pt(self) -> float:
        return self.text_height_in * 72.0 / self.cap_height_ratio


def apply_layout_config(config: JobConfig) -> None:
    """Copy ``config`` into the module-level constants the layout code reads.

    The derived constants (sheet size, labels per sheet, sheets per row, the
    horizontal gap and the font size) are recomputed from the raw values so that
    every downstream function sees one consistent geometry.
    """
    global PAGE_W_IN, PAGE_LEFT_MARGIN_IN, PAGE_RIGHT_MARGIN_IN
    global PAGE_TOP_MARGIN_IN, PAGE_BOTTOM_MARGIN_IN
    global LABEL_W_IN, LABEL_H_IN, LABEL_H_MARGIN_IN, LABEL_V_MARGIN_IN
    global VERTICAL_LABELS, FOOT_W_IN, FOOT_H_IN
    global LABELS_PER_SHEET_ROW, LABELS_PER_SHEET_COL
    global SHEET_COLS, SHEET_W_IN, SHEET_H_IN, LABELS_PER_SHEET
    global SHEETS_PER_ROW, HORIZONTAL_GAP_IN, VERTICAL_GAP_IN
    global COPIES_PER_LABEL, DRAW_BORDER, BORDER_LINE_WIDTH_PT
    global TEXT_HEIGHT_IN, CAP_HEIGHT_RATIO, FONT_SIZE_PT

    PAGE_W_IN = config.page_w_in
    PAGE_LEFT_MARGIN_IN = config.page_left_margin_in
    PAGE_RIGHT_MARGIN_IN = config.page_right_margin_in
    PAGE_TOP_MARGIN_IN = config.page_top_margin_in
    PAGE_BOTTOM_MARGIN_IN = config.page_bottom_margin_in

    LABEL_W_IN = config.label_w_in
    LABEL_H_IN = config.label_h_in
    LABEL_H_MARGIN_IN = config.label_h_margin_in
    LABEL_V_MARGIN_IN = config.label_v_margin_in

    VERTICAL_LABELS = config.vertical_labels
    FOOT_W_IN = config.foot_w_in
    FOOT_H_IN = config.foot_h_in

    # The layout helpers read the *effective* (post-rotation) grid, so the
    # swapped values go into these constants; the raw CLI values live on in the
    # JobConfig.
    LABELS_PER_SHEET_ROW = config.eff_per_sheet_row
    LABELS_PER_SHEET_COL = config.eff_per_sheet_col
    VERTICAL_GAP_IN = config.vertical_gap_in

    COPIES_PER_LABEL = config.copies_per_label

    DRAW_BORDER = config.draw_border
    BORDER_LINE_WIDTH_PT = config.border_line_width_pt

    TEXT_HEIGHT_IN = config.text_height_in
    CAP_HEIGHT_RATIO = config.cap_height_ratio

    SHEET_COLS = config.sheet_cols
    SHEET_W_IN = config.sheet_w_in
    SHEET_H_IN = config.sheet_h_in
    LABELS_PER_SHEET = config.labels_per_sheet
    SHEETS_PER_ROW = config.sheets_per_row
    HORIZONTAL_GAP_IN = config.horizontal_gap_in
    FONT_SIZE_PT = config.font_size_pt


# --- Font registration -------------------------------------------------------

ARIAL_BOLD_NAME: str = "ArialBold"
FALLBACK_FONT_NAME: str = "Helvetica-Bold"

# Common locations for Arial Bold on macOS. Probed in order.
ARIAL_BOLD_CANDIDATES: tuple[str, ...] = (
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Arial Bold.ttf",
)


def _register_bold_font(preferred_path: Path | None = None) -> tuple[str, str | None]:
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
    if LABELS_PER_SHEET_COL == 0:
        # Auto rows: a single vertical sheet; height grows with the label count.
        n_label_rows = -(-num_instances // SHEET_COLS)
        return PAGE_TOP_MARGIN_IN + n_label_rows * FOOT_H_IN + PAGE_BOTTOM_MARGIN_IN
    n_sheets = -(-num_instances // LABELS_PER_SHEET)
    n_sheet_rows = -(-n_sheets // SHEETS_PER_ROW)
    return (
        PAGE_TOP_MARGIN_IN
        + n_sheet_rows * SHEET_H_IN
        + max(0, n_sheet_rows - 1) * VERTICAL_GAP_IN
        + PAGE_BOTTOM_MARGIN_IN
    )


def label_position_in(idx: int, page_h_in: float) -> tuple[float, float]:
    """Return (x_in, y_bottom_in) of the bottom-left corner of label ``idx``.

    Positions use the footprint (``FOOT_W_IN``/``FOOT_H_IN``) and the effective
    grid, both of which are pre-swapped for vertical labels. With auto rows
    (``LABELS_PER_SHEET_COL == 0``) all instances flow through a single
    gap-free grid of ``SHEET_COLS`` columns; otherwise instances fill
    fixed-size sheets placed in row-major order.
    """
    if LABELS_PER_SHEET_COL == 0:
        row, col = divmod(idx, SHEET_COLS)
        x_in = PAGE_LEFT_MARGIN_IN + col * FOOT_W_IN
        y_in = page_h_in - PAGE_TOP_MARGIN_IN - (row + 1) * FOOT_H_IN
        return x_in, y_in
    sheet_idx, within = divmod(idx, LABELS_PER_SHEET)
    row_in_sheet, col_in_sheet = divmod(within, SHEET_COLS)
    sheet_x_in, sheet_y_top_in = sheet_origin_in(sheet_idx, page_h_in)
    # Labels fill the sheet left-to-right, top-to-bottom.
    return (
        sheet_x_in + col_in_sheet * FOOT_W_IN,
        sheet_y_top_in - (row_in_sheet + 1) * FOOT_H_IN,
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
    linear_feet: float  # substrate length along the roll, in linear feet


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
        linear_feet=total_substrate / PAGE_W_IN / 12.0,
    )
    return per_label, global_metrics


# --- Report generation --------------------------------------------------------


def write_metrics_report_json(
    per_label: list[LabelMetrics],
    global_metrics: GlobalMetrics,
    out_path: Path,
    input_path: Path,
    pdf_path: Path,
    timestamp: str,
) -> None:
    """Write a machine-readable JSON metrics report to ``out_path``.

    The schema is a fixed top-level shape (``job`` metadata plus the full
    ``global`` and ``per_label`` metric dicts) so that reports from multiple
    jobs can be loaded and summed for total material/ink usage. All areas are
    in square inches and all lengths in inches, except ``linear_feet`` (the
    substrate length along the roll, in feet).
    """
    report = {
        "job": {
            "generated": timestamp,
            "input_file": str(input_path),
            "output_pdf": str(pdf_path),
            "page_width_in": PAGE_W_IN,
            "label_width_in": LABEL_W_IN,
            "label_height_in": LABEL_H_IN,
            "copies_per_label": COPIES_PER_LABEL,
        },
        "global": asdict(global_metrics),
        "per_label": [asdict(m) for m in per_label],
    }
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def write_metrics_report(
    per_label: list[LabelMetrics],
    global_metrics: GlobalMetrics,
    out_path: Path,
    input_path: Path,
    pdf_path: Path,
) -> Path:
    """Write a human-readable metrics report to ``out_path``.

    Also writes a machine-readable JSON sibling report next to it (the
    ``.txt`` suffix, if any, is replaced with ``.json``) so that totals can be
    consolidated across jobs. Returns the path of the JSON report.
    """
    linear_feet = global_metrics.linear_feet
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

    json_path = out_path.with_suffix(".json")
    write_metrics_report_json(
        per_label, global_metrics, json_path, input_path, pdf_path, timestamp
    )
    return json_path


# --- Drawing ------------------------------------------------------------------


def draw_label_border(c: Canvas, x_in: float, y_in: float) -> None:
    """Draw a hairline border around a single label at the given bottom-left (in inches).

    The border is drawn at the label's on-page footprint, which equals the
    design size rotated 90° clockwise for vertical labels.
    """
    c.saveState()
    c.setLineWidth(BORDER_LINE_WIDTH_PT)
    c.setStrokeColor(black)
    c.rect(
        x_in * 72.0,
        y_in * 72.0,
        FOOT_W_IN * 72.0,
        FOOT_H_IN * 72.0,
        stroke=1,
        fill=0,
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

    The text is centered at the midpoint of the label's footprint. ``scale`` is
    the precomputed horizontal compression factor (see
    :func:`compute_horizontal_scale`) — passing it in keeps the PDF and the
    metrics report in lockstep.

    ``x_in``/``y_in`` are the label's on-page footprint bottom-left. With
    ``VERTICAL_LABELS`` the whole label is rotated 90° clockwise so the text
    reads top-to-bottom and the reader turns their head clockwise to read it.
    """
    # Compute the midpoint of the label's footprint
    midpoint_x_in = x_in + FOOT_W_IN / 2
    midpoint_y_in = y_in + FOOT_H_IN / 2

    c.saveState()
    if VERTICAL_LABELS:
        # Translate to midpoint in page space
        c.translate(midpoint_x_in * 72.0, midpoint_y_in * 72.0)
        # Apply 90° clockwise rotation (ReportLab uses CCW, so -90°)
        c.rotate(-90)
        # Apply horizontal scaling
        c.scale(scale, 1.0)
    else:
        # Translate to midpoint in page space
        c.translate(midpoint_x_in * 72.0, midpoint_y_in * 72.0)
        # Apply horizontal scaling
        c.scale(scale, 1.0)
    c.setFont(font_name, FONT_SIZE_PT)
    c.setFillColor(black)
    # Draw text centered at the origin, with vertical offset to center the cap height.
    # drawCentredString centers horizontally but places the baseline at y;
    # shift down by half the cap height so the text's middle is at the midpoint.
    cap_height_pt = FONT_SIZE_PT * CAP_HEIGHT_RATIO
    y_offset_pt = -cap_height_pt / 2.0
    c.drawCentredString(0, y_offset_pt, text)
    c.restoreState()


def build_pdf(
    labels: list[str],
    out_path: Path,
    per_label: list[LabelMetrics],
    font_path: Path | None = None,
) -> int:
    """Lay out ``labels`` (each printed COPIES_PER_LABEL times) into sheets and write the PDF.

    ``per_label`` provides the precomputed horizontal scale for each unique
    label so the PDF and the metrics report agree exactly. ``font_path``
    (``--font``) selects the TTF to draw with, when one was requested.

    Returns the number of label instances written.
    """
    font_name, _registered_path = _register_bold_font(font_path)
    scale_by_text = {m.text: m.horizontal_scale for m in per_label}
    instances = [lbl for lbl in labels for _ in range(COPIES_PER_LABEL)]
    h_in = page_height_in(len(instances))

    c = Canvas(str(out_path), pagesize=(PAGE_W_IN * 72.0, h_in * 72.0))

    for idx, label in enumerate(instances):
        label_x_in, label_y_in = label_position_in(idx, h_in)
        if DRAW_BORDER:
            draw_label_border(c, label_x_in, label_y_in)
        draw_label(c, label, label_x_in, label_y_in, font_name, scale_by_text[label])

    c.showPage()
    c.save()
    return len(instances)


# --- CLI ----------------------------------------------------------------------


def _validate_config(parser: argparse.ArgumentParser, config: JobConfig) -> None:
    """Exit through ``parser`` unless ``config`` describes a printable layout."""
    positive: dict[str, float] = {
        "--page-width": config.page_w_in,
        "--label-width": config.label_w_in,
        "--label-height": config.label_h_in,
        "--text-height": config.text_height_in,
    }
    for flag, value in positive.items():
        if value <= 0:
            parser.error(f"{flag} must be greater than 0 (got {value:g})")

    non_negative: dict[str, float] = {
        "--page-left-margin": config.page_left_margin_in,
        "--page-right-margin": config.page_right_margin_in,
        "--page-top-margin": config.page_top_margin_in,
        "--page-bottom-margin": config.page_bottom_margin_in,
        "--label-h-margin": config.label_h_margin_in,
        "--label-v-margin": config.label_v_margin_in,
        "--vertical-gap": config.vertical_gap_in,
        "--border-line-width": config.border_line_width_pt,
    }
    for flag, value in non_negative.items():
        if value < 0:
            parser.error(f"{flag} must not be negative (got {value:g})")

    if config.label_h_margin_in * 2 >= config.label_w_in:
        parser.error(
            "--label-h-margin is too large: 2 x "
            f"{config.label_h_margin_in:g}in leaves no text area inside a "
            f"{config.label_w_in:g}in wide label"
        )

    if config.labels_per_sheet_row < 0 or config.labels_per_sheet_col < 0:
        parser.error(
            "--labels-per-sheet-row and --labels-per-sheet-col must be >= 0 "
            "(0 means auto: fill the page width / one unbounded sheet)"
        )

    if config.sheet_cols < 1:
        parser.error(
            f"no {config.foot_w_in:g}in label footprint fits in a "
            f"{config.page_w_in:g}in page "
            f"once its margins are removed; narrow the labels or widen the page"
        )

    if config.copies_per_label < 1:
        parser.error(f"--copies must be >= 1 (got {config.copies_per_label})")

    if not 0.0 < config.cap_height_ratio <= 1.0:
        parser.error(
            f"--cap-height-ratio must be in (0, 1] (got {config.cap_height_ratio:g})"
        )

    if config.sheets_per_row < 1:
        parser.error(
            f"no {config.sheet_w_in:g}in sheet fits in a {config.page_w_in:g}in page "
            f"once its margins are removed; narrow the labels or widen the page"
        )


def parse_args(argv: list[str] | None = None) -> JobConfig:
    """Construct the argument parser, parse ``argv``, return the resulting config.

    Every option constant in this module is exposed as an optional flag whose
    default is that constant's current value; ``-i/--input`` is the only
    required argument.
    """
    parser = argparse.ArgumentParser(
        description="Lay out WIC labels on a wide print page and emit a PDF.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
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
    parser.add_argument(
        "--font",
        type=Path,
        default=None,
        help="TTF to draw the labels with (default: probe the usual Arial Bold locations).",
    )

    page = parser.add_argument_group("page", "print page geometry (inches)")
    page.add_argument("--page-width", type=float, default=PAGE_W_IN, help="Page width.")
    page.add_argument(
        "--page-left-margin",
        type=float,
        default=PAGE_LEFT_MARGIN_IN,
        help="Left page margin.",
    )
    page.add_argument(
        "--page-right-margin",
        type=float,
        default=PAGE_RIGHT_MARGIN_IN,
        help="Right page margin.",
    )
    page.add_argument(
        "--page-top-margin",
        type=float,
        default=PAGE_TOP_MARGIN_IN,
        help="Top page margin.",
    )
    page.add_argument(
        "--page-bottom-margin",
        type=float,
        default=PAGE_BOTTOM_MARGIN_IN,
        help="Bottom page margin.",
    )

    label = parser.add_argument_group("label", "single label geometry (inches)")
    label.add_argument(
        "--label-width", type=float, default=LABEL_W_IN, help="Label width."
    )
    label.add_argument(
        "--label-height", type=float, default=LABEL_H_IN, help="Label height."
    )
    label.add_argument(
        "--label-h-margin",
        type=float,
        default=LABEL_H_MARGIN_IN,
        help="Left/right margin inside each label.",
    )
    label.add_argument(
        "--label-v-margin",
        type=float,
        default=LABEL_V_MARGIN_IN,
        help="Top/bottom margin inside each label.",
    )
    label.add_argument(
        "--vertical-labels",
        action=argparse.BooleanOptionalAction,
        default=VERTICAL_LABELS,
        help="Rotate each label's border and text 90 degrees clockwise so the "
        "text reads top-to-bottom. Swaps the label's on-page width/height, so "
        "--label-width/--label-height keep describing the unrotated label. "
        "With fixed sheet counts the grid transposes too; an auto (0) count "
        "keeps filling its own page axis.",
    )

    sheet = parser.add_argument_group(
        "sheet", "labels-per-sheet grid and sheet spacing"
    )
    sheet.add_argument(
        "--labels-per-sheet-row",
        type=int,
        default=LABELS_PER_SHEET_ROW,
        help="Labels across a sheet (columns). 0 = one sheet filling the page "
        "width with no horizontal gap (default).",
    )
    sheet.add_argument(
        "--labels-per-sheet-col",
        type=int,
        default=LABELS_PER_SHEET_COL,
        help="Labels down a sheet (rows). 0 = one sheet of unbounded height "
        "with no vertical gap.",
    )
    sheet.add_argument(
        "--vertical-gap",
        type=float,
        default=VERTICAL_GAP_IN,
        help="Gap between sheet rows.",
    )

    output = parser.add_argument_group("output", "copies and border")
    output.add_argument(
        "-c",
        "--copies",
        type=int,
        default=COPIES_PER_LABEL,
        help="Copies printed of each label.",
    )
    output.add_argument(
        "--border",
        action=argparse.BooleanOptionalAction,
        default=DRAW_BORDER,
        help="Draw a hairline border around each label.",
    )
    output.add_argument(
        "--border-line-width",
        type=float,
        default=BORDER_LINE_WIDTH_PT,
        help="Border weight in points.",
    )

    text = parser.add_argument_group("text", "label text sizing")
    text.add_argument(
        "--text-height",
        type=float,
        default=TEXT_HEIGHT_IN,
        help="Cap height of the label text.",
    )
    text.add_argument(
        "--cap-height-ratio",
        type=float,
        default=CAP_HEIGHT_RATIO,
        help="Cap height as a fraction of font size for the drawn font.",
    )

    args = parser.parse_args(argv)

    config = JobConfig(
        input_path=Path(args.input),
        output_path=None if args.output is None else Path(args.output),
        font_path=None if args.font is None else Path(args.font),
        page_w_in=float(args.page_width),
        page_left_margin_in=float(args.page_left_margin),
        page_right_margin_in=float(args.page_right_margin),
        page_top_margin_in=float(args.page_top_margin),
        page_bottom_margin_in=float(args.page_bottom_margin),
        label_w_in=float(args.label_width),
        label_h_in=float(args.label_height),
        label_h_margin_in=float(args.label_h_margin),
        label_v_margin_in=float(args.label_v_margin),
        labels_per_sheet_row=int(args.labels_per_sheet_row),
        labels_per_sheet_col=int(args.labels_per_sheet_col),
        vertical_gap_in=float(args.vertical_gap),
        copies_per_label=int(args.copies),
        draw_border=bool(args.border),
        border_line_width_pt=float(args.border_line_width),
        text_height_in=float(args.text_height),
        cap_height_ratio=float(args.cap_height_ratio),
        vertical_labels=bool(args.vertical_labels),
    )
    _validate_config(parser, config)
    return config


def main(argv: list[str] | None = None) -> None:
    config = parse_args(argv)
    apply_layout_config(config)

    labels = parse_labels(config.input_path)
    if not labels:
        raise SystemExit(f"No labels found in {config.input_path}")

    out_path = config.output_path or config.input_path.with_suffix(".pdf")
    if config.vertical_labels:
        out_path = out_path.with_name(f"{out_path.stem}_vertical{out_path.suffix}")
    report_path = out_path.with_name(f"{out_path.stem}_report.txt")

    font_name, font_path = _register_bold_font(config.font_path)
    per_label, global_metrics = calculate_metrics(labels, font_name, font_path)
    n = build_pdf(labels, out_path, per_label, config.font_path)
    json_report_path = write_metrics_report(
        per_label, global_metrics, report_path, config.input_path, out_path
    )
    print(
        f"Wrote {n} label instances "
        f"({len(labels)} unique x {COPIES_PER_LABEL}) to {out_path}\n"
        f"Wrote metrics report to {report_path}\n"
        f"Wrote JSON metrics report to {json_report_path}"
    )


if __name__ == "__main__":
    main()
