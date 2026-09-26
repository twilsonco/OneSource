"""Tests for :mod:`vinyllabels.consolidate.merge`, the consolidation arithmetic."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest

from vinyllabels.consolidate.merge import (
    build_pdf_file_records,
    consolidate,
    consolidate_label_sizes,
    consolidate_labels,
    consolidate_labels_by_size,
    consolidate_size_areas,
    display_input_file,
    job_name,
    roll_widths_in,
    sort_pdf_records,
)
from vinyllabels.consolidate.model import PdfFileRecord
from vinyllabels.models import CostBreakdown, PricingConfig

from helpers import cost_breakdown as breakdown
from helpers import job_report as report
from helpers import label_metrics as label
from helpers import pdf_metrics

Size = tuple[float, float]


# --- consolidate ---------------------------------------------------------------


def test_consolidate_sums_areas_and_recomputes_percentages() -> None:
    first = report(labels=[label("A", 2.0)], substrate=96.0)
    second = report(labels=[label("BB", 1.0)], substrate=48.0, copies=1)
    metrics = consolidate([first, second])

    assert metrics.total_jobs == 2
    assert metrics.total_output_labels == 3
    assert metrics.total_characters == 4
    assert metrics.total_ink_area_sq_in == pytest.approx(5.0)
    # 1 label * 2 copies * 24 sq in, plus 1 label * 1 copy * 24 sq in.
    assert metrics.total_label_material_sq_in == pytest.approx(72.0)
    assert metrics.total_substrate_sq_in == pytest.approx(144.0)
    assert metrics.material_yield_pct == pytest.approx(72.0 / 144.0 * 100.0)
    assert metrics.average_ink_coverage_pct == pytest.approx(5.0 / 72.0 * 100.0)
    assert metrics.linear_feet == pytest.approx(96.0 / 52 / 12 + 48.0 / 52 / 12)
    assert metrics.cost_breakdown is None


def test_consolidate_without_reports() -> None:
    metrics = consolidate([])
    assert metrics.total_jobs == 0
    assert metrics.material_yield_pct == 0.0
    assert metrics.average_ink_coverage_pct == 0.0


def test_consolidate_rebuilds_the_total_from_its_components(
    make_pricing_config: Callable[..., PricingConfig],
) -> None:
    first = report(
        labels=[label("A", 2.0, breakdown(1, 2, 5, 5, 14.3))],
        cost=breakdown(1, 2, 5, 5, 14.3),
    )
    second = report(
        labels=[label("A", 2.0, breakdown(2, 4, 10, 10, 28.6))],
        cost=breakdown(2, 4, 10, 10, 28.6),
    )
    metrics = consolidate([first, second], make_pricing_config())
    cost = metrics.cost_breakdown
    assert cost is not None
    assert cost.ink_cost == pytest.approx(3.0)
    assert cost.substrate_cost == pytest.approx(6.0)
    assert cost.printer_cost == pytest.approx(15.0)
    assert cost.labor_cost == pytest.approx(15.0)
    assert cost.total_cost == pytest.approx(39.0)
    # Explicit prices dominate, so markup is not re-applied.
    assert cost.unit_price == pytest.approx(42.9)


def test_consolidate_applies_markup_when_no_prices_carried(
    make_pricing_config: Callable[..., PricingConfig],
) -> None:
    first = report(labels=[label("A", 2.0)], cost=breakdown(1, 2, 5, 5, 0.0))
    metrics = consolidate([first], make_pricing_config(markup_percent=50.0))
    assert metrics.cost_breakdown is not None
    assert metrics.cost_breakdown.unit_price == pytest.approx(13.0 * 1.5)


def test_consolidate_keeps_no_cost_when_reports_have_none(
    make_pricing_config: Callable[..., PricingConfig],
) -> None:
    metrics = consolidate([report(labels=[])], make_pricing_config())
    assert metrics.cost_breakdown is None


# --- consolidate_labels --------------------------------------------------------


def test_consolidate_labels_merges_by_code() -> None:
    cb = breakdown(1, 2, 5, 5, 14.3)
    first = report(
        input_file="a.txt",
        copies=2,
        labels=[label("A", 2.0, cb), label("B", 1.0, cb)],
    )
    second = report(
        input_file="b.txt", copies=3, substrate=72.0, labels=[label("A", 4.0, cb)]
    )
    merged = consolidate_labels([first, second])

    assert [m.text for m in merged] == ["A", "B"]
    a, b = merged
    assert (a.jobs, a.instances) == (2, 5)
    assert a.char_count == 5
    assert a.ink_area_sq_in == pytest.approx(2.0 * 2 + 4.0 * 3)
    assert (a.size_w_in, a.size_h_in) == (8.0, 3.0)
    # Both jobs have substrate == label material, so the yield ratio is 1.0 and
    # each code's substrate is simply its printed label area.
    assert a.substrate_sq_in == pytest.approx(24 * 2 + 24 * 3)
    assert b.instances == 2

    assert a.cost_breakdown is not None
    assert a.cost_breakdown.ink_cost == pytest.approx(1 * 2 + 1 * 3)
    assert a.cost_breakdown.total_cost == pytest.approx(13 * 5)


def test_consolidate_labels_without_pricing() -> None:
    merged = consolidate_labels([report(labels=[label("A", 2.0)])])
    assert merged[0].cost_breakdown is None


def test_consolidate_labels_with_partially_priced_jobs() -> None:
    cb = breakdown(1, 2, 5, 5, 14.3)
    merged = consolidate_labels(
        [
            report(input_file="a.txt", labels=[label("A", 1.0, cb)]),
            report(input_file="b.txt", labels=[label("A", 1.0, None)]),
        ]
    )
    assert merged[0].cost_breakdown is not None
    assert merged[0].cost_breakdown.ink_cost == pytest.approx(2.0)


def test_consolidate_labels_handles_a_report_without_a_size() -> None:
    merged = consolidate_labels([report(size=None, labels=[label("A", 1.0)])])
    assert merged[0].size_w_in == 0.0
    assert merged[0].substrate_sq_in == pytest.approx(0.0)


# --- consolidate_labels_by_size ------------------------------------------------


def test_consolidate_labels_by_size_keeps_sizes_apart() -> None:
    small = report(
        input_file="a.txt", size=(4.0, 2.0), copies=2, labels=[label("A", 1.0)]
    )
    large = report(
        input_file="b.txt", size=(8.0, 3.0), copies=2, labels=[label("A", 3.0)]
    )
    merged = consolidate_labels_by_size([small, large])

    # The historical ordering is the flattened "text::WxH" string, so 4x2 sorts
    # before 8x3 lexicographically.
    assert [(m.text, m.label_size) for m in merged] == [
        ("A", (4.0, 2.0)),
        ("A", (8.0, 3.0)),
    ]
    assert [m.instances for m in merged] == [2, 2]
    assert [m.ink_area_sq_in for m in merged] == [
        pytest.approx(2.0),
        pytest.approx(6.0),
    ]
    assert [m.char_count for m in merged] == [2, 2]
    assert merged[0].linear_feet == pytest.approx(2.0 * 2 / 12.0)
    assert merged[0].label_area_sq_in == pytest.approx(8.0 * 2)


def test_consolidate_labels_by_size_averages_scale_and_text_height() -> None:
    first = report(
        input_file="a.txt",
        copies=2,
        text_height_in=1.0,
        labels=[label("A", 1.0, scale=0.5)],
    )
    second = report(
        input_file="b.txt",
        copies=6,
        text_height_in=3.0,
        labels=[label("A", 1.0, scale=1.0)],
    )
    (only,) = consolidate_labels_by_size([first, second])
    # Weighted by copies: (2*0.5 + 6*1.0) / 8.
    assert only.horizontal_scale == pytest.approx((2 * 0.5 + 6 * 1.0) / 8)
    assert only.text_height_in == pytest.approx((2 * 1.0 + 6 * 3.0) / 8)
    assert only.instances == 8


def test_consolidate_labels_by_size_skips_reports_without_a_size() -> None:
    assert (
        consolidate_labels_by_size([report(size=None, labels=[label("A", 1.0)])]) == []
    )


def test_consolidate_labels_by_size_needs_a_positive_cost(
    make_pricing_config: Callable[..., PricingConfig],
) -> None:
    zero = CostBreakdown.zero()
    merged = consolidate_labels_by_size(
        [report(labels=[label("A", 1.0, zero)])], make_pricing_config()
    )
    assert merged[0].cost_breakdown is None


def test_consolidate_labels_by_size_pricing(
    make_pricing_config: Callable[..., PricingConfig],
) -> None:
    cb = breakdown(1, 2, 5, 5, 0.0)
    merged = consolidate_labels_by_size(
        [report(copies=2, labels=[label("A", 1.0, cb)])], make_pricing_config()
    )
    cost = merged[0].cost_breakdown
    assert cost is not None
    assert cost.total_cost == pytest.approx(26.0)
    assert cost.unit_price == pytest.approx(28.6)


def test_consolidate_labels_by_size_averages_fall_back_without_copies(
    make_pricing_config: Callable[..., PricingConfig],
) -> None:
    # A job that recorded no copies contributes no weight, so the averaged
    # fields fall back to their defaults rather than dividing by zero.
    merged = consolidate_labels_by_size(
        [report(copies=0, text_height_in=3.0, labels=[label("A", 1.0, None, 0.4)])],
        make_pricing_config(),
    )
    assert merged[0].instances == 0
    assert merged[0].horizontal_scale == 1.0
    assert merged[0].text_height_in == 2.0


# --- consolidate_label_sizes / size areas ---------------------------------------


def test_consolidate_label_sizes_merges_counters() -> None:
    first = report(input_file="a.txt", size=(8.0, 3.0), labels=[label("A", 1.0)])
    second = report(input_file="b.txt", size=(4.0, 2.0), labels=[label("A", 1.0)])
    assert consolidate_label_sizes([first, second]) == {(8.0, 3.0): 2, (4.0, 2.0): 2}
    assert consolidate_label_sizes([]) == Counter()


def test_consolidate_size_areas_splits_a_multi_size_job_by_share() -> None:
    multi = report(
        size=None,
        sizes=Counter({(8.0, 3.0): 6, (4.0, 2.0): 2}),
        labels=[label("A", 1.0)] * 8,
        copies=1,
        substrate=128.0,
    )
    areas = consolidate_size_areas([multi])
    assert sorted(areas) == [(4.0, 2.0), (8.0, 3.0)]
    # 6/8 of the job's substrate lands on the 8x3 bucket.
    assert areas[(8.0, 3.0)].substrate_sq_in == pytest.approx(96.0)
    assert areas[(4.0, 2.0)].substrate_sq_in == pytest.approx(32.0)
    # Linear feet uses the average roll width that printed the bucket (52in).
    assert areas[(8.0, 3.0)].linear_feet == pytest.approx(96.0 / (52.0 * 12.0))


def test_consolidate_size_areas_averages_roll_widths() -> None:
    narrow = report(input_file="a.txt", page_width_in=24.0, labels=[label("A", 1.0)])
    wide = report(input_file="b.txt", page_width_in=52.0, labels=[label("A", 1.0)])
    areas = consolidate_size_areas([narrow, wide])
    total_substrate = areas[(8.0, 3.0)].substrate_sq_in
    assert areas[(8.0, 3.0)].linear_feet == pytest.approx(
        total_substrate / (38.0 * 12.0)
    )


def test_consolidate_size_areas_reports_pricing(
    make_pricing_config: Callable[..., PricingConfig],
) -> None:
    priced = report(labels=[label("A", 1.0)], cost=breakdown(1, 2, 5, 5, 0.0))
    areas = consolidate_size_areas([priced], make_pricing_config())
    cost = areas[(8.0, 3.0)].cost_breakdown
    assert cost is not None
    assert cost.total_cost == pytest.approx(13.0)
    assert cost.unit_price == pytest.approx(14.3)


def test_consolidate_size_areas_without_pricing() -> None:
    areas = consolidate_size_areas([report(labels=[label("A", 1.0)])])
    assert areas[(8.0, 3.0)].cost_breakdown is None


def test_consolidate_size_areas_of_no_reports() -> None:
    assert consolidate_size_areas([]) == {}


# --- roll widths / pdf records --------------------------------------------------


def test_roll_widths_in_is_sorted_and_distinct() -> None:
    reports = [
        report(input_file="a.txt", page_width_in=52.0),
        report(input_file="b.txt", page_width_in=24.0),
        report(input_file="c.txt", page_width_in=52.0),
    ]
    assert roll_widths_in(reports) == [24.0, 52.0]
    assert roll_widths_in([]) == []


def test_build_pdf_file_records() -> None:
    first = report(
        input_file="a.txt",
        labels=[label("A", 1.0)],
        substrate=96.0,
        pdfs=[Path("/tmp/a_1.pdf"), Path("/tmp/a_2.pdf")],
        per_pdf=[pdf_metrics(2, 2, 4.0, 48.0), pdf_metrics(2, 2, 1.0, 24.0)],
    )
    records = build_pdf_file_records([first])
    assert [r.filename for r in records] == ["a_1.pdf", "a_2.pdf"]
    # yield ratio is substrate / material = 96 / 48 = 2 for this job.
    assert records[0].total_substrate_sq_in == pytest.approx(96.0)
    assert records[0].label_size == "8x3"
    assert records[0].linear_feet == pytest.approx(96.0 / (52.0 * 12.0))
    assert records[1].total_output_labels == 2


def test_build_pdf_file_records_carries_pricing() -> None:
    cb = breakdown(1, 2, 5, 5, 6.5)
    first = report(
        labels=[label("A", 1.0)],
        pdfs=[Path("/tmp/a.pdf")],
        per_pdf=[pdf_metrics(2, 2, 1.0, 24.0, cost=cb)],
    )
    (record,) = build_pdf_file_records([first])
    assert record.cost_breakdown == cb


def test_build_pdf_file_records_skips_unattributed_reports() -> None:
    assert build_pdf_file_records([report(labels=[label("A", 1.0)])]) == []
    without_metrics = report(labels=[label("A", 1.0)], pdfs=[Path("/tmp/a.pdf")])
    assert build_pdf_file_records([without_metrics]) == []
    without_pdfs = report(
        labels=[label("A", 1.0)], per_pdf=[pdf_metrics(1, 1, 1.0, 1.0)]
    )
    assert build_pdf_file_records([without_pdfs]) == []


def test_build_pdf_file_records_without_a_size() -> None:
    first = report(
        size=None,
        labels=[label("A", 1.0)],
        pdfs=[Path("/tmp/a.pdf")],
        per_pdf=[pdf_metrics(1, 1, 1.0, 24.0)],
    )
    (record,) = build_pdf_file_records([first])
    assert record.label_size == ""


def test_sort_pdf_records_orders_by_size_then_name() -> None:
    records = [
        PdfFileRecord("z.pdf", 1, 1, 1.0, 1.0, label_size="8x3"),
        PdfFileRecord("a.pdf", 1, 1, 1.0, 1.0, label_size="8x3"),
        PdfFileRecord("b.pdf", 1, 1, 1.0, 1.0, label_size="4x2"),
    ]
    assert [r.filename for r in sort_pdf_records(records)] == [
        "b.pdf",
        "a.pdf",
        "z.pdf",
    ]


# --- display_input_file / job_name ----------------------------------------------


def test_display_input_file_strips_the_directory_prefix(tmp_path: Path) -> None:
    inner = tmp_path / "json"
    inner.mkdir()
    assert display_input_file(str(inner / "a.txt"), tmp_path) == "json/a.txt"


def test_display_input_file_keeps_outside_paths(tmp_path: Path) -> None:
    assert (
        display_input_file("/somewhere/else/a.txt", tmp_path) == "/somewhere/else/a.txt"
    )


def test_display_input_file_of_a_relative_path(tmp_path: Path) -> None:
    # A bare name resolves into the current directory, which is inside tmp_path
    # because the suite runs from a temporary working directory.
    assert display_input_file("a.txt", Path.cwd()) == "a.txt"


def test_job_name_is_the_stem(tmp_path: Path) -> None:
    inner = tmp_path / "json"
    inner.mkdir()
    assert job_name(str(inner / "my_job.txt"), tmp_path) == "my_job"


def test_job_name_of_an_outside_path() -> None:
    assert job_name("/tmp/deep/my_job.txt", Path("/nonexistent-root")) == "my_job"
