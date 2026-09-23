"""Lay out vinyl labels (8in × 3in) on a 52in-wide print page and emit a PDF.

Reads a list of label codes from a text file (one per line; ``#`` introduces a
comment) and produces a continuous PDF suitable for printing and cutting,
alongside a metrics text report (plus a machine-readable JSON sibling with the
same data, for consolidating totals across jobs) covering material yield and
ink usage.

Run with `--help` to see available command-line options.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

from fontTools.pens.areaPen import AreaPen
from fontTools.ttLib import TTFont as FTTTFont
from reportlab.lib.colors import Color
from reportlab.lib.colors import black, blue, green, magenta, orange, red, yellow, cyan
from reportlab.lib.colors import HexColor as RLHexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont as RLTTFont
from reportlab.pdfgen.canvas import Canvas

# --- Color utilities ---------------------------------------------------------

# Preset color names mapping to reportlab colors
_PRESET_COLORS: dict[str, Color] = {
    "cyan": cyan,
    "c": cyan,
    "magenta": magenta,
    "m": magenta,
    "yellow": yellow,
    "y": yellow,
    "black": black,
    "k": black,
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


def parse_color(color_str: str) -> Color:
    """Parse a color specification into a reportlab color object.

    Accepts:
    - Preset names: black, k, blue, b, green, g, red, r, orange, o, yellow, y,
      violet, v, magenta, m
    - RGB format: r,g,b or (r,g,b) where r,g,b are ints in [0,255]
    - Hex format: aabbcc (6 hex digits, no number-sign)

    Returns a reportlab color object.
    Raises ValueError if the color specification is invalid.
    """
    # Check preset colors first
    if color_str in _PRESET_COLORS:
        return _PRESET_COLORS[color_str]

    # Try RGB format: r,g,b or (r,g,b)
    # Strip optional parentheses if present
    rgb_candidate = color_str
    if rgb_candidate.startswith("(") and rgb_candidate.endswith(")"):
        rgb_candidate = rgb_candidate[1:-1]

    # Check if it looks like RGB (contains commas)
    if "," in rgb_candidate:
        try:
            parts = [p.strip() for p in rgb_candidate.split(",")]
            if len(parts) != 3:
                raise ValueError(
                    f"RGB color must have 3 components, got {len(parts)}: {color_str}"
                )
            r, g, b = [int(p) for p in parts]
            if not all(0 <= val <= 255 for val in (r, g, b)):
                raise ValueError(
                    f"RGB values must be in [0, 255], got r={r}, g={g}, b={b}"
                )
            return RLHexColor(f"#{r:02x}{g:02x}{b:02x}")
        except ValueError as exc:
            raise ValueError(f"Invalid RGB color format {color_str}: {exc}") from exc

    # Try hex format: aabbcc (6 hex digits, no number-sign)
    try:
        # Validate hex format: must be exactly 6 characters
        if len(color_str) != 6:
            raise ValueError(
                f"Hex color must be 6 digits (got {len(color_str)}): {color_str}"
            )
        # Attempt to parse; RLHexColor raises ValueError on invalid hex
        return RLHexColor(f"#{color_str}")
    except ValueError as exc:
        raise ValueError(f"Invalid hex color format {color_str}: {exc}") from exc


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

# How many sheets fit side-by-side per sheet-row, and the gap between adjacent
# sheets. The leftover width (usable page width minus all sheets) is split
# evenly among the sheets_per_row - 1 inter-sheet gaps. With auto columns there
# is a single sheet across the page, so the gap is 0.
SHEETS_PER_ROW: int = (
    1 if LABELS_PER_SHEET_ROW == 0 else int(_USABLE_PAGE_W_IN // SHEET_W_IN)
)
HORIZONTAL_GAP_IN: float = (
    0.0
    if LABELS_PER_SHEET_ROW == 0 or SHEETS_PER_ROW < 2
    else (_USABLE_PAGE_W_IN - SHEETS_PER_ROW * SHEET_W_IN) / (SHEETS_PER_ROW - 1)
)
VERTICAL_GAP_IN: float = 2.0  # gap between sheet rows (unused when rows are auto)

COPIES_PER_LABEL: int = 2

# Hairline border drawn around each label. Set to False to disable.
# Labels tile edge-to-edge, so the border is drawn as a de-duplicated grid of
# edges: an edge shared by two adjacent labels is stroked exactly once.
DRAW_BORDER: bool = True
BORDER_LINE_WIDTH_PT: float = 0.5  # ~0.5pt is the standard "hairline" weight

# Hairline separators drawn at the midpoint of sheet gaps. Set to False to disable.
DRAW_SHEET_SEPARATORS: bool = True
SHEET_SEPARATOR_COLOR_DEFAULT: str = "yellow"
SHEET_SEPARATOR_COLOR: Color = parse_color(SHEET_SEPARATOR_COLOR_DEFAULT)

# Text color for labels (default: black).
TEXT_COLOR_DEFAULT: str = "black"
TEXT_COLOR: Color = parse_color(TEXT_COLOR_DEFAULT)

# Border color for labels (default: black).
BORDER_COLOR_DEFAULT: str = "magenta"
BORDER_COLOR: Color = parse_color(BORDER_COLOR_DEFAULT)

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
    border_color: Color = BORDER_COLOR
    draw_sheet_separators: bool = DRAW_SHEET_SEPARATORS
    sheet_separator_color: Color = SHEET_SEPARATOR_COLOR
    text_color: Color = TEXT_COLOR

    text_height_in: float = TEXT_HEIGHT_IN
    cap_height_ratio: float = CAP_HEIGHT_RATIO

    vertical_labels: bool = VERTICAL_LABELS
    flat_label_price: float | None = None

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
        """Gap between adjacent sheets in a sheet-row; 0 when columns are auto.

        The leftover width (usable page width minus all sheets) is split evenly
        among the ``sheets_per_row - 1`` inter-sheet gaps, so the last sheet's
        right edge lands exactly on the right page margin.
        """
        if self.eff_per_sheet_row == 0 or self.sheets_per_row < 2:
            return 0.0
        slack_in = self.usable_page_w_in - self.sheets_per_row * self.sheet_w_in
        return slack_in / (self.sheets_per_row - 1)

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
    global \
        COPIES_PER_LABEL, \
        DRAW_BORDER, \
        BORDER_LINE_WIDTH_PT, \
        BORDER_COLOR, \
        DRAW_SHEET_SEPARATORS
    global \
        SHEET_SEPARATOR_COLOR, \
        TEXT_COLOR, \
        TEXT_HEIGHT_IN, \
        CAP_HEIGHT_RATIO, \
        FONT_SIZE_PT

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
    BORDER_COLOR = config.border_color
    DRAW_SHEET_SEPARATORS = config.draw_sheet_separators
    SHEET_SEPARATOR_COLOR = config.sheet_separator_color
    TEXT_COLOR = config.text_color

    TEXT_HEIGHT_IN = config.text_height_in
    CAP_HEIGHT_RATIO = config.cap_height_ratio

    SHEET_COLS = config.sheet_cols
    SHEET_W_IN = config.sheet_w_in
    SHEET_H_IN = config.sheet_h_in
    LABELS_PER_SHEET = config.labels_per_sheet
    SHEETS_PER_ROW = config.sheets_per_row
    HORIZONTAL_GAP_IN = config.horizontal_gap_in
    FONT_SIZE_PT = config.font_size_pt


# --- Pricing Configuration ---------------------------------------------------


@dataclass(frozen=True)
class PricingConfig:
    """Cost configuration for computing label production costs."""

    ink_cost_usd_per_sqft: float
    substrate_cost_usd_per_sqft: float
    print_rate_hours_per_sqft: float
    printer_run_cost_usd_per_hour: float
    labor_rate_usd_per_hour: float
    labor_time_factor: float
    markup_percent: float
    customer_report: dict[str, bool] | None = None


def load_pricing_config(path: Path) -> PricingConfig | None:
    """Load PricingConfig from a JSON file, returning None if file missing or invalid."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return PricingConfig(
            ink_cost_usd_per_sqft=float(data["ink_cost_usd_per_sqft"]),
            substrate_cost_usd_per_sqft=float(data["substrate_cost_usd_per_sqft"]),
            print_rate_hours_per_sqft=float(data["print_rate_hours_per_sqft"]),
            printer_run_cost_usd_per_hour=float(data["printer_run_cost_usd_per_hour"]),
            labor_rate_usd_per_hour=float(data["labor_rate_usd_per_hour"]),
            labor_time_factor=float(data["labor_time_factor"]),
            markup_percent=float(data["markup_percent"]),
            customer_report=data.get("customer_report"),
        )
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return None


def resolve_pricing_config(
    config_path: Path | None, cli_overrides: dict[str, float]
) -> PricingConfig | None:
    """Merge file config with CLI overrides, returning None if all absent."""
    config = None
    if config_path is not None:
        config = load_pricing_config(config_path)

    if config is None and not cli_overrides:
        return None

    # Start with defaults if no file config
    if config is None:
        config = PricingConfig(
            ink_cost_usd_per_sqft=0.0,
            substrate_cost_usd_per_sqft=0.0,
            print_rate_hours_per_sqft=0.0,
            printer_run_cost_usd_per_hour=0.0,
            labor_rate_usd_per_hour=0.0,
            labor_time_factor=1.0,
            markup_percent=0.0,
        )

    # Apply CLI overrides
    overrides = {}
    if "ink_cost" in cli_overrides:
        overrides["ink_cost_usd_per_sqft"] = cli_overrides["ink_cost"]
    if "substrate_cost" in cli_overrides:
        overrides["substrate_cost_usd_per_sqft"] = cli_overrides["substrate_cost"]
    if "print_rate" in cli_overrides:
        overrides["print_rate_hours_per_sqft"] = cli_overrides["print_rate"]
    if "printer_cost" in cli_overrides:
        overrides["printer_run_cost_usd_per_hour"] = cli_overrides["printer_cost"]
    if "labor_rate" in cli_overrides:
        overrides["labor_rate_usd_per_hour"] = cli_overrides["labor_rate"]
    if "labor_factor" in cli_overrides:
        overrides["labor_time_factor"] = cli_overrides["labor_factor"]
    if "markup" in cli_overrides:
        overrides["markup_percent"] = cli_overrides["markup"]

    if overrides:
        config = replace(config, **overrides, customer_report=config.customer_report)

    return config


@dataclass(frozen=True)
class CostBreakdown:
    """Per-unit cost breakdown for label production."""

    ink_cost: float
    substrate_cost: float
    printer_hours: float
    printer_cost: float
    labor_hours: float
    labor_cost: float
    total_cost: float
    unit_price: float  # Either markup-based or flat price


def compute_cost_breakdown(
    sqft_ink: float,
    sqft_substrate: float,
    pricing_config: PricingConfig | None,
    flat_label_price: float | None,
) -> CostBreakdown | None:
    """Compute cost breakdown from areas and pricing config.

    Returns None if pricing_config is None. If flat_label_price is provided,
    unit_price uses it; otherwise unit_price is computed from markup.
    """
    if pricing_config is None:
        return None

    ink_cost = sqft_ink * pricing_config.ink_cost_usd_per_sqft
    substrate_cost = sqft_substrate * pricing_config.substrate_cost_usd_per_sqft
    printer_hours = sqft_substrate * pricing_config.print_rate_hours_per_sqft
    printer_cost = printer_hours * pricing_config.printer_run_cost_usd_per_hour
    labor_hours = printer_hours * pricing_config.labor_time_factor
    labor_cost = labor_hours * pricing_config.labor_rate_usd_per_hour
    total_cost = ink_cost + substrate_cost + printer_cost + labor_cost

    if flat_label_price is not None:
        unit_price = flat_label_price
    else:
        unit_price = total_cost * (1.0 + pricing_config.markup_percent / 100.0)

    return CostBreakdown(
        ink_cost=ink_cost,
        substrate_cost=substrate_cost,
        printer_hours=printer_hours,
        printer_cost=printer_cost,
        labor_hours=labor_hours,
        labor_cost=labor_cost,
        total_cost=total_cost,
        unit_price=unit_price,
    )


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


def _is_glob_pattern(pattern: str) -> bool:
    """Whether ``pattern`` contains glob metacharacters."""
    return any(char in pattern for char in "*?[")


def expand_input_paths(pattern: Path) -> list[Path]:
    """Return the input files matched by ``pattern``, in sorted order.

    A plain path is returned as-is (even if it does not exist, so the caller
    can report a useful error). A glob pattern is expanded with ``**``
    supported for recursive matching, and directories are dropped.
    """
    raw = str(pattern)
    if not _is_glob_pattern(raw):
        return [pattern]
    matches = (Path(p) for p in glob.glob(raw, recursive=True))
    return sorted((p for p in matches if p.is_file()), key=lambda p: str(p))


def parse_labels(path: Path) -> list[str]:
    """Return the non-empty, non-comment label codes from ``path``."""
    labels: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        labels.append(line)
    return labels


# --- Output path organization --------------------------------------------------


def organize_output_paths(base_path: Path, basename: str) -> dict[str, Path]:
    """Organize output paths into subdirectories adjacent to input file.

    Creates subdirectories next to the input file for organizing output by type:
    - "PDF Files" for PDFs
    - "Job Report" for TXT reports
    - "json" for JSON reports
    - "csv" for CSV reports

    Returns a dict mapping output type to output path, creating directories if needed.
    """
    base_dir = base_path.parent
    pdf_dir = base_dir / "PDF Files"
    report_dir = base_dir / "Job Report"
    json_dir = base_dir / "json"
    csv_dir = base_dir / "csv"

    # Create directories if they don't exist
    for d in [pdf_dir, report_dir, json_dir, csv_dir]:
        d.mkdir(parents=True, exist_ok=True)

    return {
        "pdf": pdf_dir / f"{basename}.pdf",
        "txt": report_dir / f"{basename}_report.txt",
        "json": json_dir / f"{basename}_report.json",
        "txt_labels": report_dir / f"{basename}_labels.txt",
        "txt_labels_customer": report_dir / f"{basename}_labels_customer.txt",
        "csv_report": csv_dir / f"{basename}_report.csv",
        "csv_report_customer": csv_dir / f"{basename}_report_customer.csv",
    }


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
    cost_breakdown: CostBreakdown | None = None


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
    cost_breakdown: CostBreakdown | None = None


def calculate_metrics(
    labels: list[str],
    font_name: str,
    font_path: str | None,
    pricing_config: PricingConfig | None = None,
    flat_label_price: float | None = None,
) -> tuple[list[LabelMetrics], GlobalMetrics]:
    """Compute per-label and global metrics for ``labels``.

    The ink area per label is computed at the actual drawn scale (i.e. after
    horizontal compression), so summing it across instances yields the total
    ink area that will be deposited on the substrate.
    """
    per_label: list[LabelMetrics] = []
    total_ink_area = 0.0
    total_characters = 0
    total_ink_cost = 0.0
    total_substrate_cost = 0.0
    total_printer_hours = 0.0
    total_printer_cost = 0.0
    total_labor_hours = 0.0
    total_labor_cost = 0.0

    for text in labels:
        scale = compute_horizontal_scale(text, font_name)
        unscaled_area = calculate_text_area(text, font_path, FONT_SIZE_PT)
        scaled_area = unscaled_area * scale

        # Compute cost for single instance (before multiplying by COPIES_PER_LABEL)
        label_sqft_ink = sq_ft(scaled_area)
        label_sqft_substrate = sq_ft(LABEL_W_IN * LABEL_H_IN)
        cost = compute_cost_breakdown(
            label_sqft_ink, label_sqft_substrate, pricing_config, flat_label_price
        )

        per_label.append(
            LabelMetrics(
                text=text,
                char_count=len(text),
                horizontal_scale=scale,
                ink_area_sq_in=scaled_area,
                cost_breakdown=cost,
            )
        )
        total_ink_area += scaled_area * COPIES_PER_LABEL
        total_characters += len(text) * COPIES_PER_LABEL
        if cost:
            total_ink_cost += cost.ink_cost * COPIES_PER_LABEL
            total_substrate_cost += cost.substrate_cost * COPIES_PER_LABEL
            total_printer_hours += cost.printer_hours * COPIES_PER_LABEL
            total_printer_cost += cost.printer_cost * COPIES_PER_LABEL
            total_labor_hours += cost.labor_hours * COPIES_PER_LABEL
            total_labor_cost += cost.labor_cost * COPIES_PER_LABEL

    total_output_labels = len(labels) * COPIES_PER_LABEL
    total_label_material = total_output_labels * (LABEL_W_IN * LABEL_H_IN)
    page_h_in = page_height_in(total_output_labels)
    total_substrate = PAGE_W_IN * page_h_in
    material_yield = (total_label_material / total_substrate) * 100.0
    average_ink_coverage = (total_ink_area / total_label_material) * 100.0

    # Compute global cost breakdown
    global_cost = None
    if pricing_config is not None:
        global_cost_total = (
            total_ink_cost
            + total_substrate_cost
            + total_printer_cost
            + total_labor_cost
        )
        if flat_label_price is not None:
            global_unit_price = flat_label_price * total_output_labels
        else:
            global_unit_price = global_cost_total * (
                1.0 + pricing_config.markup_percent / 100.0
            )
        global_cost = CostBreakdown(
            ink_cost=total_ink_cost,
            substrate_cost=total_substrate_cost,
            printer_hours=total_printer_hours,
            printer_cost=total_printer_cost,
            labor_hours=total_labor_hours,
            labor_cost=total_labor_cost,
            total_cost=global_cost_total,
            unit_price=global_unit_price,
        )

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
        cost_breakdown=global_cost,
    )
    return per_label, global_metrics


# --- Unit conversion ----------------------------------------------------------

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


# --- Label sizes ---------------------------------------------------------------

# A label size is its (width, height) design dimensions in inches; vertical
# labels keep the unrotated design size so counts consolidate across orientations.
LabelSize = tuple[float, float]


def job_label_sizes(global_metrics: GlobalMetrics) -> Counter[LabelSize]:
    """Return printed-label counts keyed by label size for a single job.

    A job prints every label at the same (design) size, so this is a single
    ``{(w, h): total_output_labels}`` entry; the ``Counter`` keeps it
    compatible with multi-job consolidation, which merges many such counters.
    """
    return Counter({(LABEL_W_IN, LABEL_H_IN): global_metrics.total_output_labels})


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
    substrate length along the roll, in feet); every ``*_sq_in`` area also gets
    a derived ``*_sq_ft`` sibling in square feet. A ``label_sizes`` array counts
    printed labels per unique label (design) size.
    """
    report = {
        "job": {
            "generated": timestamp,
            "input_file": str(input_path),
            "output_pdf": str(pdf_path),
            "page_width_in": PAGE_W_IN,
            "label_width_in": LABEL_W_IN,
            "label_height_in": LABEL_H_IN,
            "text_height_in": TEXT_HEIGHT_IN,
            "copies_per_label": COPIES_PER_LABEL,
        },
        "global": with_sq_ft(asdict(global_metrics)),
        "label_sizes": label_sizes_to_json(job_label_sizes(global_metrics)),
        "per_label": [with_sq_ft(asdict(m)) for m in per_label],
    }
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def write_metrics_report(
    per_label: list[LabelMetrics],
    global_metrics: GlobalMetrics,
    txt_path: Path,
    json_path: Path,
    labels_txt_path: Path,
    labels_customer_txt_path: Path,
    csv_path: Path,
    csv_customer_path: Path,
    input_path: Path,
    pdf_path: Path,
) -> Path:
    """Write a human-readable metrics report to ``txt_path``.

    Also writes a machine-readable JSON sibling report to ``json_path``,
    per-label reports, and CSV reports to their respective paths.
    Returns the path of the JSON report.
    """
    linear_feet = global_metrics.linear_feet
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines: list[str] = []
    rule = "=" * 80
    subrule = "-" * 80
    lines.append(rule)
    lines.append("LABEL PRINTING REPORT".center(80))
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
        f" ({sq_ft(global_metrics.total_substrate_sq_in):>9.2f} sq ft)"
    )
    lines.append(f"{'':27}({linear_feet:.2f} linear feet of {PAGE_W_IN:.0f}in roll)")
    lines.append(
        f"Total Label Area:          {global_metrics.total_label_material_sq_in:>10.2f} sq in"
        f" ({sq_ft(global_metrics.total_label_material_sq_in):>9.2f} sq ft)"
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
        f" ({sq_ft(global_metrics.total_ink_area_sq_in):>9.2f} sq ft)"
    )
    lines.append(
        f"Average Ink Coverage:      {global_metrics.average_ink_coverage_pct:>10.2f} %"
    )
    lines.append(f"Total Character Count:     {global_metrics.total_characters:>10d}")
    lines.append("")

    # Cost breakdown section if available
    if global_metrics.cost_breakdown is not None:
        cb = global_metrics.cost_breakdown
        lines.append(subrule)
        lines.append("COST BREAKDOWN")
        lines.append(subrule)
        lines.append(f"Ink Cost:                  ${cb.ink_cost:>10.2f}")
        lines.append(f"Substrate Cost:            ${cb.substrate_cost:>10.2f}")
        lines.append(f"Printer Hours:             {cb.printer_hours:>10.2f} hrs")
        lines.append(f"Printer Cost:              ${cb.printer_cost:>10.2f}")
        lines.append(f"Labor Hours:               {cb.labor_hours:>10.2f} hrs")
        lines.append(f"Labor Cost:                ${cb.labor_cost:>10.2f}")
        lines.append(f"Total Cost:                ${cb.total_cost:>10.2f}")
        lines.append(
            f"Unit Price (per label):    ${cb.unit_price / global_metrics.total_output_labels:>10.2f}"
        )
        lines.append("")

    lines.append(subrule)
    lines.append("LABEL SIZE BREAKDOWN")
    lines.append(subrule)
    lines.append(f"{'Label Size (WxH)':<16} | {'Labels':>6}")
    lines.append(f"{'-' * 16}-+-{'-' * 6}")
    for size, count in sorted(job_label_sizes(global_metrics).items()):
        lines.append(f"{format_label_size(size):<16} | {count:>6d}")
    lines.append("")
    lines.append(subrule)
    lines.append("PER-LABEL BREAKDOWN")
    lines.append(subrule)

    # Build header and separator for per-label table with optional cost columns
    if per_label and per_label[0].cost_breakdown is not None:
        lines.append(
            f"{'Label Code':<16} | {'Chars':>5} | {'Scale':>8} | "
            f"{'Ink Area (sq in)':>16} | {'Ink Cost':>10} | "
            f"{'Substrate Cost':>14} | {'Printer Cost':>12} | "
            f"{'Labor Cost':>10} | {'Total Cost':>10}"
        )
        lines.append(
            f"{'-' * 16}-+-{'-' * 5}-+-{'-' * 8}-+-{'-' * 16}-+-{'-' * 10}-+-{'-' * 14}-+-{'-' * 12}-+-{'-' * 10}-+-{'-' * 10}"
        )
        for m in per_label:
            cost_str = ""
            if m.cost_breakdown:
                cost_str = (
                    f" | ${m.cost_breakdown.ink_cost:>9.2f} | "
                    f"${m.cost_breakdown.substrate_cost:>12.2f} | "
                    f"${m.cost_breakdown.printer_cost:>10.2f} | "
                    f"${m.cost_breakdown.labor_cost:>8.2f} | "
                    f"${m.cost_breakdown.total_cost:>8.2f}"
                )
            lines.append(
                f"{m.text:<16} | {m.char_count:>5d} | {m.horizontal_scale:>8.3f} | {m.ink_area_sq_in:>16.4f}{cost_str}"
            )
    else:
        lines.append(
            f"{'Label Code':<16} | {'Chars':>5} | {'Scale':>8} | "
            f"{'Ink Area (sq in)':>16} | {'Ink Area (sq ft)':>16}"
        )
        lines.append(f"{'-' * 16}-+-{'-' * 5}-+-{'-' * 8}-+-{'-' * 18}-+-{'-' * 18}")
        for m in per_label:
            lines.append(
                f"{m.text:<16} | {m.char_count:>5d} | {m.horizontal_scale:>8.3f} | {m.ink_area_sq_in:>16.4f} | {sq_ft(m.ink_area_sq_in):>16.4f}"
            )
    lines.append(rule)
    lines.append("")

    txt_path.write_text("\n".join(lines), encoding="utf-8")

    write_metrics_report_json(
        per_label, global_metrics, json_path, input_path, pdf_path, timestamp
    )

    # Write text and CSV reports
    write_per_label_report(per_label, labels_txt_path, include_costs=True)
    write_per_label_report(
        per_label,
        labels_customer_txt_path,
        include_costs=False,
    )
    write_metrics_report_csv(per_label, csv_path, include_costs=True)
    write_metrics_report_csv(
        per_label,
        csv_customer_path,
        include_costs=False,
    )

    return json_path


def write_per_label_report(
    per_label: list[LabelMetrics], output_path: Path, include_costs: bool = True
) -> None:
    """Write per-label metrics to a formatted text report.

    If include_costs is True, includes full cost breakdown (for internal use).
    If False, includes only code and unit price (for customers).
    """
    if not per_label:
        return

    lines: list[str] = []

    if include_costs:
        # Internal report: all columns
        lines.append("-" * 160)
        header = (
            f"{'Label Code':<16} | {'Char Count':>5} | {'Scale':>7} | "
            f"{'Ink (sq in)':>12} | {'Ink (sq ft)':>11} | "
            f"{'Ink ($)':>10} | {'Substrate ($)':>13} | {'Print Hrs':>9} | "
            f"{'Print ($)':>10} | {'Labor Hrs':>9} | {'Labor ($)':>10} | "
            f"{'Total ($)':>10} | {'Unit ($)':>10}"
        )
        lines.append(header)
        lines.append("-" * 160)

        for m in per_label:
            label_code = m.text[:16]
            char_count = str(m.char_count)
            scale = f"{m.horizontal_scale:.3f}"
            ink_sq_in = f"{m.ink_area_sq_in:.4f}"
            ink_sq_ft = f"{sq_ft(m.ink_area_sq_in):.4f}"

            if m.cost_breakdown:
                ink_cost = f"{m.cost_breakdown.ink_cost:.2f}"
                substrate_cost = f"{m.cost_breakdown.substrate_cost:.2f}"
                printer_hours = f"{m.cost_breakdown.printer_hours:.2f}"
                printer_cost = f"{m.cost_breakdown.printer_cost:.2f}"
                labor_hours = f"{m.cost_breakdown.labor_hours:.2f}"
                labor_cost = f"{m.cost_breakdown.labor_cost:.2f}"
                total_cost = f"{m.cost_breakdown.total_cost:.2f}"
                unit_price = f"{m.cost_breakdown.unit_price:.2f}"
            else:
                ink_cost = ""
                substrate_cost = ""
                printer_hours = ""
                printer_cost = ""
                labor_hours = ""
                labor_cost = ""
                total_cost = ""
                unit_price = ""

            row = (
                f"{label_code:<16} | {char_count:>5} | {scale:>7} | "
                f"{ink_sq_in:>12} | {ink_sq_ft:>11} | "
                f"{ink_cost:>10} | {substrate_cost:>13} | {printer_hours:>9} | "
                f"{printer_cost:>10} | {labor_hours:>9} | {labor_cost:>10} | "
                f"{total_cost:>10} | {unit_price:>10}"
            )
            lines.append(row)
    else:
        # Customer report: only label code and unit price
        lines.append("-" * 40)
        header = f"{'Label Code':<16} | {'Unit Price ($)':>12}"
        lines.append(header)
        lines.append("-" * 40)

        for m in per_label:
            label_code = m.text[:16]
            unit_price = (
                f"{m.cost_breakdown.unit_price:.2f}" if m.cost_breakdown else ""
            )
            row = f"{label_code:<16} | {unit_price:>12}"
            lines.append(row)

    lines.append("-" * 160 if include_costs else "-" * 40)
    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def write_metrics_report_csv(
    per_label: list[LabelMetrics], output_path: Path, include_costs: bool = True
) -> None:
    """Write per-label metrics to a CSV file.

    If include_costs is True, includes full cost breakdown (for internal use).
    If False, includes only code, character count, and unit price (for customers).
    """
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        if include_costs:
            fieldnames = [
                "Label Code",
                "Char Count",
                "Scale",
                "Ink Area (sq in)",
                "Ink Area (sq ft)",
                "Ink Cost ($)",
                "Substrate Cost ($)",
                "Printer Hours",
                "Printer Cost ($)",
                "Labor Hours",
                "Labor Cost ($)",
                "Total Cost ($)",
                "Unit Price ($)",
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for m in per_label:
                row = {
                    "Label Code": m.text,
                    "Char Count": m.char_count,
                    "Scale": f"{m.horizontal_scale:.3f}",
                    "Ink Area (sq in)": f"{m.ink_area_sq_in:.4f}",
                    "Ink Area (sq ft)": f"{sq_ft(m.ink_area_sq_in):.4f}",
                }
                if m.cost_breakdown:
                    row.update(
                        {
                            "Ink Cost ($)": f"{m.cost_breakdown.ink_cost:.2f}",
                            "Substrate Cost ($)": f"{m.cost_breakdown.substrate_cost:.2f}",
                            "Printer Hours": f"{m.cost_breakdown.printer_hours:.2f}",
                            "Printer Cost ($)": f"{m.cost_breakdown.printer_cost:.2f}",
                            "Labor Hours": f"{m.cost_breakdown.labor_hours:.2f}",
                            "Labor Cost ($)": f"{m.cost_breakdown.labor_cost:.2f}",
                            "Total Cost ($)": f"{m.cost_breakdown.total_cost:.2f}",
                            "Unit Price ($)": f"{m.cost_breakdown.unit_price:.2f}",
                        }
                    )
                else:
                    row.update(
                        {
                            "Ink Cost ($)": "",
                            "Substrate Cost ($)": "",
                            "Printer Hours": "",
                            "Printer Cost ($)": "",
                            "Labor Hours": "",
                            "Labor Cost ($)": "",
                            "Total Cost ($)": "",
                            "Unit Price ($)": "",
                        }
                    )
                writer.writerow(row)
        else:
            # Customer CSV: only code and unit price
            fieldnames = ["Label Code", "Unit Price ($)"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for m in per_label:
                row = {"Label Code": m.text}
                if m.cost_breakdown:
                    row["Unit Price ($)"] = f"{m.cost_breakdown.unit_price:.2f}"
                else:
                    row["Unit Price ($)"] = ""
                writer.writerow(row)


# --- Drawing ------------------------------------------------------------------

# A border edge: orientation ("v" or "h"), the constant coordinate, and the
# segment's start/end along the axis. Keys carry coordinates rounded to 1e-6
# inch so that float drift between a label's far edge (x + FOOT_W_IN) and the
# neighbouring label's near edge still collapses them into one key.
_EdgeKey = tuple[str, float, float, float]


def _border_edges(
    positions: list[tuple[float, float]],
) -> dict[_EdgeKey, tuple[float, float, float, float]]:
    """Return the unique border edges of every label footprint, keyed for dedup.

    ``positions`` are the labels' bottom-left corners in inches. Each label
    contributes four edges (left/right verticals, bottom/top horizontals);
    coincident edges of adjacent labels collapse to a single entry, so shared
    borders are stroked once instead of twice. Values are the exact
    ``(x0, y0, x1, y1)`` endpoints in inches for drawing.
    """
    edges: dict[_EdgeKey, tuple[float, float, float, float]] = {}
    for x_in, y_in in positions:
        x1_in = x_in + FOOT_W_IN
        y1_in = y_in + FOOT_H_IN
        edges[("v", round(x_in, 6), round(y_in, 6), round(y1_in, 6))] = (
            x_in,
            y_in,
            x_in,
            y1_in,
        )
        edges[("v", round(x1_in, 6), round(y_in, 6), round(y1_in, 6))] = (
            x1_in,
            y_in,
            x1_in,
            y1_in,
        )
        edges[("h", round(y_in, 6), round(x_in, 6), round(x1_in, 6))] = (
            x_in,
            y_in,
            x1_in,
            y_in,
        )
        edges[("h", round(y1_in, 6), round(x_in, 6), round(x1_in, 6))] = (
            x_in,
            y1_in,
            x1_in,
            y1_in,
        )
    return edges


def draw_label_borders(c: Canvas, positions: list[tuple[float, float]]) -> None:
    """Draw the hairline borders of every label footprint (bottom-lefts in inches).

    Borders are drawn at each label's on-page footprint, which equals the
    design size rotated 90° clockwise for vertical labels. Edges shared by
    adjacent labels are de-duplicated (see :func:`_border_edges`) and every
    unique edge is stroked exactly once, in a single path.
    """
    edges = _border_edges(positions)
    if not edges:
        return
    c.saveState()
    c.setLineWidth(BORDER_LINE_WIDTH_PT)
    c.setStrokeColor(BORDER_COLOR)
    path = c.beginPath()
    for x0_in, y0_in, x1_in, y1_in in edges.values():
        path.moveTo(x0_in * 72.0, y0_in * 72.0)
        path.lineTo(x1_in * 72.0, y1_in * 72.0)
    c.drawPath(path, stroke=1, fill=0)
    c.restoreState()


def draw_sheet_separators(c: Canvas, page_h_in: float) -> None:
    """Draw hairline separators at the midpoint of sheet gaps.

    Draws vertical separators (in horizontal gaps between sheet columns) and
    horizontal separators (in vertical gaps between sheet rows). Lines use the
    same ``BORDER_LINE_WIDTH_PT`` weight as label borders and are drawn in black.
    """
    c.saveState()
    c.setLineWidth(BORDER_LINE_WIDTH_PT)
    c.setStrokeColor(SHEET_SEPARATOR_COLOR)

    # Vertical separators (in horizontal gaps between sheet columns)
    if SHEETS_PER_ROW > 1 and HORIZONTAL_GAP_IN > 0:
        # Draw vertical line at midpoint of each horizontal gap between columns
        for sheet_col in range(1, SHEETS_PER_ROW):
            # X coordinate: the next sheet's origin, pulled back half a gap.
            # The full inter-sheet stride (sheet + gap) must be counted here,
            # or separators drift left into the sheets once 3+ share a row.
            x_in = (
                PAGE_LEFT_MARGIN_IN
                + sheet_col * (SHEET_W_IN + HORIZONTAL_GAP_IN)
                - HORIZONTAL_GAP_IN / 2.0
            )
            # Draw line top-to-bottom across full page height
            c.line(x_in * 72.0, 0, x_in * 72.0, page_h_in * 72.0)

    # Horizontal separators (in vertical gaps between sheet rows)
    if LABELS_PER_SHEET_COL > 0 and VERTICAL_GAP_IN > 0:
        # Iterate through sheet rows and draw separators at gap midpoints.
        # Calculate sheet row positions based on page height and geometry.
        gap_y_in = VERTICAL_GAP_IN / 2.0

        sheet_row = 0
        while True:
            # Y position of the top of this sheet row (measured from top of page)
            sheet_row_top_in = (
                page_h_in
                - PAGE_TOP_MARGIN_IN
                - sheet_row * (SHEET_H_IN + VERTICAL_GAP_IN)
            )
            # Y position of the gap below this sheet (measured from bottom of page)
            gap_y_in_from_bottom = sheet_row_top_in - SHEET_H_IN - gap_y_in
            gap_y_pt = gap_y_in_from_bottom * 72.0

            # Stop if gap is below the bottom margin
            if gap_y_in_from_bottom < PAGE_BOTTOM_MARGIN_IN:
                break

            # Draw line left-to-right across full page width (edge to edge)
            x_start_pt = 0
            x_end_pt = PAGE_W_IN * 72.0
            c.line(x_start_pt, gap_y_pt, x_end_pt, gap_y_pt)

            sheet_row += 1

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
    c.setFillColor(TEXT_COLOR)
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
    pricing_config: PricingConfig | None = None,
    flat_label_price: float | None = None,
) -> int:
    """Lay out ``labels`` (each printed COPIES_PER_LABEL times) into sheets and write the PDF.

    ``per_label`` provides the precomputed horizontal scale for each unique
    label so the PDF and the metrics report agree exactly. ``font_path``
    (``--font``) selects the TTF to draw with, when one was requested.
    ``pricing_config`` and ``flat_label_price`` are accepted for API consistency
    but not used by this function.

    Returns the number of label instances written.
    """
    font_name, _registered_path = _register_bold_font(font_path)
    scale_by_text = {m.text: m.horizontal_scale for m in per_label}
    instances = [lbl for lbl in labels for _ in range(COPIES_PER_LABEL)]
    h_in = page_height_in(len(instances))

    c = Canvas(str(out_path), pagesize=(PAGE_W_IN * 72.0, h_in * 72.0))

    positions = [label_position_in(idx, h_in) for idx in range(len(instances))]
    # Borders are drawn as one de-duplicated grid so edges shared between
    # adjacent labels receive a single stroke instead of two overlapping ones.
    if DRAW_BORDER:
        draw_label_borders(c, positions)

    for label, (label_x_in, label_y_in) in zip(instances, positions):
        draw_label(c, label, label_x_in, label_y_in, font_name, scale_by_text[label])

    if DRAW_SHEET_SEPARATORS:
        draw_sheet_separators(c, h_in)

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
        description="Lay out labels on a wide print page and emit a PDF.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        required=True,
        help="Path to the labels text file (one code per line; '#' comments are "
        "ignored). May be a glob pattern (quote it, e.g. '*6-chars.txt'), in "
        "which case every matching file is processed in sorted order.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Path to write the PDF (default: <input>.pdf next to the input). "
        "Only allowed when --input matches a single file.",
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
        help="Draw a hairline border around each label. Edges shared between "
        "adjacent labels are drawn once, never doubled.",
    )
    output.add_argument(
        "--border-line-width",
        type=float,
        default=BORDER_LINE_WIDTH_PT,
        help="Border weight in points.",
    )
    output.add_argument(
        "--border-color",
        type=str,
        default=BORDER_COLOR_DEFAULT,
        help="Color of label borders (default: black). "
        "Accepts preset names (black/k, blue/b, green/g, red/r, orange/o, yellow/y, violet/v, magenta/m), "
        "RGB format (r,g,b) or r,g,b with ints in [0,255], or hex format (aabbcc, 6 hex digits).",
    )
    output.add_argument(
        "--sheet-separators",
        action=argparse.BooleanOptionalAction,
        default=DRAW_SHEET_SEPARATORS,
        help="Draw hairline separators at the midpoint of sheet gaps.",
    )
    output.add_argument(
        "--sheet-separator-color",
        type=str,
        default=SHEET_SEPARATOR_COLOR_DEFAULT,
        help="Color of sheet separator hairlines (default: blue). "
        "Accepts preset names (black/k, blue/b, green/g, red/r, orange/o, yellow/y, violet/v, magenta/m), "
        "RGB format (r,g,b) or r,g,b with ints in [0,255], or hex format (aabbcc, 6 hex digits).",
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
    text.add_argument(
        "--text-color",
        type=str,
        default=TEXT_COLOR_DEFAULT,
        help="Color of label text (default: black). "
        "Accepts preset names (black/k, blue/b, green/g, red/r, orange/o, yellow/y, violet/v, magenta/m), "
        "RGB format (r,g,b) or r,g,b with ints in [0,255], or hex format (aabbcc, 6 hex digits).",
    )

    pricing = parser.add_argument_group("pricing", "cost configuration and pricing")
    pricing.add_argument(
        "--pricing-config",
        type=Path,
        default=None,
        help="Path to JSON pricing config file (default: pricing-config.json in project root).",
    )
    pricing.add_argument(
        "--ink-cost",
        type=float,
        default=None,
        help="Ink cost per square foot (USD) (overrides config file).",
    )
    pricing.add_argument(
        "--substrate-cost",
        type=float,
        default=None,
        help="Substrate cost per square foot (USD) (overrides config file).",
    )
    pricing.add_argument(
        "--print-rate",
        type=float,
        default=None,
        help="Print rate in hours per square foot (overrides config file).",
    )
    pricing.add_argument(
        "--printer-cost",
        type=float,
        default=None,
        help="Printer run cost per hour (USD) (overrides config file).",
    )
    pricing.add_argument(
        "--labor-rate",
        type=float,
        default=None,
        help="Labor rate per hour (USD) (overrides config file).",
    )
    pricing.add_argument(
        "--labor-factor",
        type=float,
        default=None,
        help="Labor time multiplier relative to printer hours (overrides config file).",
    )
    pricing.add_argument(
        "--markup",
        type=float,
        default=None,
        help="Markup percentage on total cost (overrides config file).",
    )
    pricing.add_argument(
        "--flat-label-price",
        type=float,
        default=None,
        help="Fixed price per label (USD) (overrides markup calculation).",
    )

    args = parser.parse_args(argv)

    # Parse separator, text, and border colors with error handling
    try:
        separator_color = parse_color(args.sheet_separator_color)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        text_color = parse_color(args.text_color)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        border_color = parse_color(args.border_color)
    except ValueError as exc:
        parser.error(str(exc))

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
        border_color=border_color,
        draw_sheet_separators=bool(args.sheet_separators),
        sheet_separator_color=separator_color,
        text_color=text_color,
        text_height_in=float(args.text_height),
        cap_height_ratio=float(args.cap_height_ratio),
        vertical_labels=bool(args.vertical_labels),
        flat_label_price=args.flat_label_price,
    )
    _validate_config(parser, config)
    return config


def process_job(
    config: JobConfig,
    pricing_config: PricingConfig | None = None,
    flat_label_price: float | None = None,
) -> None:
    """Generate the PDF and metrics reports for a single resolved input file."""
    labels = parse_labels(config.input_path)
    if not labels:
        raise SystemExit(f"No labels found in {config.input_path}")

    # Organize output paths based on input file location
    base_name = config.input_path.stem
    if config.vertical_labels:
        base_name = f"{base_name}_vertical"
    organized_paths = organize_output_paths(config.input_path, base_name)

    # Override with explicit output path if provided, but still use organized subdirs
    if config.output_path is not None:
        # Use custom output path for PDF, but organize reports in subdirs
        out_path = config.output_path
        if config.vertical_labels and not str(out_path).endswith("_vertical.pdf"):
            out_path = out_path.with_name(f"{out_path.stem}_vertical{out_path.suffix}")
    else:
        out_path = organized_paths["pdf"]

    font_name, font_path = _register_bold_font(config.font_path)
    per_label, global_metrics = calculate_metrics(
        labels, font_name, font_path, pricing_config, flat_label_price
    )
    n = build_pdf(
        labels,
        out_path,
        per_label,
        config.font_path,
        pricing_config,
        flat_label_price,
    )
    json_report_path = write_metrics_report(
        per_label,
        global_metrics,
        organized_paths["txt"],
        organized_paths["json"],
        organized_paths["txt_labels"],
        organized_paths["txt_labels_customer"],
        organized_paths["csv_report"],
        organized_paths["csv_report_customer"],
        config.input_path,
        out_path,
    )
    print(
        f"Wrote {n} label instances "
        f"({len(labels)} unique x {COPIES_PER_LABEL}) to {out_path}\n"
        f"Wrote metrics report to {organized_paths['txt']}\n"
        f"Wrote JSON metrics report to {json_report_path}"
    )


def main(argv: list[str] | None = None) -> None:
    config = parse_args(argv)

    input_paths = expand_input_paths(config.input_path)
    if not input_paths:
        raise SystemExit(f"No input files matched {config.input_path}")
    if config.output_path is not None and len(input_paths) > 1:
        raise SystemExit(
            "-o/--output cannot be used when --input matches multiple files "
            f"({len(input_paths)} matched {config.input_path}); omit -o to name "
            "each PDF after its input file"
        )

    apply_layout_config(config)

    # Register the font once up front so a bad --font fails before any output.
    _register_bold_font(config.font_path)

    # Load pricing config (look for --pricing-config, fall back to project root)
    pricing_config_path = None
    if hasattr(config, "pricing_config") and config.pricing_config:
        pricing_config_path = config.pricing_config
    else:
        # Try to find pricing-config.json in the project root
        default_pricing = Path(__file__).parent.parent / "pricing-config.json"
        if default_pricing.exists():
            pricing_config_path = default_pricing

    # Build CLI overrides dict
    cli_overrides = {}
    if hasattr(config, "ink_cost") and config.ink_cost is not None:
        cli_overrides["ink_cost"] = config.ink_cost
    if hasattr(config, "substrate_cost") and config.substrate_cost is not None:
        cli_overrides["substrate_cost"] = config.substrate_cost
    if hasattr(config, "print_rate") and config.print_rate is not None:
        cli_overrides["print_rate"] = config.print_rate
    if hasattr(config, "printer_cost") and config.printer_cost is not None:
        cli_overrides["printer_cost"] = config.printer_cost
    if hasattr(config, "labor_rate") and config.labor_rate is not None:
        cli_overrides["labor_rate"] = config.labor_rate
    if hasattr(config, "labor_factor") and config.labor_factor is not None:
        cli_overrides["labor_factor"] = config.labor_factor
    if hasattr(config, "markup") and config.markup is not None:
        cli_overrides["markup"] = config.markup

    # Resolve pricing config
    pricing_config = resolve_pricing_config(pricing_config_path, cli_overrides)

    # Get flat label price from config
    flat_label_price = config.flat_label_price

    for idx, input_path in enumerate(input_paths):
        if len(input_paths) > 1:
            print(f"\n[{idx + 1}/{len(input_paths)}] {input_path}")
        # ``-o`` is rejected above when more than one file matched, so it is
        # safe to carry through here for the single-file case.
        process_job(
            replace(config, input_path=input_path),
            pricing_config,
            flat_label_price,
        )


if __name__ == "__main__":
    main()
