"""Drawing: borders, sheet separators, label text, and PDF assembly.

All geometry arrives through a :class:`~vinyllabels.layout.JobConfig`; the only
module-level state here is the PDF point conversion. Coordinates passed to
``Canvas`` are converted from inches to points at the boundary.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.pdfgen.canvas import Canvas

from vinyllabels.colors import cutcontour_spot_color, perfcutcontour_spot_color
from vinyllabels.fonts import register_bold_font
from vinyllabels.layout import (
    JobConfig,
    label_position_in,
    minimum_page_height,
    page_breaks,
)
from vinyllabels.models import LabelMetrics

__all__ = [
    "border_edges",
    "build_pdf",
    "draw_label",
    "draw_label_borders",
    "draw_sheet_separators",
    "pdf_paths_for",
]

PT_PER_IN: float = 72.0

# A border edge: orientation ("v" or "h"), the constant coordinate, and the
# segment's start/end along the axis. Keys carry coordinates rounded to 1e-6
# inch so that float drift between a label's far edge (x + foot_w_in) and the
# neighbouring label's near edge still collapses them into one key.
EdgeKey = tuple[str, float, float, float]

Endpoints = tuple[float, float, float, float]


def border_edges(
    positions: list[tuple[float, float]], foot_w_in: float, foot_h_in: float
) -> dict[EdgeKey, Endpoints]:
    """Return the unique border edges of every label footprint, keyed for dedup.

    ``positions`` are the labels' bottom-left corners in inches. Each label
    contributes four edges (left/right verticals, bottom/top horizontals);
    coincident edges of adjacent labels collapse to a single entry, so shared
    borders are stroked once instead of twice. Values are the exact
    ``(x0, y0, x1, y1)`` endpoints in inches for drawing.
    """
    edges: dict[EdgeKey, Endpoints] = {}
    for x_in, y_in in positions:
        x1_in = x_in + foot_w_in
        y1_in = y_in + foot_h_in
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


def draw_label_borders(
    c: Canvas, positions: list[tuple[float, float]], config: JobConfig
) -> None:
    """Draw the hairline borders of every label footprint (bottom-lefts in inches).

    Borders are drawn at each label's on-page footprint, which equals the design
    size rotated 90° clockwise for vertical labels. Edges shared by adjacent
    labels are de-duplicated (see :func:`border_edges`) and every unique edge is
    stroked exactly once, in a single path.

    When ``cut_ready_borders`` is True, borders use a CutContour spot color with
    overprint enabled, recognized by Roland VersaWorks and other RIP software
    for routing to the plotter blade (hidden from ink nozzles).
    """
    edges = border_edges(positions, config.foot_w_in, config.foot_h_in)
    if not edges:
        return
    c.saveState()
    c.setLineWidth(config.border_line_width_pt)

    if config.cut_ready_borders:
        # Apply CutContour spot color and enable overprint to avoid knockout artifacts.
        c.setStrokeColor(cutcontour_spot_color())
        c.setStrokeOverprint(True)
    else:
        c.setStrokeColor(config.border_color)

    path = c.beginPath()
    for x0_in, y0_in, x1_in, y1_in in edges.values():
        path.moveTo(x0_in * PT_PER_IN, y0_in * PT_PER_IN)
        path.lineTo(x1_in * PT_PER_IN, y1_in * PT_PER_IN)
    c.drawPath(path, stroke=1, fill=0)
    c.restoreState()


def draw_sheet_separators(c: Canvas, config: JobConfig, page_h_in: float) -> None:
    """Draw hairline separators at the midpoint of sheet gaps.

    Draws vertical separators (in horizontal gaps between sheet columns) and
    horizontal separators (in vertical gaps between sheet rows). Lines use the
    same ``border_line_width_pt`` weight as label borders.

    When ``cut_ready_separators`` is True, separators use a PerfCutContour spot
    color with overprint enabled, recognized by Roland VersaWorks and other RIP
    software for routing perforated/through-cuts to the plotter blade.
    """
    c.saveState()
    c.setLineWidth(config.border_line_width_pt)

    if config.cut_ready_separators:
        # Apply PerfCutContour spot color and enable overprint to avoid knockout artifacts.
        c.setStrokeColor(perfcutcontour_spot_color())
        c.setStrokeOverprint(True)
    else:
        c.setStrokeColor(config.sheet_separator_color)

    # Vertical separators (in horizontal gaps between sheet columns).
    if config.sheets_per_row > 1 and config.horizontal_gap_in > 0:
        for sheet_col in range(1, config.sheets_per_row):
            # X coordinate: the next sheet's origin, pulled back half a gap.
            # The full inter-sheet stride (sheet + gap) must be counted here,
            # or separators drift left into the sheets once 3+ share a row.
            x_in = (
                config.page_left_margin_in
                + sheet_col * (config.sheet_w_in + config.horizontal_gap_in)
                - config.horizontal_gap_in / 2.0
            )
            c.line(x_in * PT_PER_IN, 0, x_in * PT_PER_IN, page_h_in * PT_PER_IN)

    # Horizontal separators (in vertical gaps between sheet rows).
    if config.eff_per_sheet_col > 0 and config.vertical_gap_in > 0:
        gap_y_in = config.vertical_gap_in / 2.0
        sheet_row = 0
        while True:
            # Y of the top of this sheet row, measured from the top of the page.
            sheet_row_top_in = (
                page_h_in
                - config.page_top_margin_in
                - sheet_row * (config.sheet_h_in + config.vertical_gap_in)
            )
            # Y of the gap below this sheet, measured from the bottom of the page.
            gap_y_in_from_bottom = sheet_row_top_in - config.sheet_h_in - gap_y_in
            if gap_y_in_from_bottom < config.page_bottom_margin_in:
                break
            c.line(
                0,
                gap_y_in_from_bottom * PT_PER_IN,
                config.page_w_in * PT_PER_IN,
                gap_y_in_from_bottom * PT_PER_IN,
            )
            sheet_row += 1

    c.restoreState()


def draw_label(
    c: Canvas,
    text: str,
    x_in: float,
    y_in: float,
    font_name: str,
    scale: float,
    config: JobConfig,
) -> None:
    """Draw a single label at the given bottom-left position (in inches).

    The text is centered at the midpoint of the label's footprint. ``scale`` is
    the precomputed horizontal compression factor (see
    :func:`~vinyllabels.ink.horizontal_scale`) — passing it in keeps the PDF and
    the metrics report in lockstep.

    With ``vertical_labels`` the whole label is rotated 90° clockwise so the
    text reads top-to-bottom and the reader turns their head clockwise.
    """
    midpoint_x_in = x_in + config.foot_w_in / 2
    midpoint_y_in = y_in + config.foot_h_in / 2

    c.saveState()
    c.translate(midpoint_x_in * PT_PER_IN, midpoint_y_in * PT_PER_IN)
    if config.vertical_labels:
        # ReportLab rotates counter-clockwise, so -90 is clockwise.
        c.rotate(-90)
    c.scale(scale, 1.0)
    c.setFont(font_name, config.font_size_pt)
    c.setFillColor(config.text_color)
    # drawCentredString centers horizontally but places the baseline at y;
    # shift down by half the cap height so the text's middle is at the midpoint.
    cap_height_pt = config.font_size_pt * config.cap_height_ratio
    c.drawCentredString(0, -cap_height_pt / 2.0, text)
    c.restoreState()


def pdf_paths_for(
    out_path: Path, page_break_ranges: list[tuple[int, int]], config: JobConfig
) -> list[Path]:
    """Name the output PDFs for ``page_break_ranges``.

    Names embed the label dimensions and the label count so a stack of printed
    sheets is identifiable without opening it, e.g. ``out_8x3_24-labels.pdf``.
    With several PDFs a part number is inserted before the count so the files
    sort alphabetically in print order.
    """
    label_dims = f"{config.label_w_in:g}x{config.label_h_in:g}"
    paths: list[Path] = []
    for page_num, (start_idx, end_idx) in enumerate(page_break_ranges):
        label_count = end_idx - start_idx + 1
        if len(page_break_ranges) == 1:
            paths.append(
                out_path.parent
                / f"{out_path.stem}_{label_dims}_{label_count}-labels{out_path.suffix}"
            )
        else:
            paths.append(
                out_path.parent / f"{out_path.stem}_{label_dims}_part-{page_num + 1:03}"
                f"_{label_count}-labels{out_path.suffix}"
            )
    return paths


def build_pdf(
    labels: list[str],
    out_path: Path,
    per_label: list[LabelMetrics],
    config: JobConfig,
    page_break_ranges: list[tuple[int, int]] | None = None,
) -> tuple[list[Path], list[float]]:
    """Lay out ``labels`` (each printed ``copies_per_label`` times) into PDF(s).

    ``per_label`` provides the precomputed horizontal scale for each unique
    label so the PDF and the metrics report agree exactly. When
    ``page_break_ranges`` is omitted the breaks are calculated from the config's
    soft page height.

    Returns the paths of the created PDFs and the actual page heights in inches.
    """
    font_name, _registered_path = register_bold_font(config.font_path)
    scale_by_text = {m.text: m.horizontal_scale for m in per_label}
    instances = [lbl for lbl in labels for _ in range(config.copies_per_label)]

    if not instances:
        return [], []

    if page_break_ranges is None:
        page_break_ranges = page_breaks(config, len(instances))

    pdf_paths = pdf_paths_for(out_path, page_break_ranges, config)

    page_heights: list[float] = []
    for page_num, (start_idx, end_idx) in enumerate(page_break_ranges):
        page_instances = instances[start_idx : end_idx + 1]
        # Use minimum page height to eliminate unnecessary whitespace.
        min_page_h_in = minimum_page_height(config, len(page_instances))
        page_heights.append(min_page_h_in)

        c = Canvas(
            str(pdf_paths[page_num]),
            pagesize=(config.page_w_in * PT_PER_IN, min_page_h_in * PT_PER_IN),
        )

        # Positions are computed per page, from 0 to len(page_instances)-1.
        positions = [
            label_position_in(config, idx, min_page_h_in)
            for idx in range(len(page_instances))
        ]

        if config.draw_border:
            draw_label_borders(c, positions, config)

        for label, (label_x_in, label_y_in) in zip(page_instances, positions):
            draw_label(
                c,
                label,
                label_x_in,
                label_y_in,
                font_name,
                scale_by_text[label],
                config,
            )

        if config.draw_sheet_separators:
            draw_sheet_separators(c, config, min_page_h_in)

        c.showPage()
        c.save()

    return pdf_paths, page_heights
