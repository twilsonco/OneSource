"""Per-job report writers: JSON, text, and CSV.

The JSON report is the contract between ``generate`` and ``consolidate``: it
carries the job metadata plus the full ``global``/``per_label`` metric dicts, so
many jobs can be summed later. Its shape is therefore treated as a stable
interface and is covered by round-trip tests.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from vinyllabels.layout import JobConfig
from vinyllabels.metrics import job_label_sizes, per_pdf_metrics
from vinyllabels.models import GlobalMetrics, LabelMetrics
from vinyllabels.reportio.table import (
    Column,
    TextColumn,
    render_text_table,
    write_csv_table,
)
from vinyllabels.sizes import format_label_size, label_sizes_to_json
from vinyllabels.units import sq_ft, with_sq_ft

__all__ = [
    "label_to_pdf_mapping",
    "write_metrics_csv",
    "write_metrics_report",
    "write_metrics_report_json",
    "write_metrics_report_txt",
]

_RULE = "=" * 80
_SUBRULE = "-" * 80


def label_to_pdf_mapping(
    per_label: list[LabelMetrics],
    pdf_filenames: list[str],
    page_break_ranges: list[tuple[int, int]] | None,
    copies_per_label: int,
) -> dict[str, str]:
    """Map each label code to the PDF file it first appears in.

    Instances are laid out as ``label0 x copies, label1 x copies, ...``, so the
    first instance of a code sits at ``index * copies``. With a single PDF every
    code maps to that file.
    """
    if not page_break_ranges or len(pdf_filenames) == 1:
        return {m.text: pdf_filenames[0] for m in per_label}

    mapping: dict[str, str] = {}
    for index, metric in enumerate(per_label):
        if metric.text in mapping:
            continue
        first_instance = index * copies_per_label
        for page_num, (start, end) in enumerate(page_break_ranges):
            if start <= first_instance <= end:
                mapping[metric.text] = pdf_filenames[
                    min(page_num, len(pdf_filenames) - 1)
                ]
                break
    return mapping


def write_metrics_report_json(
    per_label: list[LabelMetrics],
    global_metrics: GlobalMetrics,
    out_path: Path,
    input_path: Path,
    pdf_paths: Path | list[Path],
    config: JobConfig,
    timestamp: str,
    page_break_ranges: list[tuple[int, int]] | None = None,
) -> None:
    """Write the machine-readable metrics report to ``out_path``.

    All areas are in square inches and lengths in inches, except ``linear_feet``
    (substrate length along the roll, in feet); every ``*_sq_in`` area also gets
    a derived ``*_sq_ft`` sibling. ``label_sizes`` counts printed labels per
    unique design size. When ``page_break_ranges`` is given, a ``per_pdf`` array
    carries the same metrics aggregated per emitted PDF.
    """
    pdf_paths_list = [pdf_paths] if isinstance(pdf_paths, Path) else list(pdf_paths)
    pdf_strs = [str(p) for p in pdf_paths_list]

    report: dict[str, object] = {
        "job": {
            "generated": timestamp,
            "input_file": str(input_path),
            "output_pdf": pdf_strs if len(pdf_strs) > 1 else pdf_strs[0],
            "page_width_in": config.page_w_in,
            "label_width_in": config.label_w_in,
            "label_height_in": config.label_h_in,
            "text_height_in": config.text_height_in,
            "copies_per_label": config.copies_per_label,
        },
        "global": with_sq_ft(asdict(global_metrics)),
        "label_sizes": label_sizes_to_json(job_label_sizes(config, global_metrics)),
        "per_label": [with_sq_ft(asdict(m)) for m in per_label],
    }

    if page_break_ranges:
        pdfs = per_pdf_metrics(
            per_label,
            page_break_ranges,
            config.copies_per_label,
            config.label_area_sq_in,
        )
        report["per_pdf"] = [with_sq_ft(asdict(m)) for m in pdfs]

    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def write_metrics_report_txt(
    per_label: list[LabelMetrics],
    global_metrics: GlobalMetrics,
    txt_path: Path,
    input_path: Path,
    pdf_paths: list[Path],
    config: JobConfig,
    timestamp: str,
) -> None:
    """Write the human-readable metrics report to ``txt_path``."""
    lines: list[str] = [_RULE, "LABEL PRINTING REPORT".center(80), _RULE]
    lines.append(f"Generated:        {timestamp}")
    lines.append(f"Input File:       {input_path}")
    for index, pdf_path in enumerate(pdf_paths):
        prefix = "Output PDF:       " if index == 0 else " " * 18
        lines.append(f"{prefix}{pdf_path}")
    lines.append("")

    lines += [_SUBRULE, "GLOBAL PRINTING METRICS", _SUBRULE]
    lines.append(
        f"Total Substrate Required:  {global_metrics.total_substrate_sq_in:>10.2f} sq in"
        f" ({sq_ft(global_metrics.total_substrate_sq_in):>9.2f} sq ft)"
    )
    lines.append(
        f"{'':27}({global_metrics.linear_feet:.2f} linear feet of "
        f"{config.page_w_in:.0f}in roll)"
    )
    lines.append(
        f"Total Label Area:          {global_metrics.total_label_material_sq_in:>10.2f} sq in"
        f" ({sq_ft(global_metrics.total_label_material_sq_in):>9.2f} sq ft)"
    )
    lines.append(
        f"Material Yield:            {global_metrics.material_yield_pct:>10.2f} %"
    )
    lines.append("")

    lines += [_SUBRULE, "INK USAGE METRICS", _SUBRULE]
    lines.append(
        f"Total Ink Area:            {global_metrics.total_ink_area_sq_in:>10.2f} sq in"
        f" ({sq_ft(global_metrics.total_ink_area_sq_in):>9.2f} sq ft)"
    )
    lines.append(
        f"Average Ink Coverage:      {global_metrics.average_ink_coverage_pct:>10.2f} %"
    )
    lines.append(f"Total Character Count:     {global_metrics.total_characters:>10d}")
    lines.append("")

    cb = global_metrics.cost_breakdown
    if cb is not None:
        per_label_price = (
            cb.unit_price / global_metrics.total_output_labels
            if global_metrics.total_output_labels
            else 0.0
        )
        lines += [_SUBRULE, "COST BREAKDOWN", _SUBRULE]
        lines.append(f"Ink Cost:                  ${cb.ink_cost:>10.2f}")
        lines.append(f"Substrate Cost:            ${cb.substrate_cost:>10.2f}")
        lines.append(f"Printer Hours:             {cb.printer_hours:>10.2f} hrs")
        lines.append(f"Printer Cost:              ${cb.printer_cost:>10.2f}")
        lines.append(f"Labor Hours:               {cb.labor_hours:>10.2f} hrs")
        lines.append(f"Labor Cost:                ${cb.labor_cost:>10.2f}")
        lines.append(f"Total Cost:                ${cb.total_cost:>10.2f}")
        lines.append(f"Unit Price (per label):    ${per_label_price:>10.2f}")
        lines.append("")

    lines += [_SUBRULE, "LABEL SIZE BREAKDOWN", _SUBRULE]
    lines.append(f"{'Label Size (WxH)':<16} | {'Labels':>6}")
    lines.append(f"{'-' * 16}-+-{'-' * 6}")
    for size, count in sorted(job_label_sizes(config, global_metrics).items()):
        lines.append(f"{format_label_size(size):<16} | {count:>6d}")
    lines.append("")

    lines += [_SUBRULE, "PER-LABEL BREAKDOWN", _SUBRULE]
    lines += _per_label_table(per_label)
    lines.append(_RULE)
    lines.append("")

    txt_path.write_text("\n".join(lines), encoding="utf-8")


def _per_label_table(per_label: list[LabelMetrics]) -> list[str]:
    """Render the per-label breakdown, with cost columns when pricing exists."""
    base: list[TextColumn[LabelMetrics]] = [
        TextColumn("Label Code", 16, lambda m: m.text, "left"),
        TextColumn("Chars", 5, lambda m: f"{m.char_count:d}"),
        TextColumn("Scale", 8, lambda m: f"{m.horizontal_scale:.3f}"),
        TextColumn("Ink Area (sq in)", 16, lambda m: f"{m.ink_area_sq_in:.4f}"),
    ]

    if per_label and per_label[0].cost_breakdown is not None:
        columns: list[TextColumn[LabelMetrics]] = base + [
            TextColumn(
                "Ink Cost",
                10,
                lambda m: (
                    f"${m.cost_breakdown.ink_cost:>9.2f}" if m.cost_breakdown else ""
                ),
            ),
            TextColumn(
                "Substrate Cost",
                14,
                lambda m: (
                    f"${m.cost_breakdown.substrate_cost:>12.2f}"
                    if m.cost_breakdown
                    else ""
                ),
            ),
            TextColumn(
                "Printer Cost",
                12,
                lambda m: (
                    f"${m.cost_breakdown.printer_cost:>10.2f}"
                    if m.cost_breakdown
                    else ""
                ),
            ),
            TextColumn(
                "Labor Cost",
                10,
                lambda m: (
                    f"${m.cost_breakdown.labor_cost:>8.2f}" if m.cost_breakdown else ""
                ),
            ),
            TextColumn(
                "Total Cost",
                10,
                lambda m: (
                    f"${m.cost_breakdown.total_cost:>8.2f}" if m.cost_breakdown else ""
                ),
            ),
        ]
    else:
        columns = base + [
            TextColumn(
                "Ink Area (sq ft)", 16, lambda m: f"{sq_ft(m.ink_area_sq_in):.4f}"
            )
        ]

    return render_text_table(columns, per_label)


def _metrics_columns(
    label_to_pdf: dict[str, str],
) -> list[Column[LabelMetrics]]:
    """Column definitions for the per-job CSV (internal + customer views)."""

    def cost(metric: LabelMetrics, attr: str) -> str:
        cb = metric.cost_breakdown
        if cb is None:
            return ""
        return f"{float(getattr(cb, attr)):.2f}"

    def materials(metric: LabelMetrics) -> str:
        """Customer-facing "price": materials only, excluding labour and markup."""
        cb = metric.cost_breakdown
        if cb is None:
            return ""
        return f"{cb.ink_cost + cb.substrate_cost:.2f}"

    return [
        Column("File", lambda m: label_to_pdf.get(m.text, "")),
        Column("Label Code", lambda m: m.text),
        Column("Char Count", lambda m: str(m.char_count), "char_count"),
        Column("Scale", lambda m: f"{m.horizontal_scale:.3f}", "scale"),
        Column("Ink Area (sq in)", lambda m: f"{m.ink_area_sq_in:.4f}", "ink_sq_in"),
        Column(
            "Ink Area (sq ft)", lambda m: f"{sq_ft(m.ink_area_sq_in):.4f}", "ink_sq_ft"
        ),
        Column("Ink Cost ($)", lambda m: cost(m, "ink_cost"), "ink_cost"),
        Column(
            "Substrate Cost ($)", lambda m: cost(m, "substrate_cost"), "substrate_cost"
        ),
        Column("Printer Hours", lambda m: cost(m, "printer_hours"), "printer_hours"),
        Column("Printer Cost ($)", lambda m: cost(m, "printer_cost"), "printer_cost"),
        Column("Labor Hours", lambda m: cost(m, "labor_hours"), "labor_hours"),
        Column("Labor Cost ($)", lambda m: cost(m, "labor_cost"), "labor_cost"),
        Column("Total Cost ($)", lambda m: cost(m, "total_cost"), "total_cost"),
        # "Price" is a customer-facing term (materials only) and has no internal
        # counterpart, unlike the consolidated reports where it means unit price.
        Column(
            "Price ($)",
            materials,
            "price",
            customer_only=True,
        ),
        Column("Unit Price ($)", lambda m: cost(m, "unit_price"), "unit_price"),
    ]


def write_metrics_csv(
    per_label: list[LabelMetrics],
    output_path: Path,
    pdf_filenames: list[str],
    page_break_ranges: list[tuple[int, int]] | None,
    config: JobConfig,
    *,
    include_costs: bool = True,
    customer_report_config: dict[str, bool] | None = None,
) -> None:
    """Write per-label metrics to CSV, with a ``File`` column per label's PDF.

    Internal output carries every column; customer output is filtered by
    ``customer_report_config`` (defaulting to just the unit price).
    """
    label_to_pdf = label_to_pdf_mapping(
        per_label, pdf_filenames, page_break_ranges, config.copies_per_label
    )
    write_csv_table(
        output_path,
        _metrics_columns(label_to_pdf),
        per_label,
        include_costs=include_costs,
        customer_config=customer_report_config,
        default_customer_keys=("unit_price",),
    )


def write_metrics_report(
    per_label: list[LabelMetrics],
    global_metrics: GlobalMetrics,
    txt_path: Path,
    json_path: Path,
    csv_path: Path,
    csv_customer_path: Path,
    input_path: Path,
    pdf_paths: list[Path],
    config: JobConfig,
    page_break_ranges: list[tuple[int, int]] | None = None,
    customer_report_config: dict[str, bool] | None = None,
) -> Path:
    """Write the text, JSON, and CSV per-job reports; return the JSON path."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    write_metrics_report_txt(
        per_label, global_metrics, txt_path, input_path, pdf_paths, config, timestamp
    )
    write_metrics_report_json(
        per_label,
        global_metrics,
        json_path,
        input_path,
        pdf_paths,
        config,
        timestamp,
        page_break_ranges=page_break_ranges,
    )

    pdf_filenames = [p.name for p in pdf_paths]
    write_metrics_csv(
        per_label,
        csv_path,
        pdf_filenames,
        page_break_ranges,
        config,
        include_costs=True,
    )
    write_metrics_csv(
        per_label,
        csv_customer_path,
        pdf_filenames,
        page_break_ranges,
        config,
        include_costs=False,
        customer_report_config=customer_report_config,
    )
    return json_path
