"""Consolidated report writers: JSON, the headline text report, and per-label listings.

The text report is the human-facing deliverable; the JSON sibling is the
machine-readable one, and is itself re-read by ``consolidate`` -- which is why it
carries the ``report_type`` marker that keeps re-runs idempotent.

Every table here is declared once as a list of
:class:`~vinyllabels.reportio.table.TextColumn` and rendered by the shared
engine, so a column added to a report is added in exactly one place.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from vinyllabels.consolidate.merge import (
    build_pdf_file_records,
    display_input_file,
    job_name,
    roll_widths_in,
    sort_pdf_records,
)
from vinyllabels.consolidate.model import (
    ConsolidatedLabel,
    ConsolidatedMetrics,
    JobReport,
    PdfFileRecord,
    SizeAreas,
)
from vinyllabels.job_report import label_to_pdf_mapping
from vinyllabels.models import CostBreakdown, LabelMetrics
from vinyllabels.reportio.table import (
    TextColumn,
    render_rule_table,
    render_text_table,
)
from vinyllabels.reportio.xlsx import Sheet, XlsxColumn, write_workbook
from vinyllabels.sizes import LabelSize, format_label_size, label_sizes_to_json
from vinyllabels.units import sq_ft, with_sq_ft

__all__ = [
    "write_consolidated_report",
    "write_consolidated_report_json",
    "write_per_label_report",
    "write_per_label_report_xlsx",
]

_RULE = "=" * 80
_SUBRULE = "-" * 80

# A size-breakdown row is a design size plus the printed-label count for it.
_SizeRow = tuple[LabelSize, int]

# A file-breakdown row is a PDF record plus its 1-based row number in the table.
_FileRow = tuple[int, PdfFileRecord]


@dataclass(frozen=True)
class LabelRow:
    """One label as it appears in a per-label listing, tagged with its job.

    The listings walk every job's labels, so a row needs the job's display name,
    design size, and emitted PDF alongside the label's own metrics. ``pdf_file``
    is the basename of the PDF the label first appears in (blank when the job
    recorded no PDFs).
    """

    job: str
    label: LabelMetrics
    size: LabelSize | None = None
    pdf_file: str = ""

    @property
    def label_size(self) -> str:
        """The job's design size, rendered for display."""
        return format_label_size(self.size) if self.size else ""

    @property
    def label_size_sq_in(self) -> float:
        """Design area of one instance of this label."""
        return 0.0 if self.size is None else self.size[0] * self.size[1]

    @property
    def label_area_sq_ft(self) -> float:
        """Design area of one instance, in square feet."""
        return sq_ft(self.label_size_sq_in)

    @property
    def ink_sq_ft(self) -> float:
        """This instance's ink coverage, in square feet."""
        return sq_ft(self.label.ink_area_sq_in)

    @property
    def breakdown(self) -> CostBreakdown | None:
        """This label's pricing, when the job was written with a pricing config."""
        return self.label.cost_breakdown

    def cost(self, attr: str) -> str:
        """One cost field formatted to two decimals, blank without pricing."""
        if self.breakdown is None:
            return ""
        return f"{float(getattr(self.breakdown, attr)):.2f}"


# --- JSON ----------------------------------------------------------------------


def write_consolidated_report_json(
    metrics: ConsolidatedMetrics,
    sizes: Counter[LabelSize],
    size_areas: dict[LabelSize, SizeAreas],
    labels: list[ConsolidatedLabel],
    reports: list[JobReport],
    out_path: Path,
    directory: Path,
    timestamp: str,
) -> None:
    """Write a machine-readable consolidated report to ``out_path``.

    Mirrors the per-job schema (``job`` metadata plus ``global`` and
    ``per_label`` sections) and adds a ``jobs`` array carrying each contributing
    job's own totals, so downstream tools can still attribute usage per job.
    """
    report: dict[str, object] = {
        "job": {
            "report_type": "consolidated",
            "generated": timestamp,
            "source_directory": str(directory),
            "total_jobs": metrics.total_jobs,
            "report_files": [str(job.path) for job in reports],
        },
        "global": with_sq_ft(asdict(metrics)),
        "label_sizes": [
            {
                "width_in": width_in,
                "height_in": height_in,
                "labels": count,
                **with_sq_ft(
                    {
                        "substrate_sq_in": size_areas[
                            (width_in, height_in)
                        ].substrate_sq_in,
                        "label_material_sq_in": size_areas[
                            (width_in, height_in)
                        ].label_material_sq_in,
                        "ink_area_sq_in": size_areas[
                            (width_in, height_in)
                        ].ink_area_sq_in,
                    }
                ),
            }
            for (width_in, height_in), count in sorted(sizes.items())
        ],
        "jobs": [
            {
                "input_file": job.input_file,
                "report_file": str(job.path),
                "page_width_in": job.page_width_in,
                "copies_per_label": job.copies_per_label,
                "global": with_sq_ft(asdict(job.global_metrics)),
                "label_sizes": label_sizes_to_json(job.label_sizes),
            }
            for job in reports
        ],
        "per_label": [with_sq_ft(asdict(label)) for label in labels],
    }
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


# --- Consolidated text report --------------------------------------------------


def write_consolidated_report(
    metrics: ConsolidatedMetrics,
    sizes: Counter[LabelSize],
    size_areas: dict[LabelSize, SizeAreas],
    labels: list[ConsolidatedLabel],
    reports: list[JobReport],
    out_path: Path,
    directory: Path,
    json_path: Path | None = None,
) -> Path:
    """Write the human-readable consolidated report, plus its JSON sibling.

    Returns the path of the JSON report.
    """
    if json_path is None:
        json_path = out_path.with_suffix(".json")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    roll_desc = "/".join(f"{width:g}in" for width in roll_widths_in(reports))

    lines: list[str] = [
        _RULE,
        "VINYL LABEL PRINTING REPORT - CONSOLIDATED".center(80),
        _RULE,
    ]
    lines.append(f"Generated:         {timestamp}")
    lines.append(f"Source Directory:  {directory}")
    lines.append(f"Jobs Consolidated: {metrics.total_jobs}")
    lines.append("")

    lines += [_SUBRULE, "GLOBAL PRINTING METRICS", _SUBRULE]
    lines.append(
        f"Total Substrate Required:  {metrics.total_substrate_sq_in:>10.2f} sq in"
        f" ({sq_ft(metrics.total_substrate_sq_in):>9.2f} sq ft)"
    )
    lines.append(f"{'':27}({metrics.linear_feet:.2f} linear feet of {roll_desc} roll)")
    lines.append(
        f"Total Label Area:          {metrics.total_label_material_sq_in:>10.2f} sq in"
        f" ({sq_ft(metrics.total_label_material_sq_in):>9.2f} sq ft)"
    )
    lines.append(f"Material Yield:            {metrics.material_yield_pct:>10.2f} %")
    lines.append("")

    lines += [_SUBRULE, "INK USAGE METRICS", _SUBRULE]
    lines.append(
        f"Total Ink Area:            {metrics.total_ink_area_sq_in:>10.2f} sq in"
        f" ({sq_ft(metrics.total_ink_area_sq_in):>9.2f} sq ft)"
    )
    lines.append(
        f"Average Ink Coverage:      {metrics.average_ink_coverage_pct:>10.2f} %"
    )
    lines.append(f"Total Character Count:     {metrics.total_characters:>10d}")
    lines.append(f"Total Output Labels:       {metrics.total_output_labels:>10d}")
    lines.append("")

    if metrics.cost_breakdown is not None:
        lines += [_SUBRULE, "COST BREAKDOWN", _SUBRULE]
        lines += _cost_lines(metrics.cost_breakdown)
        lines.append("")

    jobs_per_size, files_per_size = _size_counts(reports)
    size_rows: list[_SizeRow] = [(size, count) for size, count in sorted(sizes.items())]

    lines += [_SUBRULE, "LABEL SIZE BREAKDOWN", _SUBRULE]
    lines += _size_breakdown_table(
        metrics,
        size_areas,
        jobs_per_size,
        files_per_size,
        size_rows,
        with_costs=any(area.cost_breakdown is not None for area in size_areas.values()),
    )
    lines.append("")

    lines += [_SUBRULE, "VENDOR LABEL SIZE BREAKDOWN", _SUBRULE]
    lines += _size_breakdown_table(
        metrics, size_areas, jobs_per_size, files_per_size, size_rows, with_costs=False
    )
    lines.append("")

    pdf_records = sort_pdf_records(build_pdf_file_records(reports))
    if pdf_records:
        lines += [_SUBRULE, "FILE BREAKDOWN", _SUBRULE]
        lines += _file_breakdown_table(pdf_records, with_filenames=True)
        lines.append("")
        lines += [_SUBRULE, "VENDOR FILE BREAKDOWN", _SUBRULE]
        lines += _file_breakdown_table(pdf_records, with_filenames=False)
        lines.append("")

    lines += [_SUBRULE, "JOB BREAKDOWN", _SUBRULE]
    lines += _job_breakdown_table(reports, directory)
    lines.append("")

    lines += [_SUBRULE, "PER-LABEL BREAKDOWN", _SUBRULE]
    lines += _per_label_breakdown_table(labels)
    lines.append(_RULE)
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    write_consolidated_report_json(
        metrics, sizes, size_areas, labels, reports, json_path, directory, timestamp
    )
    return json_path


def _cost_lines(breakdown: CostBreakdown) -> list[str]:
    """The COST BREAKDOWN body; the last line is a total, not a unit price."""
    return [
        f"Ink Cost:                  ${breakdown.ink_cost:>10.2f}",
        f"Substrate Cost:            ${breakdown.substrate_cost:>10.2f}",
        f"Printer Hours:             {breakdown.printer_hours:>10.2f} hrs",
        f"Printer Cost:              ${breakdown.printer_cost:>10.2f}",
        f"Labor Hours:               {breakdown.labor_hours:>10.2f} hrs",
        f"Labor Cost:                ${breakdown.labor_cost:>10.2f}",
        f"Total Cost:                ${breakdown.total_cost:>10.2f}",
        f"Total Price:               ${breakdown.unit_price:>10.2f}",
    ]


def _size_counts(
    reports: list[JobReport],
) -> tuple[Counter[LabelSize], Counter[LabelSize]]:
    """Per-size counts of contributing jobs and emitted PDF files."""
    jobs: Counter[LabelSize] = Counter()
    files: Counter[LabelSize] = Counter()
    for report in reports:
        jobs.update(report.label_sizes.keys())
        for size in report.label_sizes:
            files[size] += report.num_pdf_files
    return jobs, files


# Header, column width, and CostBreakdown field for each size-breakdown cost column.
_SIZE_COST_COLUMNS: tuple[tuple[str, int, str], ...] = (
    ("Substrate Cost", 14, "substrate_cost"),
    ("Ink Cost", 10, "ink_cost"),
    ("Printer Cost", 12, "printer_cost"),
    ("Labor Cost", 10, "labor_cost"),
    ("Total Cost", 10, "total_cost"),
)


def _size_breakdown_table(
    metrics: ConsolidatedMetrics,
    size_areas: dict[LabelSize, SizeAreas],
    jobs_per_size: Counter[LabelSize],
    files_per_size: Counter[LabelSize],
    rows: Sequence[_SizeRow],
    *,
    with_costs: bool,
) -> list[str]:
    """Render LABEL SIZE BREAKDOWN, or its cost-free vendor twin.

    The totals row reports the consolidated figures rather than re-summing the
    buckets, so a rounding difference between the two stays visible instead of
    being silently smoothed over.
    """
    columns: list[TextColumn[_SizeRow]] = [
        TextColumn(
            "Label Size (WxH)", 16, lambda row: format_label_size(row[0]), "left"
        ),
        TextColumn(
            "Jobs",
            4,
            lambda row: f"{jobs_per_size[row[0]]:d}",
            total=lambda rows: f"{metrics.total_jobs:d}",
        ),
        TextColumn(
            "Files",
            5,
            lambda row: f"{files_per_size[row[0]]:d}",
            total=lambda rows: f"{sum(files_per_size.values()):d}",
        ),
        TextColumn(
            "Labels",
            6,
            lambda row: f"{row[1]:d}",
            total=lambda rows: f"{metrics.total_output_labels:d}",
        ),
        TextColumn(
            "Lin Ft",
            7,
            lambda row: f"{size_areas[row[0]].linear_feet:.2f}",
            total=lambda rows: f"{metrics.linear_feet:.2f}",
        ),
        TextColumn(
            "Substrate (sq ft)",
            17,
            lambda row: f"{sq_ft(size_areas[row[0]].substrate_sq_in):.2f}",
            total=lambda rows: f"{sq_ft(metrics.total_substrate_sq_in):.2f}",
        ),
        TextColumn(
            "Label Area (sq ft)",
            18,
            lambda row: f"{sq_ft(size_areas[row[0]].label_material_sq_in):.2f}",
            total=lambda rows: f"{sq_ft(metrics.total_label_material_sq_in):.2f}",
        ),
        TextColumn(
            "Ink (sq ft)",
            11,
            lambda row: f"{sq_ft(size_areas[row[0]].ink_area_sq_in):.2f}",
            total=lambda rows: f"{sq_ft(metrics.total_ink_area_sq_in):.2f}",
        ),
    ]

    if with_costs:
        columns += [
            TextColumn(
                header,
                width,
                _bucket_cost_cell(size_areas, attr, width),
                total=_breakdown_cost_cell(metrics.cost_breakdown, attr, width),
            )
            for header, width, attr in _SIZE_COST_COLUMNS
        ]

    return render_text_table(columns, list(rows), total_label="Total")


def _bucket_cost_cell(
    size_areas: dict[LabelSize, SizeAreas], attr: str, width: int
) -> Callable[[_SizeRow], str]:
    """Cell formatter reading one cost field of a size bucket's breakdown."""

    def cell(row: _SizeRow) -> str:
        return _money(size_areas[row[0]].cost_breakdown, attr, width)

    return cell


def _breakdown_cost_cell(
    breakdown: CostBreakdown | None, attr: str, width: int
) -> Callable[[Sequence[_SizeRow]], str]:
    """Totals-row formatter reading one cost field of a breakdown."""

    def total(rows: Sequence[_SizeRow]) -> str:
        return _money(breakdown, attr, width)

    return total


def _money(breakdown: CostBreakdown | None, attr: str, width: int) -> str:
    """One currency cell, right-aligned inside ``width`` including the sign."""
    if breakdown is None:
        return ""
    return f"${float(getattr(breakdown, attr)):>{width - 1}.2f}"


def _file_breakdown_table(
    records: list[PdfFileRecord], *, with_filenames: bool
) -> list[str]:
    """Render FILE BREAKDOWN, or the vendor twin with the filename column dropped.

    Both variants keep the size column and number their rows in the
    vendor-friendly (size, filename) order, matching the file-breakdown CSVs and
    the numbering used for the vendor PDF drop folder.
    """
    rows: list[_FileRow] = list(enumerate(sort_pdf_records(records), start=1))
    name_width = max((len(record.filename) for _, record in rows), default=80)

    columns: list[TextColumn[_FileRow]] = [
        TextColumn("File #", 6, lambda row: str(row[0])),
    ]
    if with_filenames:
        columns.append(
            TextColumn("Filename", name_width, lambda row: row[1].filename, "left")
        )
    columns.append(TextColumn("Label WxH", 9, lambda row: row[1].label_size))

    columns += [
        TextColumn(
            "Labels",
            6,
            lambda row: f"{row[1].total_output_labels:d}",
            total=lambda rows: f"{sum(r[1].total_output_labels for r in rows):d}",
        ),
        TextColumn(
            "Lin Ft",
            7,
            lambda row: f"{row[1].linear_feet:.2f}",
            total=lambda rows: f"{sum(r[1].linear_feet for r in rows):.2f}",
        ),
        TextColumn(
            "Substrate (sq ft)",
            17,
            lambda row: f"{sq_ft(row[1].total_substrate_sq_in):.2f}",
            total=lambda rows: (
                f"{sum(sq_ft(r[1].total_substrate_sq_in) for r in rows):.2f}"
            ),
        ),
        TextColumn(
            "Label Area (sq ft)",
            17,
            lambda row: f"{sq_ft(row[1].total_label_material_sq_in):.2f}",
            total=lambda rows: (
                f"{sum(sq_ft(r[1].total_label_material_sq_in) for r in rows):.2f}"
            ),
        ),
        TextColumn(
            "Ink (sq in)",
            11,
            lambda row: f"{row[1].total_ink_area_sq_in:.2f}",
            total=lambda rows: f"{sum(r[1].total_ink_area_sq_in for r in rows):.2f}",
        ),
        TextColumn(
            "Ink (sq ft)",
            11,
            lambda row: f"{sq_ft(row[1].total_ink_area_sq_in):.2f}",
            total=lambda rows: (
                f"{sum(sq_ft(r[1].total_ink_area_sq_in) for r in rows):.2f}"
            ),
        ),
    ]
    # The first column is a row number, so the totals label belongs to the next:    # the filename when it is shown, otherwise the size.
    return render_text_table(columns, rows, total_label="TOTAL", total_label_index=1)


def _job_breakdown_table(reports: list[JobReport], directory: Path) -> list[str]:
    """Render JOB BREAKDOWN: one row per contributing job."""
    root = directory.parent

    def name(report: JobReport) -> str:
        return display_input_file(report.input_file, root)

    def substrate_sq_ft(report: JobReport) -> float:
        return sq_ft(report.global_metrics.total_substrate_sq_in)

    def material_sq_ft(report: JobReport) -> float:
        return sq_ft(report.global_metrics.total_label_material_sq_in)

    def ink_sq_ft(report: JobReport) -> float:
        return sq_ft(report.global_metrics.total_ink_area_sq_in)

    name_width = max((len(name(report)) for report in reports), default=33)
    columns: list[TextColumn[JobReport]] = [
        TextColumn("Input File", name_width, name, "left"),
        TextColumn(
            "WxH",
            5,
            lambda r: format_label_size(r.label_size) if r.label_size else "",
        ),
        TextColumn(
            "# Files",
            7,
            lambda r: f"{r.num_pdf_files:d}",
            total=lambda rows: f"{sum(r.num_pdf_files for r in rows):d}",
        ),
        TextColumn("Roll", 5, lambda r: f"{r.page_width_in:.0f}"),
        TextColumn(
            "Labels",
            6,
            lambda r: f"{r.global_metrics.total_output_labels:d}",
            total=lambda rows: (
                f"{sum(r.global_metrics.total_output_labels for r in rows):d}"
            ),
        ),
        TextColumn(
            "Substrate (sq ft)",
            17,
            lambda r: f"{substrate_sq_ft(r):.2f}",
            total=lambda rows: f"{sum(substrate_sq_ft(r) for r in rows):.2f}",
        ),
        TextColumn(
            "Label Area (sq ft)",
            17,
            lambda r: f"{material_sq_ft(r):.2f}",
            total=lambda rows: f"{sum(material_sq_ft(r) for r in rows):.2f}",
        ),
        TextColumn(
            "Lin Ft",
            9,
            lambda r: f"{r.global_metrics.linear_feet:.2f}",
            total=lambda rows: f"{sum(r.global_metrics.linear_feet for r in rows):.2f}",
        ),
        TextColumn(
            "Ink (sq in)",
            11,
            lambda r: f"{r.global_metrics.total_ink_area_sq_in:.2f}",
            total=lambda rows: (
                f"{sum(r.global_metrics.total_ink_area_sq_in for r in rows):.2f}"
            ),
        ),
        TextColumn(
            "Ink (sq ft)",
            11,
            lambda r: f"{ink_sq_ft(r):.2f}",
            total=lambda rows: f"{sum(ink_sq_ft(r) for r in rows):.2f}",
        ),
    ]
    return render_text_table(columns, reports, total_label="TOTAL")


def _per_label_breakdown_table(labels: list[ConsolidatedLabel]) -> list[str]:
    """Render PER-LABEL BREAKDOWN: one row per unique label code."""
    code_width = max(max((len(label.text) for label in labels), default=16), 10)

    columns: list[TextColumn[ConsolidatedLabel]] = [
        TextColumn(
            "Label Code",
            code_width,
            lambda r: r.text,
            "left",
            total=lambda rows: "TOTAL",
        ),
        TextColumn(
            "Jobs",
            4,
            lambda r: f"{r.jobs:d}",
            total=lambda rows: f"{sum(r.jobs for r in rows):d}",
        ),
        TextColumn(
            "Copies",
            6,
            lambda r: f"{r.instances:d}",
            total=lambda rows: f"{sum(r.instances for r in rows):d}",
        ),
        TextColumn(
            "Chars",
            5,
            lambda r: f"{r.char_count:d}",
            total=lambda rows: f"{sum(r.char_count for r in rows):d}",
        ),
        TextColumn(
            "Label Area (sq ft)",
            18,
            lambda r: f"{sq_ft(r.label_area_sq_in):.4f}",
            total=lambda rows: f"{sum(sq_ft(r.label_area_sq_in) for r in rows):.4f}",
        ),
        TextColumn(
            "Substrate (sq ft)",
            17,
            lambda r: f"{sq_ft(r.substrate_sq_in):.2f}",
            total=lambda rows: f"{sum(sq_ft(r.substrate_sq_in) for r in rows):.2f}",
        ),
        TextColumn(
            "Ink Area (sq in)",
            16,
            lambda r: f"{r.ink_area_sq_in:.4f}",
            total=lambda rows: f"{sum(r.ink_area_sq_in for r in rows):.4f}",
        ),
        TextColumn(
            "Ink Area (sq ft)",
            16,
            lambda r: f"{sq_ft(r.ink_area_sq_in):.4f}",
            total=lambda rows: f"{sum(sq_ft(r.ink_area_sq_in) for r in rows):.4f}",
        ),
    ]
    return render_text_table(columns, labels, total_label="TOTAL")


# --- Per-label listings --------------------------------------------------------


# Header, width, and CostBreakdown field for each internal per-label cost column.
_LABEL_COST_COLUMNS: tuple[tuple[str, int, str], ...] = (
    ("Ink ($)", 10, "ink_cost"),
    ("Substrate ($)", 13, "substrate_cost"),
    ("Print Hrs", 9, "printer_hours"),
    ("Print ($)", 10, "printer_cost"),
    ("Labor Hrs", 9, "labor_hours"),
    ("Labor ($)", 10, "labor_cost"),
    ("Total ($)", 10, "total_cost"),
    ("Unit ($)", 10, "unit_price"),
)


def _pdf_file_by_label(report: JobReport) -> dict[str, str]:
    """Map each label code in ``report`` to the PDF basename it first appears in.

    The per-job report stores its emitted PDFs in print order and, for split
    runs, ``per_pdf`` metrics saying how many instances each PDF holds; walking
    those counts rebuilds the instance ranges :func:`label_to_pdf_mapping`
    needs. Without ``per_pdf`` metrics every code maps to the first PDF, and a
    job that recorded no PDFs at all maps to nothing.
    """
    filenames = [path.name for path in report.output_pdf_files or ()]
    if not filenames:
        return {}
    ranges: list[tuple[int, int]] | None = None
    if report.per_pdf_metrics:
        ranges = []
        start = 0
        for metric in report.per_pdf_metrics:
            ranges.append((start, start + metric.total_output_labels - 1))
            start += metric.total_output_labels
    return label_to_pdf_mapping(
        report.per_label, filenames, ranges, report.copies_per_label
    )


def _label_rows(reports: list[JobReport], directory: Path) -> list[LabelRow]:
    """Flatten every job's labels into rows tagged with the job, size, and PDF."""
    rows: list[LabelRow] = []
    for report in reports:
        name = job_name(report.input_file, directory.parent)
        files = _pdf_file_by_label(report)
        rows.extend(
            LabelRow(name, label, report.label_size, files.get(label.text, ""))
            for label in report.per_label
        )
    return rows


def write_per_label_report(
    reports: list[JobReport],
    output_path: Path,
    directory: Path,
    include_costs: bool = True,
) -> None:
    """Write one row per label per job, tagged with the job and PDF it came from.

    Internal output carries the full cost breakdown; customer output is just the
    unit price.
    """
    rows = _label_rows(reports, directory)
    file_width = max((len(row.pdf_file) for row in rows), default=4)
    identity: list[TextColumn[LabelRow]] = [
        TextColumn("Job", 30, lambda row: row.job, "left"),
        TextColumn("Label Code", 16, lambda row: row.label.text[:16], "left"),
        TextColumn("File", max(file_width, 4), lambda row: row.pdf_file, "left"),
    ]
    if include_costs:
        columns = [
            *identity,
            TextColumn("Char Count", 5, lambda row: f"{row.label.char_count:d}"),
            TextColumn("Scale", 7, lambda row: f"{row.label.horizontal_scale:.3f}"),
            TextColumn(
                "Ink (sq in)", 12, lambda row: f"{row.label.ink_area_sq_in:.4f}"
            ),
            TextColumn("Ink (sq ft)", 11, lambda row: f"{row.ink_sq_ft:.4f}"),
            *(
                TextColumn(header, width, _cost_cell(attr))
                for header, width, attr in _LABEL_COST_COLUMNS
            ),
        ]
        rule = "-" * 180
    else:
        columns = [
            *identity,
            TextColumn("Unit Price ($)", 12, _cost_cell("unit_price")),
        ]
        rule = "-" * 60

    output_path.write_text(
        "\n".join(
            [
                *render_rule_table(columns, rows, rule=rule),
                "",
            ]
        ),
        encoding="utf-8",
    )


def _cost_cell(attr: str) -> Callable[[LabelRow], str]:
    """Cell formatter reading one formatted cost field of a row's breakdown."""

    def cell(row: LabelRow) -> str:
        return row.cost(attr)

    return cell


# --- Per-label workbooks -------------------------------------------------------

# Config key -> (header, field) for the customer workbook's optional columns.
_CUSTOMER_XLSX_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("label_size_wxh", "Label Size (WxH)", "label_size"),
    ("char_count", "Char Count", "char_count"),
    ("scale", "Scale", "horizontal_scale"),
    ("label_size_sq_in", "Label Size (sq in)", "label_size_sq_in"),
    ("label_area_sq_ft", "Label Area (sq ft)", "label_area_sq_ft"),
    ("ink_sq_in", "Ink (sq in)", "ink_area_sq_in"),
    ("ink_sq_ft", "Ink (sq ft)", "ink_sq_ft"),
    ("ink_cost", "Ink Cost ($)", "ink_cost"),
    ("substrate_cost", "Substrate Cost ($)", "substrate_cost"),
    ("printer_hours", "Printer Hours", "printer_hours"),
    ("printer_cost", "Printer Cost ($)", "printer_cost"),
    ("labor_hours", "Labor Hours", "labor_hours"),
    ("labor_cost", "Labor Cost ($)", "labor_cost"),
    ("total_cost", "Total Cost ($)", "total_cost"),
    ("price", "Price ($)", "unit_price"),
    ("unit_price", "Unit Price ($)", "unit_price"),
)

# Fields read straight off the label rather than its pricing.
_LABEL_FIELDS = frozenset({"char_count", "horizontal_scale", "ink_area_sq_in"})


def _row_value(row: LabelRow, key: str) -> float | int | str | None:
    """Read one workbook field off a row, preferring the label over its pricing."""
    if key == "label_size":
        return row.label_size
    if key == "label_size_sq_in":
        return row.label_size_sq_in
    if key == "label_area_sq_ft":
        return row.label_area_sq_ft
    if key == "ink_sq_ft":
        return row.ink_sq_ft
    if key in _LABEL_FIELDS:
        value = getattr(row.label, key)
        return value if isinstance(value, float | int | str) else None
    return None if row.breakdown is None else float(getattr(row.breakdown, key))


def _cell(key: str) -> Callable[[LabelRow], float | int | str | None]:
    """Workbook cell formatter reading one field of a row."""

    def cell(row: LabelRow) -> float | int | str | None:
        return _row_value(row, key)

    return cell


def _pricing_cell(attr: str) -> Callable[[LabelRow], float | int | str | None]:
    """Workbook cell reading one numeric pricing field, blank without pricing."""

    def cell(row: LabelRow) -> float | int | str | None:
        return None if row.breakdown is None else float(getattr(row.breakdown, attr))

    return cell


def _internal_xlsx_columns() -> list[XlsxColumn[LabelRow]]:
    """Column definitions for the internal per-label workbook."""
    return [
        XlsxColumn("Label Code", lambda row: row.label.text),
        XlsxColumn("File", lambda row: row.pdf_file),
        XlsxColumn("Label Size (WxH)", lambda row: row.label_size),
        XlsxColumn("Char Count", lambda row: row.label.char_count),
        XlsxColumn("Scale", lambda row: row.label.horizontal_scale),
        XlsxColumn("Ink (sq in)", lambda row: row.label.ink_area_sq_in),
        XlsxColumn("Ink (sq ft)", lambda row: row.ink_sq_ft),
        XlsxColumn("Ink ($)", _pricing_cell("ink_cost")),
        XlsxColumn("Substrate ($)", _pricing_cell("substrate_cost")),
        XlsxColumn("Print Hrs", _pricing_cell("printer_hours")),
        XlsxColumn("Print ($)", _pricing_cell("printer_cost")),
        XlsxColumn("Labor Hrs", _pricing_cell("labor_hours")),
        XlsxColumn("Labor ($)", _pricing_cell("labor_cost")),
        XlsxColumn("Total ($)", _pricing_cell("total_cost")),
        XlsxColumn("Unit ($)", _pricing_cell("unit_price")),
    ]


def _customer_xlsx_columns() -> list[XlsxColumn[LabelRow]]:
    """Column definitions for the customer per-label workbook."""
    return [
        XlsxColumn("Label Code", lambda row: row.label.text),
        XlsxColumn("File", lambda row: row.pdf_file),
        *[
            XlsxColumn(header, _cell(key), config_key)
            for config_key, header, key in _CUSTOMER_XLSX_COLUMNS
        ],
    ]


def write_per_label_report_xlsx(
    reports: list[JobReport],
    output_path: Path,
    directory: Path,
    include_costs: bool = True,
    customer_report_config: dict[str, bool] | None = None,
) -> None:
    """Write one worksheet per job, styled with bold headers.

    Internal workbooks carry the full breakdown; customer workbooks show the
    columns enabled in ``customer_report_config``.
    """
    write_workbook(
        output_path,
        _workbook_sheets(reports, directory),
        _internal_xlsx_columns() if include_costs else _customer_xlsx_columns(),
        include_costs=include_costs,
        customer_config=customer_report_config,
        default_customer_keys=("unit_price",),
    )


def _workbook_sheets(
    reports: list[JobReport], directory: Path
) -> list[Sheet[LabelRow]]:
    """One sheet per job, named after its input file."""
    sheets: list[Sheet[LabelRow]] = []
    for report in reports:
        name = job_name(report.input_file, directory.parent)
        files = _pdf_file_by_label(report)
        rows = [
            LabelRow(name, label, report.label_size, files.get(label.text, ""))
            for label in report.per_label
        ]
        sheets.append(Sheet(name, rows))
    return sheets
