"""CSV breakdowns for a consolidated run.

Six files, each in an internal and a customer variant (plus two vendor-only
ones). They all share the shape the table engine models -- ordered columns, a
per-row formatter, an optional totals row, and a customer subset -- so each is
declared as a list of :class:`~vinyllabels.reportio.table.Column` and written by
:func:`~vinyllabels.reportio.table.write_csv_table`.

Two conventions are worth knowing before editing these:

* A totals row sums the *displayed* quantities. Where a report has both a sq-in
  and a sq-ft column, some totals add the per-row sq-ft values and others convert
  the summed sq-in; both forms exist here because each keeps its total consistent
  with the column it sits in.
* Non-summable columns (ratios, unit prices, sizes) have no ``total`` and render
  blank in the totals row.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path

from vinyllabels.consolidate.merge import display_input_file, sort_pdf_records
from vinyllabels.consolidate.model import (
    ConsolidatedLabelBySize,
    JobReport,
    PdfFileRecord,
    SizeAreas,
)
from vinyllabels.models import CostBreakdown
from vinyllabels.reportio.table import Column, write_csv_table
from vinyllabels.sizes import LabelSize, format_label_size
from vinyllabels.units import sq_ft

__all__ = [
    "write_job_breakdown_csv",
    "write_pdf_file_breakdown_csv",
    "write_pdf_file_breakdown_vendor_csv",
    "write_per_label_breakdown_csv",
    "write_size_breakdown_csv",
    "write_vendor_label_size_breakdown_csv",
]

# A size-breakdown row is a design size plus the printed-label count for it.
_SizeRow = tuple[LabelSize, int]

# A file-breakdown row is a PDF record plus its 1-based position in the report.
_FileRow = tuple[int, PdfFileRecord]

# Assumed text height when no contributing job recorded one.
_DEFAULT_TEXT_HEIGHT_IN = 2.0


def _sum_rows[T](
    rows: Sequence[T], value: Callable[[T], float], *, digits: int = 2
) -> str:
    """Format the sum of ``value`` over ``rows``."""
    return f"{sum(value(row) for row in rows):.{digits}f}"


def _as_sq_ft[T](value: Callable[[T], float]) -> Callable[[T], float]:
    """Lift a square-inch accessor into a square-foot one."""

    def converted(row: T) -> float:
        return sq_ft(value(row))

    return converted


def _formatted[T](
    value: Callable[[T], float], *, digits: int = 2
) -> Callable[[T], str]:
    """Row formatter rendering one numeric accessor with fixed precision."""

    def cell(row: T) -> str:
        return f"{value(row):.{digits}f}"

    return cell


def _total_of[T](
    value: Callable[[T], float], *, digits: int = 2
) -> Callable[[Sequence[T]], str]:
    """Totals formatter summing one numeric accessor across the rows."""

    def total(rows: Sequence[T]) -> str:
        return _sum_rows(rows, value, digits=digits)

    return total


def _cost_value[T](
    get_breakdown: Callable[[T], CostBreakdown | None], attr: str, digits: int
) -> Callable[[T], str]:
    """Per-row formatter for one cost field, blank when the row has no pricing."""

    def value(row: T) -> str:
        breakdown = get_breakdown(row)
        return (
            "" if breakdown is None else f"{float(getattr(breakdown, attr)):.{digits}f}"
        )

    return value


def _cost_total[T](
    get_breakdown: Callable[[T], CostBreakdown | None], attr: str, digits: int
) -> Callable[[Sequence[T]], str]:
    """Totals formatter summing one cost field over the priced rows."""

    def total(rows: Sequence[T]) -> str:
        subtotal = 0.0
        for row in rows:
            breakdown = get_breakdown(row)
            if breakdown is not None:
                subtotal += float(getattr(breakdown, attr))
        return f"{subtotal:.{digits}f}"

    return total


def _cost_column[T](
    header: str,
    attr: str,
    config_key: str,
    get_breakdown: Callable[[T], CostBreakdown | None],
    *,
    digits: int = 2,
    total_digits: int | None = None,
) -> Column[T]:
    """A priced column: same field per row and in the totals row.

    ``total_digits`` exists because a few reports print hours to four decimals
    per row but two in the totals row.
    """
    return Column(
        header,
        _cost_value(get_breakdown, attr, digits),
        config_key,
        _cost_total(get_breakdown, attr, total_digits if total_digits else digits),
    )


# --- Size breakdown ------------------------------------------------------------


def size_text_heights(reports: Sequence[JobReport]) -> dict[LabelSize, float]:
    """Text height per size, taken from the first job that printed it."""
    heights: dict[LabelSize, float] = {}
    for report in reports:
        size = report.label_size
        if size is not None:
            heights.setdefault(size, report.text_height_in)
    return heights


def write_size_breakdown_csv(
    sizes: Counter[LabelSize],
    size_areas: dict[LabelSize, SizeAreas],
    reports: Sequence[JobReport],
    output_path: Path,
    *,
    include_costs: bool = True,
    customer_report_config: dict[str, bool] | None = None,
) -> None:
    """Write the per-label-size breakdown, internal or customer variant."""
    heights = size_text_heights(reports)
    files_per_size: Counter[LabelSize] = Counter()
    for report in reports:
        for size in report.label_sizes:
            files_per_size[size] += report.num_pdf_files

    def breakdown(row: _SizeRow) -> CostBreakdown | None:
        return size_areas[row[0]].cost_breakdown

    area_fields: dict[str, Callable[[_SizeRow], float]] = {
        "substrate": lambda row: size_areas[row[0]].substrate_sq_in,
        "label_material": lambda row: size_areas[row[0]].label_material_sq_in,
        "ink": lambda row: size_areas[row[0]].ink_area_sq_in,
    }

    def unit_price(row: _SizeRow) -> str:
        priced = size_areas[row[0]].cost_breakdown
        if priced is None or row[1] <= 0:
            return ""
        return f"{priced.unit_price / row[1]:.2f}"

    columns: list[Column[_SizeRow]] = [
        Column("Label Size (WxH)", lambda row: format_label_size(row[0])),
        Column(
            "Text Height (in)",
            lambda row: f"{heights.get(row[0], _DEFAULT_TEXT_HEIGHT_IN):.2f}",
            "text_height_in",
        ),
        Column(
            "Files",
            lambda row: str(files_per_size.get(row[0], 0)),
            total=lambda rows: str(sum(files_per_size.values())),
        ),
        Column(
            "Labels",
            lambda row: str(row[1]),
            total=lambda rows: str(sum(row[1] for row in rows)),
        ),
    ]
    for header, field, config_key in (
        ("Substrate (sq ft)", "substrate", "substrate_sq_ft"),
        ("Label Area (sq ft)", "label_material", "label_area_sq_ft"),
        ("Ink (sq ft)", "ink", "ink_sq_ft"),
    ):
        sq_ft_of = _as_sq_ft(area_fields[field])
        columns.append(
            Column(header, _formatted(sq_ft_of), config_key, _total_of(sq_ft_of))
        )

    columns += [
        _cost_column(
            "Substrate Cost ($)", "substrate_cost", "substrate_cost", breakdown
        ),
        _cost_column("Ink Cost ($)", "ink_cost", "ink_cost", breakdown),
        _cost_column("Printer Hours", "printer_hours", "printer_hours", breakdown),
        _cost_column("Printer Cost ($)", "printer_cost", "printer_cost", breakdown),
        _cost_column("Labor Hours", "labor_hours", "labor_hours", breakdown),
        _cost_column("Labor Cost ($)", "labor_cost", "labor_cost", breakdown),
        _cost_column("Total Cost ($)", "total_cost", "total_cost", breakdown),
        _cost_column("Price ($)", "unit_price", "price", breakdown),
        Column(
            "Unit Price ($)",
            unit_price,
            "unit_price",
            _price_per_unit_total(breakdown),
        ),
    ]

    write_csv_table(
        output_path,
        columns,
        [(size, count) for size, count in sorted(sizes.items())],
        include_costs=include_costs,
        customer_config=customer_report_config,
        default_customer_keys=("price", "unit_price"),
        customer_order=_SIZE_CUSTOMER_ORDER,
        total_label="TOTAL",
    )


_SIZE_CUSTOMER_ORDER: tuple[str, ...] = (
    "Label Size (WxH)",
    "Files",
    "Labels",
    "Text Height (in)",
    "Substrate (sq ft)",
    "Label Area (sq ft)",
    "Ink (sq ft)",
    "Substrate Cost ($)",
    "Ink Cost ($)",
    "Printer Hours",
    "Printer Cost ($)",
    "Labor Hours",
    "Labor Cost ($)",
    "Total Cost ($)",
    "Price ($)",
    "Unit Price ($)",
)


def _price_per_unit_total[T](
    get_breakdown: Callable[[T], CostBreakdown | None],
) -> Callable[[Sequence[T]], str]:
    """Unit prices are not additive, so the totals row leaves them blank.

    The accessor is accepted for symmetry with the other total builders; a
    per-unit figure has no meaningful sum regardless of the rows.
    """

    def total(rows: Sequence[T]) -> str:
        return ""

    return total


def write_vendor_label_size_breakdown_csv(
    sizes: Counter[LabelSize],
    size_areas: dict[LabelSize, SizeAreas],
    reports: Sequence[JobReport],
    output_path: Path,
) -> None:
    """Write the vendor size breakdown: counts and areas, no pricing."""
    jobs_per_size: Counter[LabelSize] = Counter()
    files_per_size: Counter[LabelSize] = Counter()
    for report in reports:
        jobs_per_size.update(report.label_sizes.keys())
        for size in report.label_sizes:
            files_per_size[size] += report.num_pdf_files

    area_fields: dict[str, Callable[[_SizeRow], float]] = {
        "substrate": lambda row: size_areas[row[0]].substrate_sq_in,
        "label_material": lambda row: size_areas[row[0]].label_material_sq_in,
        "ink": lambda row: size_areas[row[0]].ink_area_sq_in,
    }

    columns: list[Column[_SizeRow]] = [
        Column("Label Size (WxH)", lambda row: format_label_size(row[0])),
        Column(
            "Jobs",
            lambda row: str(jobs_per_size.get(row[0], 0)),
            total=lambda rows: str(len(reports)),
        ),
        Column(
            "Files",
            lambda row: str(files_per_size.get(row[0], 0)),
            total=lambda rows: str(sum(files_per_size.values())),
        ),
        Column(
            "Labels",
            lambda row: str(row[1]),
            total=lambda rows: str(sum(row[1] for row in rows)),
        ),
        Column(
            "Lin Ft",
            lambda row: f"{size_areas[row[0]].linear_feet:.2f}",
            None,
            total=lambda rows: _sum_rows(
                rows, lambda row: size_areas[row[0]].linear_feet
            ),
        ),
    ]
    for header, field in (
        ("Substrate (sq ft)", "substrate"),
        ("Label Area (sq ft)", "label_material"),
        ("Ink (sq ft)", "ink"),
    ):
        sq_ft_of = _as_sq_ft(area_fields[field])
        columns.append(Column(header, _formatted(sq_ft_of), None, _total_of(sq_ft_of)))

    write_csv_table(
        output_path,
        columns,
        [(size, count) for size, count in sorted(sizes.items())],
        total_label="TOTAL",
    )


# --- Job breakdown -------------------------------------------------------------


def write_job_breakdown_csv(
    reports: Sequence[JobReport],
    directory: Path,
    output_path: Path,
    *,
    include_costs: bool = True,
    customer_report_config: dict[str, bool] | None = None,
) -> None:
    """Write one row per contributing job, internal or customer variant."""
    root = directory.parent

    def breakdown(report: JobReport) -> CostBreakdown | None:
        return report.global_metrics.cost_breakdown

    def sizes_label(report: JobReport) -> str:
        if not report.label_sizes:
            return ""
        return ", ".join(
            format_label_size(size) for size in sorted(report.label_sizes.keys())
        )

    def unit_price(report: JobReport) -> str:
        priced = breakdown(report)
        total = report.global_metrics.total_output_labels
        if priced is None or total <= 0:
            return ""
        return f"{priced.unit_price / total:.2f}"

    def ink_sq_ft(report: JobReport) -> float:
        return sq_ft(report.global_metrics.total_ink_area_sq_in)

    def total_ink_sq_in(rows: Sequence[JobReport]) -> float:
        return sum(row.global_metrics.total_ink_area_sq_in for row in rows)

    columns: list[Column[JobReport]] = [
        Column("Input File", lambda row: display_input_file(row.input_file, root)),
        Column("Label Size (WxH)", sizes_label, "label_size_wxh"),
        Column(
            "Text Height (in)",
            lambda row: f"{row.text_height_in:.2f}",
            "text_height_in",
        ),
        Column(
            "Files",
            lambda row: str(row.num_pdf_files),
            total=lambda rows: str(sum(row.num_pdf_files for row in rows)),
        ),
        Column(
            "Labels",
            lambda row: str(row.global_metrics.total_output_labels),
            total=lambda rows: str(
                sum(row.global_metrics.total_output_labels for row in rows)
            ),
        ),
        Column(
            "Substrate (sq ft)",
            lambda row: f"{sq_ft(row.global_metrics.total_substrate_sq_in):.2f}",
            "substrate_sq_ft",
            total=lambda rows: _sum_rows(
                rows, lambda row: sq_ft(row.global_metrics.total_substrate_sq_in)
            ),
        ),
        Column(
            "Linear Feet",
            lambda row: f"{row.global_metrics.linear_feet:.2f}",
            "linear_feet",
            total=lambda rows: _sum_rows(
                rows, lambda row: row.global_metrics.linear_feet
            ),
            customer_only=True,
        ),
        Column(
            "Label Area (sq ft)",
            lambda row: f"{sq_ft(row.global_metrics.total_label_material_sq_in):.2f}",
            "label_area_sq_ft",
            total=lambda rows: _sum_rows(
                rows, lambda row: sq_ft(row.global_metrics.total_label_material_sq_in)
            ),
        ),
        Column(
            "Ink (sq in)",
            lambda row: f"{row.global_metrics.total_ink_area_sq_in:.2f}",
            "ink_sq_in",
            total=lambda rows: f"{total_ink_sq_in(rows):.2f}",
        ),
        Column(
            "Ink (sq ft)",
            lambda row: f"{ink_sq_ft(row):.2f}",
            "ink_sq_ft",
            total=lambda rows: f"{sq_ft(total_ink_sq_in(rows)):.2f}",
        ),
        _cost_column("Ink Cost ($)", "ink_cost", "ink_cost", breakdown),
        _cost_column(
            "Substrate Cost ($)", "substrate_cost", "substrate_cost", breakdown
        ),
        _cost_column("Printer Hours", "printer_hours", "printer_hours", breakdown),
        _cost_column("Printer Cost ($)", "printer_cost", "printer_cost", breakdown),
        _cost_column("Labor Hours", "labor_hours", "labor_hours", breakdown),
        _cost_column("Labor Cost ($)", "labor_cost", "labor_cost", breakdown),
        _cost_column("Total Cost ($)", "total_cost", "total_cost", breakdown),
        _cost_column("Price ($)", "unit_price", "price", breakdown),
        Column(
            "Unit Price ($)", unit_price, "unit_price", _price_per_unit_total(breakdown)
        ),
    ]

    write_csv_table(
        output_path,
        columns,
        list(reports),
        include_costs=include_costs,
        customer_config=customer_report_config,
        default_customer_keys=("label_area_sq_ft", "price", "unit_price"),
        customer_order=_JOB_CUSTOMER_ORDER,
        total_label="TOTAL",
    )


_JOB_CUSTOMER_ORDER: tuple[str, ...] = (
    "Input File",
    "Files",
    "Labels",
    "Label Size (WxH)",
    "Text Height (in)",
    "Substrate (sq ft)",
    "Linear Feet",
    "Label Area (sq ft)",
    "Ink (sq in)",
    "Ink (sq ft)",
    "Ink Cost ($)",
    "Substrate Cost ($)",
    "Printer Hours",
    "Printer Cost ($)",
    "Labor Hours",
    "Labor Cost ($)",
    "Total Cost ($)",
    "Price ($)",
    "Unit Price ($)",
)


# --- File breakdown ------------------------------------------------------------


def write_pdf_file_breakdown_csv(
    pdf_records: Sequence[PdfFileRecord],
    output_path: Path,
    *,
    include_costs: bool = True,
    customer_report_config: dict[str, bool] | None = None,
) -> None:
    """Write one row per emitted PDF, internal or customer variant.

    Rows are numbered in the vendor-friendly (size, filename) order the text
    report uses, so the ``File #`` matches the copies dropped in the vendor folder.
    """
    if not pdf_records:
        return

    rows: list[_FileRow] = [
        (number, record)
        for number, record in enumerate(sort_pdf_records(list(pdf_records)), start=1)
    ]

    def breakdown(row: _FileRow) -> CostBreakdown | None:
        return row[1].cost_breakdown

    def total_ink_sq_in(files: Sequence[_FileRow]) -> float:
        return sum(record.total_ink_area_sq_in for _, record in files)

    columns: list[Column[_FileRow]] = [
        Column("File #", lambda row: str(row[0])),
        Column("Filename", lambda row: row[1].filename),
        Column("Label Size (WxH)", lambda row: row[1].label_size, "label_size_wxh"),
        Column(
            "Labels",
            lambda row: str(row[1].total_output_labels),
            total=lambda rows: str(sum(row[1].total_output_labels for row in rows)),
        ),
        Column(
            "Lin Ft",
            lambda row: f"{row[1].linear_feet:.2f}",
            "linear_feet",
            total=lambda rows: _sum_rows(rows, lambda row: row[1].linear_feet),
        ),
        Column(
            "Substrate (sq ft)",
            lambda row: f"{sq_ft(row[1].total_substrate_sq_in):.2f}",
            "substrate_sq_ft",
            total=lambda rows: _sum_rows(
                rows, lambda row: sq_ft(row[1].total_substrate_sq_in)
            ),
        ),
        Column(
            "Label Area (sq ft)",
            lambda row: f"{sq_ft(row[1].total_label_material_sq_in):.2f}",
            "label_area_sq_ft",
            total=lambda rows: _sum_rows(
                rows, lambda row: sq_ft(row[1].total_label_material_sq_in)
            ),
        ),
        Column(
            "Ink (sq in)",
            lambda row: f"{row[1].total_ink_area_sq_in:.2f}",
            "ink_sq_in",
            total=lambda rows: f"{total_ink_sq_in(rows):.2f}",
        ),
        Column(
            "Ink (sq ft)",
            lambda row: f"{sq_ft(row[1].total_ink_area_sq_in):.2f}",
            "ink_sq_ft",
            total=lambda rows: f"{sq_ft(total_ink_sq_in(rows)):.2f}",
        ),
        _cost_column("Ink Cost ($)", "ink_cost", "ink_cost", breakdown),
        _cost_column(
            "Substrate Cost ($)", "substrate_cost", "substrate_cost", breakdown
        ),
        _cost_column("Printer Hours", "printer_hours", "printer_hours", breakdown),
        _cost_column("Printer Cost ($)", "printer_cost", "printer_cost", breakdown),
        _cost_column("Labor Hours", "labor_hours", "labor_hours", breakdown),
        _cost_column("Labor Cost ($)", "labor_cost", "labor_cost", breakdown),
        _cost_column("Total Cost ($)", "total_cost", "total_cost", breakdown),
        Column(
            "Unit Price ($)",
            _cost_value(breakdown, "unit_price", 2),
            "unit_price",
            _price_per_unit_total(breakdown),
        ),
    ]

    write_csv_table(
        output_path,
        columns,
        rows,
        include_costs=include_costs,
        customer_config=customer_report_config,
        default_customer_keys=("label_size_wxh", "unit_price"),
        customer_order=_FILE_CUSTOMER_ORDER,
        total_label="TOTAL",
        total_label_index=1,
    )


_FILE_CUSTOMER_ORDER: tuple[str, ...] = (
    "File #",
    "Filename",
    "Labels",
    "Label Size (WxH)",
    "Lin Ft",
    "Substrate (sq ft)",
    "Label Area (sq ft)",
    "Ink (sq in)",
    "Ink (sq ft)",
    "Ink Cost ($)",
    "Substrate Cost ($)",
    "Printer Hours",
    "Printer Cost ($)",
    "Labor Hours",
    "Labor Cost ($)",
    "Total Cost ($)",
    "Unit Price ($)",
)


def write_pdf_file_breakdown_vendor_csv(
    pdf_records: Sequence[PdfFileRecord], output_path: Path
) -> None:
    """Write the vendor file breakdown: counts and areas only, no names or prices."""
    if not pdf_records:
        return

    rows: list[_FileRow] = [
        (number, record)
        for number, record in enumerate(sort_pdf_records(list(pdf_records)), start=1)
    ]

    columns: list[Column[_FileRow]] = [
        Column("File #", lambda row: str(row[0])),
        Column("Label Size (WxH)", lambda row: row[1].label_size),
        Column(
            "Labels",
            lambda row: str(row[1].total_output_labels),
            total=lambda rows: str(sum(row[1].total_output_labels for row in rows)),
        ),
        Column(
            "Substrate (sq ft)",
            lambda row: f"{sq_ft(row[1].total_substrate_sq_in):.2f}",
            None,
            total=lambda rows: _sum_rows(
                rows, lambda row: sq_ft(row[1].total_substrate_sq_in)
            ),
        ),
        Column(
            "Label Area (sq ft)",
            lambda row: f"{sq_ft(row[1].total_label_material_sq_in):.2f}",
            None,
            total=lambda rows: _sum_rows(
                rows, lambda row: sq_ft(row[1].total_label_material_sq_in)
            ),
        ),
        Column(
            "Ink (sq in)",
            lambda row: f"{row[1].total_ink_area_sq_in:.2f}",
            None,
            total=lambda rows: _sum_rows(rows, lambda row: row[1].total_ink_area_sq_in),
        ),
        Column(
            "Ink (sq ft)",
            lambda row: f"{sq_ft(row[1].total_ink_area_sq_in):.2f}",
            None,
            total=lambda rows: _sum_rows(
                rows, lambda row: sq_ft(row[1].total_ink_area_sq_in)
            ),
        ),
    ]

    write_csv_table(
        output_path, columns, rows, total_label="TOTAL", total_label_index=1
    )


# --- Per-label breakdown -------------------------------------------------------


def write_per_label_breakdown_csv(
    labels: Sequence[ConsolidatedLabelBySize],
    output_path: Path,
    *,
    include_costs: bool = True,
    customer_report_config: dict[str, bool] | None = None,
) -> None:
    """Write one row per ``(label code, size)`` pair, internal or customer variant."""
    if not labels:
        return

    def breakdown(label: ConsolidatedLabelBySize) -> CostBreakdown | None:
        return label.cost_breakdown

    def unit_price(label: ConsolidatedLabelBySize) -> str:
        priced = label.cost_breakdown
        if priced is None or label.instances <= 0:
            return ""
        return f"{priced.unit_price / label.instances:.2f}"

    columns: list[Column[ConsolidatedLabelBySize]] = [
        Column("Label Code", lambda row: row.text),
        Column(
            "Copies",
            lambda row: str(row.instances),
            total=lambda rows: str(sum(row.instances for row in rows)),
        ),
        Column(
            "Label Size (WxH)",
            lambda row: format_label_size(row.label_size),
            "label_size_wxh",
        ),
        Column(
            "Label Size (sq in)",
            lambda row: f"{row.label_area_sq_in:.2f}",
            "label_size_sq_in",
            total=lambda rows: _sum_rows(rows, lambda row: row.label_area_sq_in),
        ),
        Column(
            "Text Height (in)",
            lambda row: f"{row.text_height_in:.2f}",
            "text_height_in",
        ),
        Column(
            "Char Count",
            lambda row: str(row.char_count),
            "char_count",
            total=lambda rows: str(sum(row.char_count for row in rows)),
        ),
        Column("Scale", lambda row: f"{row.horizontal_scale:.4f}", "scale"),
        Column(
            "Label Area (sq ft)",
            lambda row: f"{sq_ft(row.label_area_sq_in):.4f}",
            "label_area_sq_ft",
            total=lambda rows: _sum_rows(
                rows, lambda row: sq_ft(row.label_area_sq_in), digits=4
            ),
        ),
        Column(
            "Linear Feet",
            lambda row: f"{row.linear_feet:.2f}",
            "linear_feet",
            total=lambda rows: _sum_rows(rows, lambda row: row.linear_feet),
        ),
        Column(
            "Ink (sq in)",
            lambda row: f"{row.ink_area_sq_in:.2f}",
            "ink_sq_in",
            total=lambda rows: _sum_rows(rows, lambda row: row.ink_area_sq_in),
        ),
        Column(
            "Ink (sq ft)",
            lambda row: f"{sq_ft(row.ink_area_sq_in):.4f}",
            "ink_sq_ft",
            total=lambda rows: _sum_rows(
                rows, lambda row: sq_ft(row.ink_area_sq_in), digits=4
            ),
        ),
        _cost_column("Ink Cost ($)", "ink_cost", "ink_cost", breakdown),
        _cost_column(
            "Substrate Cost ($)", "substrate_cost", "substrate_cost", breakdown
        ),
        _cost_column(
            "Printer Hours",
            "printer_hours",
            "printer_hours",
            breakdown,
            digits=4,
            total_digits=2,
        ),
        _cost_column("Printer Cost ($)", "printer_cost", "printer_cost", breakdown),
        _cost_column(
            "Labor Hours",
            "labor_hours",
            "labor_hours",
            breakdown,
            digits=4,
            total_digits=2,
        ),
        _cost_column("Labor Cost ($)", "labor_cost", "labor_cost", breakdown),
        _cost_column("Total Cost ($)", "total_cost", "total_cost", breakdown),
        _cost_column("Price ($)", "unit_price", "price", breakdown),
        Column(
            "Unit Price ($)", unit_price, "unit_price", _price_per_unit_total(breakdown)
        ),
    ]

    write_csv_table(
        output_path,
        columns,
        list(labels),
        include_costs=include_costs,
        customer_config=customer_report_config,
        customer_order=_LABEL_CUSTOMER_ORDER,
        total_label="TOTAL",
    )


_LABEL_CUSTOMER_ORDER: tuple[str, ...] = (
    "Label Code",
    "Copies",
    "Label Size (WxH)",
    "Text Height (in)",
    "Char Count",
    "Scale",
    "Label Size (sq in)",
    "Label Area (sq ft)",
    "Linear Feet",
    "Ink (sq in)",
    "Ink (sq ft)",
    "Ink Cost ($)",
    "Substrate Cost ($)",
    "Printer Hours",
    "Printer Cost ($)",
    "Labor Hours",
    "Labor Cost ($)",
    "Total Cost ($)",
    "Price ($)",
    "Unit Price ($)",
)
