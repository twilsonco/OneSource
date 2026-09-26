"""Tests for :mod:`vinyllabels.consolidate.csv_reports`.

Every writer is checked against hand-computed cells, and both audiences are
covered: internal (all columns, declared order) and customer (subset, reordered,
optionally widened by a ``customer_report`` config).
"""

from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from vinyllabels.consolidate.csv_reports import (
    size_text_heights,
    write_job_breakdown_csv,
    write_pdf_file_breakdown_csv,
    write_pdf_file_breakdown_vendor_csv,
    write_per_label_breakdown_csv,
    write_size_breakdown_csv,
    write_vendor_label_size_breakdown_csv,
)
from vinyllabels.consolidate.merge import (
    build_pdf_file_records,
    consolidate_label_sizes,
    consolidate_labels_by_size,
    consolidate_size_areas,
)
from vinyllabels.consolidate.model import (
    ConsolidatedLabelBySize,
    JobReport,
    SizeAreas,
)

from vinyllabels.sizes import LabelSize

from helpers import cost_breakdown, job_report, label_metrics, pdf_metrics


def read_rows(path: Path) -> list[list[str]]:
    """Every row of a CSV file, header first."""
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


def table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """A CSV file as ``(header, rows-as-dicts)``, totals row included."""
    rows = read_rows(path)
    header = rows[0]
    return header, [dict(zip(header, row, strict=True)) for row in rows[1:]]


def _job_a(tmp_path: Path) -> JobReport:
    """A priced 8x3 job printing two files."""
    aa_cost = cost_breakdown(1.0, 2.0, 3.0, 4.0, 20.0)
    return job_report(
        input_file=str(tmp_path / "a.txt"),
        labels=[label_metrics("AA", 1.0, aa_cost), label_metrics("BB", 2.0)],
        cost=cost_breakdown(1.0, 2.0, 3.0, 4.0, 20.0),
        pdfs=[tmp_path / "a_2.pdf", tmp_path / "a_1.pdf"],
        per_pdf=[
            pdf_metrics(2, 4, 3.0, 48.0),
            pdf_metrics(2, 4, 3.0, 48.0, cost=cost_breakdown(1.0, 2.0, 3.0, 4.0, 20.0)),
        ],
    )


def _job_b(tmp_path: Path) -> JobReport:
    """An unpriced 4x2 job on a narrower roll, with a taller text height."""
    return job_report(
        input_file=str(tmp_path / "b.txt"),
        copies=1,
        size=(4.0, 2.0),
        labels=[label_metrics("AA", 1.0)],
        substrate=24.0,
        text_height_in=3.0,
        page_width_in=24.0,
    )


def _reports(tmp_path: Path) -> list[JobReport]:
    return [_job_a(tmp_path), _job_b(tmp_path)]


# A report that recorded no label sizes at all.
_empty_sizes: Counter[LabelSize] = Counter()


# --- size_text_heights ---------------------------------------------------------


def test_size_text_heights_takes_the_first_job_per_size(tmp_path: Path) -> None:
    reports = _reports(tmp_path)
    assert size_text_heights(reports) == {(8.0, 3.0): 2.0, (4.0, 2.0): 3.0}
    # A second job for the same size does not overwrite the first.
    again = job_report(input_file="c.txt", labels=[label_metrics("CC", 1.0)])
    assert size_text_heights([*reports, again])[(8.0, 3.0)] == 2.0


def test_size_text_heights_skips_jobs_without_a_size(tmp_path: Path) -> None:
    assert size_text_heights([job_report(size=None, labels=[])]) == {}
    assert size_text_heights([]) == {}


# --- size breakdown ------------------------------------------------------------


def test_write_size_breakdown_csv_internal(tmp_path: Path) -> None:
    reports = _reports(tmp_path)
    out = tmp_path / "sizes.csv"
    write_size_breakdown_csv(
        consolidate_label_sizes(reports),
        consolidate_size_areas(reports),
        reports,
        out,
    )

    header, rows = table(out)
    assert header == [
        "Label Size (WxH)",
        "Text Height (in)",
        "Files",
        "Labels",
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
    ]
    assert [row["Label Size (WxH)"] for row in rows] == ["4x2", "8x3", "TOTAL"]

    unpriced, priced, totals = rows
    # Sizes sort ascending, and each size keeps the text height of its first job.
    assert unpriced["Text Height (in)"] == "3.00"
    assert unpriced["Files"] == "1"
    assert unpriced["Labels"] == "1"
    assert unpriced["Substrate (sq ft)"] == "0.17"
    assert unpriced["Label Area (sq ft)"] == "0.06"
    assert unpriced["Ink (sq ft)"] == "0.01"
    # A size with no pricing renders blank money cells rather than zeros.
    assert unpriced["Total Cost ($)"] == ""
    assert unpriced["Unit Price ($)"] == ""

    assert priced["Text Height (in)"] == "2.00"
    assert priced["Files"] == "2"
    assert priced["Labels"] == "4"
    assert priced["Substrate (sq ft)"] == "0.67"
    assert priced["Ink (sq ft)"] == "0.04"
    assert priced["Substrate Cost ($)"] == "2.00"
    assert priced["Ink Cost ($)"] == "1.00"
    assert priced["Printer Hours"] == "0.50"
    assert priced["Total Cost ($)"] == "10.00"
    assert priced["Price ($)"] == "20.00"
    assert priced["Unit Price ($)"] == "5.00"

    assert totals["Files"] == "3"
    assert totals["Labels"] == "5"
    # The totals row sums the displayed sq-ft values, so it carries rounding.
    assert totals["Substrate (sq ft)"] == "0.83"
    assert totals["Label Area (sq ft)"] == "0.72"
    assert totals["Ink (sq ft)"] == "0.05"
    assert totals["Total Cost ($)"] == "10.00"
    assert totals["Price ($)"] == "20.00"
    # Unit prices are not additive, so the totals row leaves the cell blank.
    assert totals["Unit Price ($)"] == ""
    assert totals["Text Height (in)"] == ""


def test_write_size_breakdown_csv_customer_defaults(tmp_path: Path) -> None:
    reports = _reports(tmp_path)
    out = tmp_path / "sizes.csv"
    write_size_breakdown_csv(
        consolidate_label_sizes(reports),
        consolidate_size_areas(reports),
        reports,
        out,
        include_costs=False,
    )
    header, rows = table(out)
    assert header == [
        "Label Size (WxH)",
        "Files",
        "Labels",
        "Price ($)",
        "Unit Price ($)",
    ]
    assert [row["Label Size (WxH)"] for row in rows] == ["4x2", "8x3", "TOTAL"]
    assert rows[1]["Unit Price ($)"] == "5.00"


def test_write_size_breakdown_csv_customer_config_and_order(tmp_path: Path) -> None:
    reports = _reports(tmp_path)
    out = tmp_path / "sizes.csv"
    write_size_breakdown_csv(
        consolidate_label_sizes(reports),
        consolidate_size_areas(reports),
        reports,
        out,
        include_costs=False,
        customer_report_config={
            "text_height_in": True,
            "label_area_sq_ft": True,
        },
    )
    header, _ = table(out)
    # An explicit config replaces the defaults rather than adding to them, and
    # customer output follows _SIZE_CUSTOMER_ORDER rather than the declared order.
    assert header == [
        "Label Size (WxH)",
        "Files",
        "Labels",
        "Text Height (in)",
        "Label Area (sq ft)",
    ]


def test_write_size_breakdown_csv_blank_unit_price_without_labels(
    tmp_path: Path,
) -> None:
    one = _job_a(tmp_path)
    out = tmp_path / "sizes.csv"
    write_size_breakdown_csv(
        Counter({(8.0, 3.0): 0}),
        consolidate_size_areas([one]),
        [one],
        out,
    )
    _, (row, _) = table(out)
    assert row["Labels"] == "0"
    assert row["Price ($)"] == "20.00"
    assert row["Unit Price ($)"] == ""


def test_write_size_breakdown_csv_uses_default_text_height(
    tmp_path: Path,
) -> None:
    out = tmp_path / "sizes.csv"
    # A size present in the counter but printed by no job falls back to 2.00.
    write_size_breakdown_csv(
        Counter({(1.0, 1.0): 3}),
        {(1.0, 1.0): SizeAreas(substrate_sq_in=144.0, label_material_sq_in=72.0)},
        [],
        out,
    )
    _, (row, _) = table(out)
    assert row["Text Height (in)"] == "2.00"
    assert row["Files"] == "0"
    assert row["Label Size (WxH)"] == "1x1"
    assert row["Substrate (sq ft)"] == "1.00"
    assert row["Label Area (sq ft)"] == "0.50"


# --- vendor size breakdown -----------------------------------------------------


def test_write_vendor_label_size_breakdown_csv(tmp_path: Path) -> None:
    reports = _reports(tmp_path)
    out = tmp_path / "vendor.csv"
    write_vendor_label_size_breakdown_csv(
        consolidate_label_sizes(reports), consolidate_size_areas(reports), reports, out
    )
    header, rows = table(out)
    assert header == [
        "Label Size (WxH)",
        "Jobs",
        "Files",
        "Labels",
        "Lin Ft",
        "Substrate (sq ft)",
        "Label Area (sq ft)",
        "Ink (sq ft)",
    ]
    unpriced, priced, totals = rows
    assert (unpriced["Jobs"], unpriced["Files"], unpriced["Labels"]) == ("1", "1", "1")
    # Lin Ft comes from the size bucket: substrate / (average roll width * 12).
    assert unpriced["Lin Ft"] == "0.08"
    assert priced["Lin Ft"] == "0.15"
    assert totals["Jobs"] == "2"
    assert totals["Files"] == "3"
    assert totals["Labels"] == "5"
    assert totals["Lin Ft"] == "0.24"


# --- job breakdown -------------------------------------------------------------


def test_write_job_breakdown_csv_internal(tmp_path: Path) -> None:
    reports = _reports(tmp_path)
    out = tmp_path / "jobs.csv"
    write_job_breakdown_csv(reports, tmp_path / "json", out)

    header, rows = table(out)
    assert header == [
        "Input File",
        "Label Size (WxH)",
        "Text Height (in)",
        "Files",
        "Labels",
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
        "Price ($)",
        "Unit Price ($)",
    ]
    first, second, totals = rows
    # Input paths are shown relative to the report directory's parent.
    assert first["Input File"] == "a.txt"
    assert first["Label Size (WxH)"] == "8x3"
    assert first["Files"] == "2"
    assert first["Labels"] == "4"
    assert first["Substrate (sq ft)"] == "0.67"
    assert first["Ink (sq in)"] == "6.00"
    assert first["Ink (sq ft)"] == "0.04"
    assert first["Unit Price ($)"] == "5.00"
    assert second["Input File"] == "b.txt"
    assert second["Label Size (WxH)"] == "4x2"
    assert second["Total Cost ($)"] == ""

    assert totals["Files"] == "3"
    assert totals["Labels"] == "5"
    assert totals["Substrate (sq ft)"] == "0.83"
    # The sq-in total is summed then formatted; the sq-ft total converts the
    # summed sq-in, so the two need not agree to the last digit.
    assert totals["Ink (sq in)"] == "7.00"
    assert totals["Ink (sq ft)"] == "0.05"
    assert totals["Unit Price ($)"] == ""


def test_write_job_breakdown_csv_customer_defaults(tmp_path: Path) -> None:
    reports = _reports(tmp_path)
    out = tmp_path / "jobs.csv"
    write_job_breakdown_csv(reports, tmp_path / "json", out, include_costs=False)
    header, _ = table(out)
    assert header == [
        "Input File",
        "Files",
        "Labels",
        "Label Area (sq ft)",
        "Price ($)",
        "Unit Price ($)",
    ]


def test_write_job_breakdown_csv_customer_config_adds_linear_feet(
    tmp_path: Path,
) -> None:
    reports = _reports(tmp_path)
    out = tmp_path / "jobs.csv"
    write_job_breakdown_csv(
        reports,
        tmp_path / "json",
        out,
        include_costs=False,
        customer_report_config={"linear_feet": True, "text_height_in": True},
    )
    header, rows = table(out)
    assert header == [
        "Input File",
        "Files",
        "Labels",
        "Text Height (in)",
        "Linear Feet",
    ]
    # 96 sq in on a 52 in roll is 0.15 ft; 24 sq in on 24 in is 0.08 ft.
    assert rows[0]["Linear Feet"] == "0.15"
    assert rows[1]["Linear Feet"] == "0.08"


def test_write_job_breakdown_csv_keeps_paths_outside_the_directory(
    tmp_path: Path,
) -> None:
    elsewhere = tmp_path.parent / "elsewhere" / "z.txt"
    one = job_report(
        input_file=str(elsewhere),
        labels=[label_metrics("AA", 1.0)],
    )
    out = tmp_path / "jobs.csv"
    write_job_breakdown_csv([one], tmp_path / "json", out)
    _, (row, _) = table(out)
    assert row["Input File"] == str(elsewhere)


def test_write_job_breakdown_csv_sizes_and_unit_price_edge_cases(
    tmp_path: Path,
) -> None:
    no_sizes: JobReport = job_report(
        input_file="a.txt",
        sizes=_empty_sizes,
        labels=[label_metrics("AA", 1.0)],
        cost=cost_breakdown(1.0, 2.0, 3.0, 4.0, 20.0),
    )
    no_labels = job_report(
        input_file="b.txt",
        labels=[],
        cost=cost_breakdown(1.0, 2.0, 3.0, 4.0, 20.0),
    )
    out = tmp_path / "jobs.csv"
    write_job_breakdown_csv([no_sizes, no_labels], tmp_path / "json", out)
    _, rows = table(out)
    # A report that recorded no sizes renders an empty size cell.
    assert rows[0]["Label Size (WxH)"] == ""
    assert rows[0]["Labels"] == "2"
    assert rows[0]["Unit Price ($)"] == "10.00"
    # A job with no printed labels has a price but no meaningful unit price.
    assert rows[1]["Label Size (WxH)"] == "8x3"
    assert rows[1]["Labels"] == "0"
    assert rows[1]["Unit Price ($)"] == ""
    assert rows[2]["Labels"] == "2"
    assert rows[2]["Files"] == "2"


# --- file breakdown ------------------------------------------------------------


def test_write_pdf_file_breakdown_csv(tmp_path: Path) -> None:
    records = build_pdf_file_records(_reports(tmp_path))
    out = tmp_path / "files.csv"
    write_pdf_file_breakdown_csv(records, out)

    header, rows = table(out)
    assert header == [
        "File #",
        "Filename",
        "Label Size (WxH)",
        "Labels",
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
    ]
    first, second, totals = rows
    # Rows are numbered in vendor order (size, then filename), not job order.
    assert [row["Filename"] for row in rows] == ["a_1.pdf", "a_2.pdf", "TOTAL"]
    assert first["File #"] == "1"
    assert second["File #"] == "2"
    assert first["Label Size (WxH)"] == "8x3"
    assert first["Labels"] == "2"
    assert first["Lin Ft"] == "0.08"
    assert first["Substrate (sq ft)"] == "0.33"
    assert first["Label Area (sq ft)"] == "0.33"
    assert first["Ink (sq in)"] == "3.00"
    assert first["Ink (sq ft)"] == "0.02"
    assert first["Total Cost ($)"] == "10.00"
    assert first["Unit Price ($)"] == "20.00"
    # The second file carries no pricing of its own.
    assert second["Total Cost ($)"] == ""
    assert second["Unit Price ($)"] == ""
    # The totals label sits on the filename column, leaving File # blank.
    assert totals["File #"] == ""
    assert totals["Filename"] == "TOTAL"
    assert totals["Labels"] == "4"
    assert totals["Ink (sq in)"] == "6.00"
    assert totals["Ink (sq ft)"] == "0.04"
    assert totals["Total Cost ($)"] == "10.00"
    assert totals["Unit Price ($)"] == ""


def test_write_pdf_file_breakdown_csv_customer_defaults(tmp_path: Path) -> None:
    records = build_pdf_file_records(_reports(tmp_path))
    out = tmp_path / "files.csv"
    write_pdf_file_breakdown_csv(records, out, include_costs=False)
    header, rows = table(out)
    assert header == [
        "File #",
        "Filename",
        "Labels",
        "Label Size (WxH)",
        "Unit Price ($)",
    ]
    assert rows[0]["Label Size (WxH)"] == "8x3"
    assert rows[0]["Unit Price ($)"] == "20.00"


def test_write_pdf_file_breakdown_csv_skips_empty_input(tmp_path: Path) -> None:
    out = tmp_path / "files.csv"
    write_pdf_file_breakdown_csv([], out)
    assert not out.exists()


def test_write_pdf_file_breakdown_vendor_csv(tmp_path: Path) -> None:
    records = build_pdf_file_records(_reports(tmp_path))
    out = tmp_path / "vendor.csv"
    write_pdf_file_breakdown_vendor_csv(records, out)
    header, rows = table(out)
    assert header == [
        "File #",
        "Label Size (WxH)",
        "Labels",
        "Substrate (sq ft)",
        "Label Area (sq ft)",
        "Ink (sq in)",
        "Ink (sq ft)",
    ]
    assert [row["Label Size (WxH)"] for row in rows] == ["8x3", "8x3", "TOTAL"]
    assert rows[2]["File #"] == ""
    assert rows[2]["Labels"] == "4"
    assert rows[2]["Ink (sq in)"] == "6.00"


def test_write_pdf_file_breakdown_vendor_csv_skips_empty_input(
    tmp_path: Path,
) -> None:
    out = tmp_path / "vendor.csv"
    write_pdf_file_breakdown_vendor_csv([], out)
    assert not out.exists()


# --- per-label breakdown -------------------------------------------------------


def _labels() -> list[ConsolidatedLabelBySize]:
    priced = ConsolidatedLabelBySize(
        text="AA",
        label_size=(8.0, 3.0),
        instances=4,
        char_count=8,
        ink_area_sq_in=6.0,
        substrate_sq_in=96.0,
        horizontal_scale=0.5,
        linear_feet=1.0,
        text_height_in=2.0,
        cost_breakdown=cost_breakdown(1.0, 2.0, 3.0, 4.0, 20.0),
    )
    unpriced = ConsolidatedLabelBySize(
        text="BB",
        label_size=(4.0, 2.0),
        instances=0,
        char_count=0,
        ink_area_sq_in=0.0,
    )
    return [priced, unpriced]


def test_write_per_label_breakdown_csv_internal(tmp_path: Path) -> None:
    out = tmp_path / "labels.csv"
    write_per_label_breakdown_csv(_labels(), out)
    header, rows = table(out)
    assert header == [
        "Label Code",
        "Copies",
        "Label Size (WxH)",
        "Label Size (sq in)",
        "Text Height (in)",
        "Char Count",
        "Scale",
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
    ]
    priced, unpriced, totals = rows
    assert priced["Label Size (sq in)"] == "96.00"
    assert priced["Scale"] == "0.5000"
    assert priced["Label Area (sq ft)"] == "0.6667"
    assert priced["Ink (sq ft)"] == "0.0417"
    # Hours print to four decimals per row...
    assert priced["Printer Hours"] == "0.5000"
    assert priced["Labor Hours"] == "0.2500"
    assert priced["Unit Price ($)"] == "5.00"
    # ...and a zero-instance label has no unit price.
    assert unpriced["Copies"] == "0"
    assert unpriced["Unit Price ($)"] == ""
    assert unpriced["Ink Cost ($)"] == ""
    assert totals["Copies"] == "4"
    assert totals["Char Count"] == "8"
    assert totals["Label Size (sq in)"] == "96.00"
    assert totals["Label Area (sq ft)"] == "0.6667"
    assert totals["Ink (sq ft)"] == "0.0417"
    # ...but only two in the totals row.
    assert totals["Printer Hours"] == "0.50"
    assert totals["Labor Hours"] == "0.25"
    assert totals["Price ($)"] == "20.00"
    assert totals["Unit Price ($)"] == ""


def test_write_per_label_breakdown_csv_customer_defaults(tmp_path: Path) -> None:
    out = tmp_path / "labels.csv"
    write_per_label_breakdown_csv(_labels(), out, include_costs=False)
    header, _ = table(out)
    # Nothing but the always-present columns is customer-visible by default.
    assert header == ["Label Code", "Copies"]


def test_write_per_label_breakdown_csv_customer_config(tmp_path: Path) -> None:
    out = tmp_path / "labels.csv"
    write_per_label_breakdown_csv(
        _labels(),
        out,
        include_costs=False,
        customer_report_config={"price": True, "unit_price": True, "scale": True},
    )
    header, _ = table(out)
    assert header == ["Label Code", "Copies", "Scale", "Price ($)", "Unit Price ($)"]


def test_write_per_label_breakdown_csv_skips_empty_input(tmp_path: Path) -> None:
    out = tmp_path / "labels.csv"
    write_per_label_breakdown_csv([], out)
    assert not out.exists()


def test_write_per_label_breakdown_csv_from_consolidated_labels(
    tmp_path: Path,
) -> None:
    # The real pipeline path: labels come out of consolidate_labels_by_size.
    reports = _reports(tmp_path)
    labels: Sequence[ConsolidatedLabelBySize] = consolidate_labels_by_size(reports)
    out = tmp_path / "labels.csv"
    write_per_label_breakdown_csv(labels, out)
    _, rows = table(out)
    assert [row["Label Code"] for row in rows] == ["AA", "AA", "BB", "TOTAL"]
    assert [row["Label Size (WxH)"] for row in rows] == ["4x2", "8x3", "8x3", ""]
    assert rows[0]["Total Cost ($)"] == ""
    # Two printed copies of AA at 8x3, each priced at 10.00 of cost.
    assert rows[1]["Total Cost ($)"] == "20.00"
    assert rows[1]["Price ($)"] == "40.00"
    assert rows[1]["Unit Price ($)"] == "20.00"
