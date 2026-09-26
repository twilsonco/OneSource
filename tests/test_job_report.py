"""Tests for :mod:`vinyllabels.job_report`, the per-job report writers."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from vinyllabels.job_report import (
    label_to_pdf_mapping,
    write_metrics_csv,
    write_metrics_report,
    write_metrics_report_json,
    write_metrics_report_txt,
)
from vinyllabels.layout import JobConfig
from vinyllabels.metrics import calculate_metrics
from vinyllabels.models import CostBreakdown, GlobalMetrics, LabelMetrics, PricingConfig
from vinyllabels.reportio.loaders import load_json_object
from vinyllabels.units import sq_ft

BUILTIN_FONT = "Helvetica-Bold"


def labels() -> list[LabelMetrics]:
    return [
        LabelMetrics(
            text="ALPHA",
            char_count=5,
            horizontal_scale=1.0,
            ink_area_sq_in=2.0,
            cost_breakdown=CostBreakdown(
                ink_cost=1.0,
                substrate_cost=2.0,
                printer_hours=0.5,
                printer_cost=5.0,
                labor_hours=0.25,
                labor_cost=5.0,
                total_cost=13.0,
                unit_price=14.3,
            ),
        ),
        LabelMetrics(
            text="B",
            char_count=1,
            horizontal_scale=0.5,
            ink_area_sq_in=0.25,
            cost_breakdown=CostBreakdown(
                ink_cost=0.5,
                substrate_cost=1.0,
                printer_hours=0.25,
                printer_cost=2.5,
                labor_hours=0.125,
                labor_cost=2.5,
                total_cost=6.5,
                unit_price=7.15,
            ),
        ),
    ]


def unpriced_labels() -> list[LabelMetrics]:
    return [
        LabelMetrics(
            text="ALPHA", char_count=5, horizontal_scale=1.0, ink_area_sq_in=2.0
        )
    ]


def global_metrics(with_costs: bool = True) -> GlobalMetrics:
    return GlobalMetrics(
        total_output_labels=10,
        total_characters=60,
        total_ink_area_sq_in=22.5,
        total_label_material_sq_in=240.0,
        total_substrate_sq_in=1352.0,
        material_yield_pct=240.0 / 1352.0 * 100.0,
        average_ink_coverage_pct=22.5 / 240.0 * 100.0,
        page_height_in=26.0,
        linear_feet=1352.0 / 52.0 / 12.0,
        cost_breakdown=(
            CostBreakdown(
                ink_cost=15.0,
                substrate_cost=30.0,
                printer_hours=7.5,
                printer_cost=75.0,
                labor_hours=3.75,
                labor_cost=75.0,
                total_cost=195.0,
                unit_price=214.5,
            )
            if with_costs
            else None
        ),
    )


# --- label_to_pdf_mapping ------------------------------------------------------


def test_mapping_with_a_single_pdf() -> None:
    mapping = label_to_pdf_mapping(labels(), ["only.pdf"], [(0, 19)], 2)
    assert mapping == {"ALPHA": "only.pdf", "B": "only.pdf"}


def test_mapping_with_no_page_breaks() -> None:
    mapping = label_to_pdf_mapping(labels(), ["only.pdf"], None, 2)
    assert mapping == {"ALPHA": "only.pdf", "B": "only.pdf"}


def test_mapping_uses_the_first_instance_of_each_code() -> None:
    mapping = label_to_pdf_mapping(labels(), ["p1.pdf", "p2.pdf"], [(0, 1), (2, 19)], 2)
    # ALPHA's first instance is index 0 (page 1); B's is index 2 (page 2).
    assert mapping == {"ALPHA": "p1.pdf", "B": "p2.pdf"}


def test_mapping_clamps_a_missing_page() -> None:
    mapping = label_to_pdf_mapping(labels(), ["p1.pdf"], [(0, 1), (2, 19)], 2)
    assert mapping == {"ALPHA": "p1.pdf", "B": "p1.pdf"}


def test_mapping_keeps_the_first_page_for_duplicate_codes() -> None:
    duplicated = [labels()[0], labels()[0]]
    mapping = label_to_pdf_mapping(
        duplicated, ["p1.pdf", "p2.pdf"], [(0, 1), (2, 3)], 1
    )
    assert mapping == {"ALPHA": "p1.pdf"}


def test_mapping_omits_a_label_no_range_covers() -> None:
    # The ranges stop before B's first instance (index 2), so only ALPHA is
    # attributed; the report leaves the other code's File cell blank.
    mapping = label_to_pdf_mapping(labels(), ["p1.pdf", "p2.pdf"], [(0, 1)], 2)
    assert mapping == {"ALPHA": "p1.pdf"}


# --- JSON ----------------------------------------------------------------------


def test_write_metrics_report_json_shape(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    out = tmp_path / "r.json"
    write_metrics_report_json(
        labels(),
        global_metrics(),
        out,
        Path("in.txt"),
        Path("a.pdf"),
        make_job_config(),
        "2026-01-01 00:00:00",
    )
    report = load_json_object(out)
    job = report["job"]
    assert isinstance(job, dict)
    assert job["generated"] == "2026-01-01 00:00:00"
    assert job["input_file"] == "in.txt"
    assert job["output_pdf"] == "a.pdf"
    assert job["copies_per_label"] == 2
    assert job["label_width_in"] == 8.0

    assert report["label_sizes"] == [{"width_in": 8.0, "height_in": 3.0, "labels": 10}]

    section = report["global"]
    assert isinstance(section, dict)
    assert section["total_output_labels"] == 10
    # Every *_sq_in area gains a *_sq_ft sibling.
    assert section["total_ink_area_sq_ft"] == pytest.approx(sq_ft(22.5))
    assert "per_pdf" not in report


def test_write_metrics_report_json_lists_multiple_pdfs(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    out = tmp_path / "r.json"
    write_metrics_report_json(
        labels(),
        global_metrics(),
        out,
        Path("in.txt"),
        [Path("a.pdf"), Path("b.pdf")],
        make_job_config(),
        "t",
    )
    report = load_json_object(out)
    job = report["job"]
    assert isinstance(job, dict)
    assert job["output_pdf"] == ["a.pdf", "b.pdf"]


def test_write_metrics_report_json_includes_per_pdf(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    out = tmp_path / "r.json"
    write_metrics_report_json(
        labels(),
        global_metrics(),
        out,
        Path("in.txt"),
        [Path("a.pdf"), Path("b.pdf")],
        make_job_config(),
        "t",
        page_break_ranges=[(0, 1), (2, 19)],
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    per_pdf = report["per_pdf"]
    assert isinstance(per_pdf, list)
    assert len(per_pdf) == 2
    assert per_pdf[0]["total_output_labels"] == 2
    assert per_pdf[0]["total_ink_area_sq_ft"] == pytest.approx(sq_ft(4.0))


def test_write_metrics_report_json_records_no_pricing(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    out = tmp_path / "r.json"
    write_metrics_report_json(
        unpriced_labels(),
        global_metrics(with_costs=False),
        out,
        Path("in.txt"),
        Path("a.pdf"),
        make_job_config(),
        "t",
    )
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["per_label"][0]["cost_breakdown"] is None
    assert report["global"]["cost_breakdown"] is None


# --- text ----------------------------------------------------------------------


def test_write_metrics_report_txt_full(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    out = tmp_path / "r.txt"
    write_metrics_report_txt(
        labels(),
        global_metrics(),
        out,
        Path("in.txt"),
        [Path("a.pdf"), Path("b.pdf")],
        make_job_config(),
        "2026-01-01 00:00:00",
    )
    text = out.read_text(encoding="utf-8")
    assert "LABEL PRINTING REPORT" in text
    assert "Generated:        2026-01-01 00:00:00" in text
    assert "Input File:       in.txt" in text
    assert "Output PDF:       a.pdf" in text
    # The second PDF is listed without repeating the label.
    assert " " * 18 + "b.pdf" in text
    assert "COST BREAKDOWN" in text
    assert "Total Cost:                $    195.00" in text
    # unit_price 214.5 over 10 labels.
    assert "Unit Price (per label):    $     21.45" in text
    assert "8x3              |     10" in text
    assert "PER-LABEL BREAKDOWN" in text
    assert "ALPHA" in text
    assert "$   13.00" in text


def test_write_metrics_report_txt_without_pricing(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    out = tmp_path / "r.txt"
    write_metrics_report_txt(
        unpriced_labels(),
        global_metrics(with_costs=False),
        out,
        Path("in.txt"),
        [Path("a.pdf")],
        make_job_config(),
        "t",
    )
    text = out.read_text(encoding="utf-8")
    assert "COST BREAKDOWN" not in text
    assert "Ink Area (sq ft)" in text


def test_write_metrics_report_txt_with_no_labels(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    out = tmp_path / "r.txt"
    write_metrics_report_txt(
        [],
        global_metrics(with_costs=False),
        out,
        Path("in.txt"),
        [],
        make_job_config(),
        "t",
    )
    assert "PER-LABEL BREAKDOWN" in out.read_text(encoding="utf-8")


def test_write_metrics_report_txt_unit_price_without_labels(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    out = tmp_path / "r.txt"
    priced = global_metrics()
    empty = GlobalMetrics(
        total_output_labels=0,
        total_characters=priced.total_characters,
        total_ink_area_sq_in=priced.total_ink_area_sq_in,
        total_label_material_sq_in=priced.total_label_material_sq_in,
        total_substrate_sq_in=priced.total_substrate_sq_in,
        material_yield_pct=priced.material_yield_pct,
        average_ink_coverage_pct=priced.average_ink_coverage_pct,
        page_height_in=priced.page_height_in,
        linear_feet=priced.linear_feet,
        cost_breakdown=priced.cost_breakdown,
    )
    write_metrics_report_txt(
        labels(), empty, out, Path("in.txt"), [], make_job_config(), "t"
    )
    assert "Unit Price (per label):    $      0.00" in out.read_text(encoding="utf-8")


# --- CSV -----------------------------------------------------------------------


def read_csv(path: Path) -> list[list[str]]:
    return list(csv.reader(path.read_text(encoding="utf-8").splitlines()))


def test_write_metrics_csv_internal(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    out = tmp_path / "r.csv"
    write_metrics_csv(labels(), out, ["a.pdf"], None, make_job_config())
    table = read_csv(out)
    assert table[0][:3] == ["File", "Label Code", "Char Count"]
    assert "Price ($)" not in table[0]
    assert table[1][:6] == [
        "a.pdf",
        "ALPHA",
        "5",
        "1.000",
        "2.0000",
        f"{sq_ft(2.0):.4f}",
    ]
    assert table[1][-1] == "14.30"


def test_write_metrics_csv_customer_defaults_to_unit_price(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    out = tmp_path / "c.csv"
    write_metrics_csv(
        labels(), out, ["a.pdf"], None, make_job_config(), include_costs=False
    )
    table = read_csv(out)
    assert table[0] == ["File", "Label Code", "Unit Price ($)"]
    assert table[1] == ["a.pdf", "ALPHA", "14.30"]


def test_write_metrics_csv_customer_follows_the_config(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    out = tmp_path / "c.csv"
    write_metrics_csv(
        labels(),
        out,
        ["a.pdf"],
        None,
        make_job_config(),
        include_costs=False,
        customer_report_config={"price": True, "char_count": True},
    )
    table = read_csv(out)
    assert table[0] == ["File", "Label Code", "Char Count", "Price ($)"]
    assert table[1] == ["a.pdf", "ALPHA", "5", "3.00"]


def test_write_metrics_csv_without_pricing_leaves_cost_cells_blank(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    out = tmp_path / "r.csv"
    write_metrics_csv(unpriced_labels(), out, ["a.pdf"], None, make_job_config())
    table = read_csv(out)
    assert table[1][-1] == ""


def test_write_metrics_csv_customer_price_is_blank_without_pricing(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    out = tmp_path / "c.csv"
    write_metrics_csv(
        unpriced_labels(),
        out,
        ["a.pdf"],
        None,
        make_job_config(),
        include_costs=False,
        customer_report_config={"price": True},
    )
    table = read_csv(out)
    assert table[0] == ["File", "Label Code", "Price ($)"]
    assert table[1] == ["a.pdf", "ALPHA", ""]


# --- the umbrella writer -------------------------------------------------------


def test_write_metrics_report_writes_every_artifact(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    txt = tmp_path / "r.txt"
    js = tmp_path / "r.json"
    csv_path = tmp_path / "r.csv"
    csv_customer = tmp_path / "r_customer.csv"

    returned = write_metrics_report(
        labels(),
        global_metrics(),
        txt,
        js,
        csv_path,
        csv_customer,
        Path("in.txt"),
        [Path("a.pdf")],
        make_job_config(),
        page_break_ranges=[(0, 19)],
        customer_report_config={"price": True},
    )

    assert returned == js
    assert all(path.is_file() for path in (txt, js, csv_path, csv_customer))
    report = json.loads(js.read_text(encoding="utf-8"))
    assert report["job"]["generated"]
    # Only "price" is enabled, so the internal-only Unit Price column is absent.
    assert read_csv(csv_customer)[0] == ["File", "Label Code", "Price ($)"]


def test_write_metrics_report_round_trips_through_the_loader(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    """What ``generate`` writes is exactly what ``consolidate`` reads back."""
    js = tmp_path / "job_report.json"
    config = make_job_config(copies_per_label=3)
    per_label, metrics = calculate_metrics(
        ["ALPHA", "B"], BUILTIN_FONT, None, config, None
    )
    write_metrics_report(
        per_label,
        metrics,
        tmp_path / "r.txt",
        js,
        tmp_path / "r.csv",
        tmp_path / "r_customer.csv",
        Path("in.txt"),
        [Path("a.pdf")],
        config,
        page_break_ranges=[(0, 5)],
    )

    from vinyllabels.consolidate.load import load_report

    loaded = load_report(js)
    assert loaded.copies_per_label == 3
    assert loaded.page_width_in == 52.0
    assert loaded.text_height_in == 2.0
    assert [m.text for m in loaded.per_label] == ["ALPHA", "B"]
    assert loaded.global_metrics.total_output_labels == 6
    assert loaded.global_metrics.total_ink_area_sq_in == pytest.approx(
        metrics.total_ink_area_sq_in
    )
    assert loaded.label_size == (8.0, 3.0)
    assert loaded.num_pdf_files == 1
    assert loaded.per_pdf_metrics is not None
    assert len(loaded.per_pdf_metrics) == 1


def test_write_metrics_report_round_trips_with_pricing(
    tmp_path: Path,
    make_job_config: Callable[..., JobConfig],
    make_pricing_config: Callable[..., PricingConfig],
) -> None:
    js = tmp_path / "job_report.json"
    config = make_job_config()
    per_label, metrics = calculate_metrics(
        ["ALPHA"], BUILTIN_FONT, None, config, make_pricing_config()
    )
    write_metrics_report(
        per_label,
        metrics,
        tmp_path / "r.txt",
        js,
        tmp_path / "r.csv",
        tmp_path / "r_customer.csv",
        Path("in.txt"),
        [Path("a.pdf")],
        config,
    )
    from vinyllabels.consolidate.load import load_report

    loaded = load_report(js)
    assert loaded.global_metrics.cost_breakdown is not None
    assert loaded.per_label[0].cost_breakdown is not None


def test_write_metrics_report_txt_matches_on_repeat_runs(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    """The writers are deterministic given the same inputs and timestamp."""
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    for out in (first, second):
        write_metrics_report_txt(
            labels(),
            global_metrics(),
            out,
            Path("in.txt"),
            [Path("a.pdf")],
            make_job_config(),
            "2026-01-01 00:00:00",
        )
    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")
