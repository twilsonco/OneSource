"""Layout configuration and pure geometry.

Every number a label job needs is a field of :class:`JobConfig`; nothing in this
module reads ambient state. That is deliberate: the previous implementation kept
the page/label geometry in module-level globals that ``apply_layout_config()``
rewrote in place, which made the layout functions order-dependent and
untestable in isolation. Here each function takes the config it needs and
returns a value, so any layout question can be asked about any job directly.

Units: inches for page/label geometry, points for font metrics.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reportlab.lib.colors import Color

from vinyllabels.colors import parse_color
from vinyllabels.sizes import LabelSize

__all__ = [
    "JobConfig",
    "grid_page_height",
    "label_position_in",
    "minimum_page_height",
    "page_breaks",
    "page_height_in",
    "resolve_sheet_cols",
    "sheet_origin_in",
]

# --- Defaults (inches unless the suffix says otherwise) -----------------------
#
# These are immutable defaults, overridable per job from the command line. They
# are *not* mutable module state: a job's geometry lives on its JobConfig.

PAGE_W_IN: float = 52.0
PAGE_LEFT_MARGIN_IN: float = 0.0
PAGE_RIGHT_MARGIN_IN: float = 0.0
PAGE_TOP_MARGIN_IN: float = 1.0
PAGE_BOTTOM_MARGIN_IN: float = 1.0

LABEL_W_IN: float = 8.0
LABEL_H_IN: float = 3.0
LABEL_H_MARGIN_IN: float = 0.25  # left/right margin inside each label
LABEL_V_MARGIN_IN: float = 0.5  # top/bottom margin inside each label

# When True, each label's border and text are rotated 90 degrees clockwise so
# the text reads top-to-bottom (turn your head clockwise to read it). The label
# keeps its internal design (text area, margins, cap height) and is rotated as
# a unit, so ``label_w_in``/``label_h_in`` stay the *design* dimensions used for
# text layout while ``foot_w_in``/``foot_h_in`` are the on-page footprint used
# for positioning: with vertical labels the footprint swaps width/height, and
# when both sheet counts are fixed the grid transposes (columns <-> rows). An
# auto (0) count is a page-space directive and stays on its own axis.
VERTICAL_LABELS: bool = False

# A sheet is a self-contained block of labels, no internal gaps. A size of 0
# means "auto": 0 columns makes a single sheet that fills the usable page width
# (no horizontal gap); 0 rows makes a single sheet of unbounded height (no
# vertical gap). These defaults yield one sheet across the page, 8 rows down.
LABELS_PER_SHEET_ROW: int = 0  # columns per sheet (0 = fill page width)
LABELS_PER_SHEET_COL: int = 8  # rows per sheet (0 = single unbounded sheet)

VERTICAL_GAP_IN: float = 2.0  # gap between sheet rows (unused when rows are auto)

COPIES_PER_LABEL: int = 2

# Hairline border drawn around each label. Labels tile edge-to-edge, so the
# border is drawn as a de-duplicated grid of edges: an edge shared by two
# adjacent labels is stroked exactly once.
DRAW_BORDER: bool = True
BORDER_LINE_WIDTH_PT: float = 0.5  # ~0.5pt is the standard "hairline" weight

DRAW_SHEET_SEPARATORS: bool = True
SHEET_SEPARATOR_COLOR_DEFAULT: str = "yellow"
TEXT_COLOR_DEFAULT: str = "black"
BORDER_COLOR_DEFAULT: str = "magenta"

TEXT_HEIGHT_IN: float = 2.0  # cap height of the label text

# Cap-height-to-font-size ratio: Arial Bold reports HHeight = 728/1000 (0.728),
# Helvetica Bold is 718/1000 (0.718). We pick the larger so the cap height
# never exceeds TEXT_HEIGHT_IN; with Arial this yields a true 2.00in cap,
# with Helvetica it lands at ~2.01in.
CAP_HEIGHT_RATIO: float = 0.728

SOFT_PAGE_HEIGHT_IN: float = 80.0  # max page height before splitting into PDFs


def resolve_sheet_cols(
    labels_per_sheet_row: int, usable_w_in: float, label_w_in: float
) -> int:
    """Return columns per sheet, expanding the auto value 0 to fill ``usable_w_in``."""
    if labels_per_sheet_row == 0:
        return int(usable_w_in // label_w_in)
    return labels_per_sheet_row


@dataclass(frozen=True)
class JobConfig:
    """Every tunable option of a print job, as resolved from the command line.

    Field defaults are the option constants above, so ``JobConfig(input_path=…)``
    reproduces the documented default layout exactly. Derived quantities are
    exposed as properties, and every layout function takes a ``JobConfig``.
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
    border_color: Color = parse_color(BORDER_COLOR_DEFAULT)
    draw_sheet_separators: bool = DRAW_SHEET_SEPARATORS
    sheet_separator_color: Color = parse_color(SHEET_SEPARATOR_COLOR_DEFAULT)
    text_color: Color = parse_color(TEXT_COLOR_DEFAULT)

    text_height_in: float = TEXT_HEIGHT_IN
    cap_height_ratio: float = CAP_HEIGHT_RATIO

    vertical_labels: bool = VERTICAL_LABELS
    soft_page_height_in: float = SOFT_PAGE_HEIGHT_IN
    flat_label_price: float | None = None
    skip_postprocess: bool = False

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
        return resolve_sheet_cols(
            self.eff_per_sheet_row, self.usable_page_w_in, self.foot_w_in
        )

    @property
    def sheet_w_in(self) -> float:
        """Sheet width in inches."""
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
        """Font size (points) that yields ``text_height_in`` of cap height."""
        return self.text_height_in * 72.0 / self.cap_height_ratio

    @property
    def label_area_sq_in(self) -> float:
        """Area of one label's design rectangle, in square inches."""
        return self.label_w_in * self.label_h_in

    @property
    def label_size(self) -> LabelSize:
        """The job's design label size as a ``(width_in, height_in)`` pair."""
        return (self.label_w_in, self.label_h_in)

    @property
    def text_area_w_in(self) -> float:
        """Width available to label text, inside the horizontal label margins."""
        return self.label_w_in - 2 * self.label_h_margin_in


# --- Geometry -----------------------------------------------------------------


def sheet_origin_in(
    config: JobConfig, sheet_idx: int, page_h_in: float
) -> tuple[float, float]:
    """Return (x_in, y_top_in) of the top-left corner of sheet ``sheet_idx``."""
    sheet_col = sheet_idx % config.sheets_per_row
    sheet_row = sheet_idx // config.sheets_per_row
    x_in = config.page_left_margin_in + sheet_col * (
        config.sheet_w_in + config.horizontal_gap_in
    )
    y_top_in = (
        page_h_in
        - config.page_top_margin_in
        - sheet_row * (config.sheet_h_in + config.vertical_gap_in)
    )
    return x_in, y_top_in


def grid_page_height(config: JobConfig, num_instances: int) -> float:
    """Page height (inches) needed for ``num_instances``, including page margins.

    With auto rows the labels flow through a single gap-free sheet whose height
    grows with the count; otherwise they fill fixed-size sheets arranged in
    row-major order, with a vertical gap between sheet rows.
    """
    if config.eff_per_sheet_col == 0:
        n_label_rows = -(-num_instances // config.sheet_cols)
        return (
            config.page_top_margin_in
            + n_label_rows * config.foot_h_in
            + config.page_bottom_margin_in
        )
    n_sheets = -(-num_instances // config.labels_per_sheet)
    n_sheet_rows = -(-n_sheets // config.sheets_per_row)
    return (
        config.page_top_margin_in
        + n_sheet_rows * config.sheet_h_in
        + max(0, n_sheet_rows - 1) * config.vertical_gap_in
        + config.page_bottom_margin_in
    )


# Public alias: the name used by the metrics code and the reports.
page_height_in = grid_page_height


def minimum_page_height(config: JobConfig, num_instances: int) -> float:
    """Minimum page height needed for ``num_instances``, trimming empty space.

    Trims unnecessary space by using actual row counts in partial sheets. For
    sheet layouts, if the last sheet-row contains only one sheet and that sheet
    is partial, height is based on rows actually used. Otherwise uses the
    standard sheet height.
    """
    if num_instances == 0:
        return config.page_top_margin_in + config.page_bottom_margin_in

    if config.eff_per_sheet_col == 0:
        # Auto rows: labels flow in a single sheet with unbounded height.
        n_label_rows = -(-num_instances // config.sheet_cols)
        return (
            config.page_top_margin_in
            + n_label_rows * config.foot_h_in
            + config.page_bottom_margin_in
        )

    # Fixed sheets.
    n_sheets = -(-num_instances // config.labels_per_sheet)
    n_sheet_rows = -(-n_sheets // config.sheets_per_row)
    sheets_in_last_row = n_sheets % config.sheets_per_row
    if sheets_in_last_row == 0:
        sheets_in_last_row = config.sheets_per_row

    # How many instances in the last sheet?
    remaining_in_last_sheet = num_instances % config.labels_per_sheet
    if remaining_in_last_sheet == 0:
        remaining_in_last_sheet = config.labels_per_sheet

    # Standard height for full sheet-rows.
    standard_height = grid_page_height(config, num_instances)

    # Optimization: if the last sheet-row has only 1 sheet and it's partial,
    # reduce the last sheet-row height to just the rows used.
    if sheets_in_last_row == 1:
        rows_in_last_sheet = -(-remaining_in_last_sheet // config.sheet_cols)
        if rows_in_last_sheet < config.eff_per_sheet_col:
            return (
                config.page_top_margin_in
                + (n_sheet_rows - 1) * config.sheet_h_in
                + max(0, n_sheet_rows - 2) * config.vertical_gap_in
                + (config.vertical_gap_in if n_sheet_rows > 1 else 0)
                + rows_in_last_sheet * config.foot_h_in
                + config.page_bottom_margin_in
            )

    return standard_height


def label_position_in(
    config: JobConfig, idx: int, page_h_in: float
) -> tuple[float, float]:
    """Return (x_in, y_bottom_in) of the bottom-left corner of label ``idx``.

    Positions use the footprint (``foot_w_in``/``foot_h_in``) and the effective
    grid, both of which are pre-swapped for vertical labels. With auto rows all
    instances flow through a single gap-free grid of ``sheet_cols`` columns;
    otherwise instances fill fixed-size sheets placed in row-major order.
    """
    if config.eff_per_sheet_col == 0:
        row, col = divmod(idx, config.sheet_cols)
        x_in = config.page_left_margin_in + col * config.foot_w_in
        y_in = page_h_in - config.page_top_margin_in - (row + 1) * config.foot_h_in
        return x_in, y_in
    sheet_idx, within = divmod(idx, config.labels_per_sheet)
    row_in_sheet, col_in_sheet = divmod(within, config.sheet_cols)
    sheet_x_in, sheet_y_top_in = sheet_origin_in(config, sheet_idx, page_h_in)
    # Labels fill the sheet left-to-right, top-to-bottom.
    return (
        sheet_x_in + col_in_sheet * config.foot_w_in,
        sheet_y_top_in - (row_in_sheet + 1) * config.foot_h_in,
    )


# --- Page break calculation ---------------------------------------------------


def page_breaks(
    config: JobConfig,
    num_instances: int,
    soft_page_height_in: float | None = None,
) -> list[tuple[int, int]]:
    """Calculate page breaks for multi-page PDF splitting.

    Returns a list of ``(start_idx, end_idx)`` tuples (inclusive on both ends)
    representing label ranges per page. If the entire document fits within
    ``soft_page_height_in`` (defaulting to the config's value), a single range
    covering all instances is returned.

    Split boundaries respect sheet/row structure: with auto rows splits occur
    after complete label rows, and with fixed rows after complete sheet-rows.
    The end of the document is always a candidate too, so a page is only closed
    when the *whole* remainder stops fitting. Without it a count landing exactly
    on a row boundary has no candidate after it, and the trailing rows would
    split into separate PDFs even though they fitted together.
    """
    if num_instances == 0:
        return []

    limit_in = (
        config.soft_page_height_in
        if soft_page_height_in is None
        else soft_page_height_in
    )

    # Candidate split points: the index right after a complete row/sheet-row.
    if config.eff_per_sheet_col == 0:
        stride = config.sheet_cols
    else:
        # One sheet-row holds labels_per_sheet * sheets_per_row instances.
        stride = config.labels_per_sheet * config.sheets_per_row
    interior_splits = [
        (i + 1) * stride
        for i in range(num_instances // stride)
        if (i + 1) * stride < num_instances
    ]
    split_candidates = [*interior_splits, num_instances]

    # Greedily extend the current page while the next candidate still fits.
    breaks: list[int] = [0]
    last_good_split = 0
    candidate_idx = 0

    while candidate_idx < len(split_candidates):
        split_idx = split_candidates[candidate_idx]
        page_start = breaks[-1]
        height_in = grid_page_height(config, split_idx - page_start)

        if height_in <= limit_in:
            # This candidate fits on the current page; remember it.
            last_good_split = split_idx
            candidate_idx += 1
        elif last_good_split > page_start:
            # Over the limit, but an earlier candidate fit: close the page
            # there and re-check this candidate against the next page.
            breaks.append(last_good_split)
            last_good_split = 0
        else:
            # Nothing fit, so include this candidate anyway to guarantee at
            # least one row/sheet per page.
            breaks.append(split_idx)
            last_good_split = 0
            candidate_idx += 1

    if last_good_split > breaks[-1]:
        breaks.append(last_good_split)

    breaks.append(num_instances)
    boundaries = sorted(set(breaks))

    return [(boundaries[i], boundaries[i + 1] - 1) for i in range(len(boundaries) - 1)]
