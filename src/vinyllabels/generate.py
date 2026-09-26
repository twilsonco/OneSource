"""``generate`` — lay out vinyl label codes and emit PDFs plus job reports.

Reads a list of label codes from a text file (one per line; ``#`` introduces a
comment) and produces a continuous PDF suitable for printing and cutting,
alongside a metrics text report, a machine-readable JSON sibling (consumed by
``consolidate``), and internal/customer CSV reports.

Run with ``--help`` for the full option list.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from vinyllabels.cli import add_pricing_arguments, pricing_from_args
from vinyllabels.colors import PRESET_COLOR_NAMES, parse_color
from vinyllabels.drawing import build_pdf
from vinyllabels.fonts import register_bold_font
from vinyllabels.job_report import write_metrics_report
from vinyllabels.labels import expand_input_paths, parse_labels
from vinyllabels.layout import JobConfig
from vinyllabels.layout import page_breaks as compute_page_breaks
from vinyllabels.metrics import apply_actual_page_heights, calculate_metrics
from vinyllabels.models import PricingConfig
from vinyllabels.output_paths import job_output_paths

__all__ = ["build_argument_parser", "main", "process_job"]


def build_argument_parser() -> argparse.ArgumentParser:
    """Construct the parser; every layout constant is an optional flag."""
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
    page.add_argument("--page-width", type=float, help="Page width.")
    for flag, help_text in (
        ("--page-left-margin", "Left page margin."),
        ("--page-right-margin", "Right page margin."),
        ("--page-top-margin", "Top page margin."),
        ("--page-bottom-margin", "Bottom page margin."),
    ):
        page.add_argument(flag, type=float, help=help_text)

    label = parser.add_argument_group("label", "single label geometry (inches)")
    label.add_argument("--label-width", type=float, help="Label width.")
    label.add_argument("--label-height", type=float, help="Label height.")
    label.add_argument(
        "--label-h-margin", type=float, help="Left/right margin inside each label."
    )
    label.add_argument(
        "--label-v-margin", type=float, help="Top/bottom margin inside each label."
    )
    label.add_argument(
        "--vertical-labels",
        action=argparse.BooleanOptionalAction,
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
        help="Labels across a sheet (columns). 0 = one sheet filling the page "
        "width with no horizontal gap (default).",
    )
    sheet.add_argument(
        "--labels-per-sheet-col",
        type=int,
        help="Labels down a sheet (rows). 0 = one sheet of unbounded height "
        "with no vertical gap.",
    )
    sheet.add_argument("--vertical-gap", type=float, help="Gap between sheet rows.")
    sheet.add_argument(
        "--soft-page-height",
        type=float,
        help="Maximum page height (inches) before splitting PDF into multiple files "
        "(soft limit—a page exceeding this will still be included, but next page "
        "starts after it).",
    )

    output = parser.add_argument_group("output", "copies and border")
    output.add_argument(
        "-c",
        "--copies",
        type=int,
        help="Copies printed of each label.",
    )
    output.add_argument(
        "--border",
        action=argparse.BooleanOptionalAction,
        help="Draw a hairline border around each label. Edges shared between "
        "adjacent labels are drawn once, never doubled.",
    )
    output.add_argument(
        "--border-line-width", type=float, help="Border weight in points."
    )
    output.add_argument(
        "--border-color",
        type=str,
        help=_color_help("Color of label borders"),
    )
    output.add_argument(
        "--sheet-separators",
        action=argparse.BooleanOptionalAction,
        help="Draw hairline separators at the midpoint of sheet gaps.",
    )
    output.add_argument(
        "--sheet-separator-color",
        type=str,
        help=_color_help("Color of sheet separator hairlines"),
    )

    text = parser.add_argument_group("text", "label text sizing")
    text.add_argument("--text-height", type=float, help="Cap height of the label text.")
    text.add_argument(
        "--cap-height-ratio",
        type=float,
        help="Cap height as a fraction of font size for the drawn font.",
    )
    text.add_argument("--text-color", type=str, help=_color_help("Color of label text"))

    add_pricing_arguments(parser)
    parser.add_argument(
        "--flat-label-price",
        type=float,
        default=None,
        help="Fixed price per label (USD) (overrides markup calculation).",
    )

    _apply_dataclass_defaults(parser)
    return parser


def _color_help(description: str) -> str:
    return (
        f"{description} (default shown below). "
        f"Accepts preset names ({PRESET_COLOR_NAMES}), "
        "RGB format (r,g,b) or r,g,b with ints in [0,255], "
        "or hex format (aabbcc, 6 hex digits)."
    )


def _apply_dataclass_defaults(parser: argparse.ArgumentParser) -> None:
    """Default every option to the matching :class:`JobConfig` field.

    Keeps one source of truth for defaults (the dataclass) while still letting
    ``argparse`` print them in ``--help``.
    """
    defaults = JobConfig(input_path=Path("."))
    field_names = {
        "--page-width": "page_w_in",
        "--page-left-margin": "page_left_margin_in",
        "--page-right-margin": "page_right_margin_in",
        "--page-top-margin": "page_top_margin_in",
        "--page-bottom-margin": "page_bottom_margin_in",
        "--label-width": "label_w_in",
        "--label-height": "label_h_in",
        "--label-h-margin": "label_h_margin_in",
        "--label-v-margin": "label_v_margin_in",
        "--vertical-labels": "vertical_labels",
        "--labels-per-sheet-row": "labels_per_sheet_row",
        "--labels-per-sheet-col": "labels_per_sheet_col",
        "--vertical-gap": "vertical_gap_in",
        "--soft-page-height": "soft_page_height_in",
        "--copies": "copies_per_label",
        "--border": "draw_border",
        "--border-line-width": "border_line_width_pt",
        "--sheet-separators": "draw_sheet_separators",
        "--text-height": "text_height_in",
        "--cap-height-ratio": "cap_height_ratio",
    }
    for flag, field_name in field_names.items():
        parser.set_defaults(**{_dest(flag): getattr(defaults, field_name)})

    # Color flags default to the parsed color's original spelling.
    parser.set_defaults(
        border_color="magenta",
        sheet_separator_color="yellow",
        text_color="black",
    )


def _dest(flag: str) -> str:
    return flag.lstrip("-").replace("-", "_")


def validate_config(parser: argparse.ArgumentParser, config: JobConfig) -> None:
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


def config_from_args(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> JobConfig:
    """Build and validate the :class:`JobConfig` described by ``args``."""
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
        soft_page_height_in=float(args.soft_page_height),
        flat_label_price=args.flat_label_price,
    )
    validate_config(parser, config)
    return config


def parse_args(argv: list[str] | None = None) -> JobConfig:
    """Parse ``argv`` and return the resulting validated layout config."""
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    return config_from_args(parser, args)


def _output_pdf_path(config: JobConfig, organized: dict[str, Path]) -> Path:
    """Resolve the PDF path, honouring ``-o`` and the vertical-label suffix."""
    if config.output_path is None:
        return organized["pdf"]
    out_path = config.output_path
    if config.vertical_labels and not str(out_path).endswith("_vertical.pdf"):
        out_path = out_path.with_name(f"{out_path.stem}_vertical{out_path.suffix}")
    return out_path


def process_job(config: JobConfig, pricing_config: PricingConfig | None = None) -> None:
    """Generate the PDF and metrics reports for a single resolved input file."""
    labels = parse_labels(config.input_path)
    if not labels:
        raise SystemExit(f"No labels found in {config.input_path}")

    base_name = config.input_path.stem
    if config.vertical_labels:
        base_name = f"{base_name}_vertical"
    organized_paths = job_output_paths(config.input_path, base_name)
    out_path = _output_pdf_path(config, organized_paths)

    font_name, font_path = register_bold_font(config.font_path)
    per_label, global_metrics = calculate_metrics(
        labels, font_name, font_path, config, pricing_config, config.flat_label_price
    )

    num_instances = len(labels) * config.copies_per_label
    page_break_ranges = compute_page_breaks(config, num_instances)

    pdf_paths, page_heights = build_pdf(
        labels, out_path, per_label, config, page_break_ranges
    )

    # The PDF trims trailing whitespace per page, so rebase the substrate
    # numbers onto the page heights that were actually emitted.
    global_metrics = apply_actual_page_heights(global_metrics, config, page_heights)

    json_report_path = write_metrics_report(
        per_label,
        global_metrics,
        organized_paths["txt"],
        organized_paths["json"],
        organized_paths["csv_report"],
        organized_paths["csv_report_customer"],
        config.input_path,
        pdf_paths,
        config,
        page_break_ranges=page_break_ranges,
        customer_report_config=pricing_config.customer_report
        if pricing_config
        else None,
    )

    summary = (
        f"Wrote {num_instances} label instances "
        f"({len(labels)} unique x {config.copies_per_label})"
    )
    if len(pdf_paths) == 1:
        summary += f" to {pdf_paths[0]}"
    else:
        summary += f" to {len(pdf_paths)} PDFs:\n" + "\n".join(
            f"  {p}" for p in pdf_paths
        )
    print(
        f"{summary}\n"
        f"Wrote metrics report to {organized_paths['txt']}\n"
        f"Wrote JSON metrics report to {json_report_path}"
    )


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run one job per matched input file."""
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    config = config_from_args(parser, args)

    input_paths = expand_input_paths(config.input_path)
    if not input_paths:
        raise SystemExit(f"No input files matched {config.input_path}")
    if config.output_path is not None and len(input_paths) > 1:
        raise SystemExit(
            "-o/--output cannot be used when --input matches multiple files "
            f"({len(input_paths)} matched {config.input_path}); omit -o to name "
            "each PDF after its input file"
        )

    # Register the font up front so a bad --font fails before any output.
    register_bold_font(config.font_path)

    # Unlike the previous script, the pricing flags are actually honoured here.
    pricing_config = pricing_from_args(args)

    for idx, input_path in enumerate(input_paths):
        if len(input_paths) > 1:
            print(f"\n[{idx + 1}/{len(input_paths)}] {input_path}")
        # ``-o`` is rejected above when several files matched, so carrying it
        # through is safe for the single-file case.
        process_job(replace(config, input_path=input_path), pricing_config)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
