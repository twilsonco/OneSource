"""Lay out WIC labels (8in × 3in) on a 52in-wide print page and emit a PDF.

Reads a list of label codes from a text file (one per line; ``#`` introduces a
comment) and produces a continuous PDF suitable for printing and cutting.

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
from pathlib import Path

from reportlab.lib.colors import black
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
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


def _register_bold_font() -> str:
    """Register bold Arial from disk if available; otherwise return the fallback name."""
    for candidate in ARIAL_BOLD_CANDIDATES:
        if Path(candidate).exists():
            try:
                pdfmetrics.registerFont(TTFont(ARIAL_BOLD_NAME, candidate))
                return ARIAL_BOLD_NAME
            except Exception:
                continue
    return FALLBACK_FONT_NAME


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
) -> None:
    """Draw a single label at the given bottom-left position (in inches).

    Within the label, the text is centered horizontally in the 7in-wide text
    area and its baseline sits flush with the inner edge of the bottom margin
    so the 2in cap height fills the 2in text area from bottom margin to top
    margin. Horizontal compression is applied only when the natural width
    exceeds the text area.
    """
    text_x0_in = x_in + LABEL_H_MARGIN_IN
    text_baseline_in = y_in + LABEL_V_MARGIN_IN
    text_w_in = LABEL_W_IN - 2 * LABEL_H_MARGIN_IN  # 7in when LABEL_H_MARGIN_IN = 0.5

    natural_w_pt = c.stringWidth(text, font_name, FONT_SIZE_PT)
    natural_w_in = natural_w_pt / 72.0
    if natural_w_in > 0:
        scale = min(1.0, text_w_in / natural_w_in)
    else:
        scale = 1.0
    drawn_w_in = natural_w_in * scale
    text_x_in = text_x0_in + (text_w_in - drawn_w_in) / 2.0  # horizontal center

    c.saveState()
    c.translate(text_x_in * 72.0, text_baseline_in * 72.0)
    c.scale(scale, 1.0)
    c.setFont(font_name, FONT_SIZE_PT)
    c.setFillColor(black)
    c.drawString(0, 0, text)
    c.restoreState()


def build_pdf(labels: list[str], out_path: Path) -> int:
    """Lay out ``labels`` (each printed COPIES_PER_LABEL times) into sheets and write the PDF.

    Returns the number of label instances written.
    """
    font_name = _register_bold_font()
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
        draw_label(c, label, label_x_in, label_y_in, font_name)

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
    n = build_pdf(labels, out_path)
    print(
        f"Wrote {n} label instances "
        f"({len(labels)} unique x {COPIES_PER_LABEL}) to {out_path}"
    )


if __name__ == "__main__":
    main()
