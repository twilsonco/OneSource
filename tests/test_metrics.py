"""Tests for :mod:`vinyllabels.metrics`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from vinyllabels.ink import horizontal_scale
from vinyllabels.layout import JobConfig
from vinyllabels.metrics import (
    apply_actual_page_heights,
    calculate_metrics,
    job_label_sizes,
    per_pdf_metrics,
)
from vinyllabels.models import CostBreakdown, GlobalMetrics, LabelMetrics, PricingConfig

# The built-in font always resolves, so metrics tests need no font file.
BUILTIN_FONT = "Helvetica-Bold"

# Fallback ink estimate per character at the default 2in cap height:
#   width = 144pt * 0.60, area = width * 144pt * 0.45, / 72**2 -> 1.08 sq in.
INK_SQ_IN_PER_CHAR = 1.08


def test_calculate_metrics_without_pricing(
    make_job_config: Callable[..., JobConfig],
) -> None:
    config = make_job_config()
    per_label, global_metrics = calculate_metrics(["AB"], BUILTIN_FONT, None, config)

    assert len(per_label) == 1
    label = per_label[0]
    assert label.text == "AB"
    assert label.char_count == 2
    assert label.horizontal_scale == 1.0
    assert label.ink_area_sq_in == pytest.approx(2 * INK_SQ_IN_PER_CHAR)
    assert label.cost_breakdown is None
    assert global_metrics.cost_breakdown is None


def test_calculate_metrics_global_totals(
    make_job_config: Callable[..., JobConfig],
) -> None:
    config = make_job_config()
    _, global_metrics = calculate_metrics(["AB"], BUILTIN_FONT, None, config)

    assert global_metrics.total_output_labels == 2
    assert global_metrics.total_characters == 4
    assert global_metrics.total_ink_area_sq_in == pytest.approx(2 * 2.16)
    assert global_metrics.total_label_material_sq_in == pytest.approx(48.0)
    assert global_metrics.total_substrate_sq_in == pytest.approx(52.0 * 26.0)
    assert global_metrics.page_height_in == pytest.approx(26.0)
    assert global_metrics.linear_feet == pytest.approx(1352.0 / 52.0 / 12.0)
    assert global_metrics.material_yield_pct == pytest.approx(48.0 / 1352.0 * 100.0)
    assert global_metrics.average_ink_coverage_pct == pytest.approx(4.32 / 48.0 * 100.0)


def test_calculate_metrics_applies_horizontal_compression(
    make_job_config: Callable[..., JobConfig],
) -> None:
    config = make_job_config(label_w_in=1.0, label_h_margin_in=0.25)
    per_label, _ = calculate_metrics(["ABABABAB"], BUILTIN_FONT, None, config)
    label = per_label[0]
    expected_scale = horizontal_scale(
        "ABABABAB", BUILTIN_FONT, config.text_area_w_in, config.font_size_pt
    )
    assert expected_scale < 1.0
    assert label.horizontal_scale == pytest.approx(expected_scale)
    # Ink is reported at the drawn scale, i.e. reduced along with the glyphs.
    assert label.ink_area_sq_in == pytest.approx(
        8 * INK_SQ_IN_PER_CHAR * expected_scale
    )


def test_calculate_metrics_with_pricing(
    make_job_config: Callable[..., JobConfig],
    make_pricing_config: Callable[..., PricingConfig],
) -> None:
    config = make_job_config()
    per_label, global_metrics = calculate_metrics(
        ["AB"], BUILTIN_FONT, None, config, make_pricing_config()
    )

    label_cost = per_label[0].cost_breakdown
    assert label_cost is not None
    assert label_cost.ink_cost == pytest.approx(2.16 / 144.0)
    assert label_cost.substrate_cost == pytest.approx(24.0 / 144.0 * 2.0)
    assert label_cost.printer_hours == pytest.approx(24.0 / 144.0 * 0.5)
    assert label_cost.printer_cost == pytest.approx(24.0 / 144.0 * 0.5 * 10.0)
    assert label_cost.labor_hours == pytest.approx(24.0 / 144.0 * 0.5 * 0.5)
    assert label_cost.labor_cost == pytest.approx(24.0 / 144.0 * 0.5 * 0.5 * 20.0)

    global_cost = global_metrics.cost_breakdown
    assert global_cost is not None
    # Two printed instances of the per-label breakdown.
    assert global_cost.total_cost == pytest.approx(2 * label_cost.total_cost)
    # The headline price is the job total with markup, not the summed prices.
    assert global_cost.unit_price == pytest.approx(2 * label_cost.total_cost * 1.1)


def test_calculate_metrics_flat_price_wins_over_markup(
    make_job_config: Callable[..., JobConfig],
    make_pricing_config: Callable[..., PricingConfig],
) -> None:
    config = make_job_config()
    per_label, global_metrics = calculate_metrics(
        ["AB"], BUILTIN_FONT, None, config, make_pricing_config(), 3.0
    )
    assert per_label[0].cost_breakdown is not None
    assert per_label[0].cost_breakdown.unit_price == 3.0
    assert global_metrics.cost_breakdown is not None
    assert global_metrics.cost_breakdown.unit_price == pytest.approx(6.0)


def test_calculate_metrics_empty_label_list(
    make_job_config: Callable[..., JobConfig],
) -> None:
    config = make_job_config()
    per_label, global_metrics = calculate_metrics([], BUILTIN_FONT, None, config)
    assert per_label == []
    assert global_metrics.total_output_labels == 0
    assert global_metrics.total_substrate_sq_in > 0
    assert global_metrics.material_yield_pct == pytest.approx(0.0)
    assert global_metrics.average_ink_coverage_pct == 0.0


def test_calculate_metrics_vertical_labels_use_the_design_area(
    make_job_config: Callable[..., JobConfig],
) -> None:
    flat = make_job_config()
    vertical = make_job_config(vertical_labels=True)
    _, flat_global = calculate_metrics(["AB"], BUILTIN_FONT, None, flat)
    _, vertical_global = calculate_metrics(["AB"], BUILTIN_FONT, None, vertical)
    assert (
        vertical_global.total_label_material_sq_in
        == flat_global.total_label_material_sq_in
    )
    # The rotated footprint is taller, so the page (and thus the substrate) grows.
    assert vertical_global.page_height_in > flat_global.page_height_in


def test_per_pdf_metrics_splits_instances_by_page() -> None:
    per_label = [
        LabelMetrics(text="AA", char_count=2, horizontal_scale=1.0, ink_area_sq_in=2.0),
        LabelMetrics(
            text="BBB", char_count=3, horizontal_scale=1.0, ink_area_sq_in=3.0
        ),
    ]
    results = per_pdf_metrics(per_label, [(0, 1), (2, 3)], 2, 24.0)
    assert [r.total_output_labels for r in results] == [2, 2]
    assert [r.total_characters for r in results] == [4, 6]
    assert [r.total_ink_area_sq_in for r in results] == [4.0, 6.0]
    assert [r.total_label_material_sq_in for r in results] == [48.0, 48.0]
    assert [r.cost_breakdown for r in results] == [None, None]


def test_per_pdf_metrics_skips_labels_beyond_the_end() -> None:
    per_label = [
        LabelMetrics(text="AA", char_count=2, horizontal_scale=1.0, ink_area_sq_in=2.0)
    ]
    (only,) = per_pdf_metrics(per_label, [(0, 9)], 2, 24.0)
    # Instances 0 and 1 map to the only known label; 2..9 fall off the end.
    assert only.total_output_labels == 2
    assert only.total_characters == 4


def test_per_pdf_metrics_reports_an_average_price_per_label(
    make_cost_breakdown: Callable[..., CostBreakdown],
) -> None:
    breakdown = make_cost_breakdown(
        ink_cost=1.0, substrate_cost=2.0, total_cost=13.0, unit_price=14.3
    )
    per_label = [
        LabelMetrics(
            text="AA",
            char_count=2,
            horizontal_scale=1.0,
            ink_area_sq_in=2.0,
            cost_breakdown=breakdown,
        )
    ]
    (only,) = per_pdf_metrics(per_label, [(0, 3)], 2, 24.0)
    assert only.cost_breakdown is not None
    assert only.cost_breakdown.ink_cost == pytest.approx(2.0)
    assert only.cost_breakdown.total_cost == pytest.approx(26.0)
    # unit_price is replaced by total_cost / labels, not the summed price.
    assert only.cost_breakdown.unit_price == pytest.approx(26.0 / 2)


def test_per_pdf_metrics_drops_a_zero_cost_breakdown() -> None:
    zero = CostBreakdown.zero()
    per_label = [
        LabelMetrics(
            text="AA",
            char_count=2,
            horizontal_scale=1.0,
            ink_area_sq_in=2.0,
            cost_breakdown=zero,
        )
    ]
    (only,) = per_pdf_metrics(per_label, [(0, 1)], 2, 24.0)
    assert only.cost_breakdown is None


def test_job_label_sizes_is_a_single_entry(
    make_job_config: Callable[..., JobConfig],
) -> None:
    config = make_job_config(label_w_in=4.0, label_h_in=2.0)
    metrics = GlobalMetrics(
        total_output_labels=7,
        total_characters=0,
        total_ink_area_sq_in=0.0,
        total_label_material_sq_in=0.0,
        total_substrate_sq_in=0.0,
        material_yield_pct=0.0,
        average_ink_coverage_pct=0.0,
        page_height_in=0.0,
        linear_feet=0.0,
    )
    assert job_label_sizes(config, metrics) == {(4.0, 2.0): 7}


def _global_metrics(material: float, substrate: float) -> GlobalMetrics:
    return GlobalMetrics(
        total_output_labels=1,
        total_characters=1,
        total_ink_area_sq_in=1.0,
        total_label_material_sq_in=material,
        total_substrate_sq_in=substrate,
        material_yield_pct=material / substrate * 100.0,
        average_ink_coverage_pct=0.0,
        page_height_in=substrate,
        linear_feet=substrate / 52.0 / 12.0,
    )


def test_apply_actual_page_heights_rebases_the_numbers(
    make_job_config: Callable[..., JobConfig],
) -> None:
    config = make_job_config()
    updated = apply_actual_page_heights(
        _global_metrics(24.0, 100.0), config, [10.0, 20.0]
    )
    assert updated.total_substrate_sq_in == pytest.approx(52.0 * 30.0)
    assert updated.page_height_in == pytest.approx(30.0)
    assert updated.material_yield_pct == pytest.approx(24.0 / (52.0 * 30.0) * 100.0)
    assert updated.linear_feet == pytest.approx((52.0 * 30.0) / 52.0 / 12.0)


def test_apply_actual_page_heights_ignores_a_zero_total(
    make_job_config: Callable[..., JobConfig],
) -> None:
    config = make_job_config()
    original = _global_metrics(24.0, 100.0)
    assert apply_actual_page_heights(original, config, []) is original


def test_metrics_are_independent_of_page_size_state(
    make_job_config: Callable[..., JobConfig], tmp_path: Path
) -> None:
    """Two configs with different geometry never share derived state."""
    small = make_job_config(input_path=tmp_path / "a.txt", label_w_in=4.0)
    large = make_job_config(input_path=tmp_path / "b.txt", label_w_in=8.0)
    calculate_metrics(["AB"], BUILTIN_FONT, None, small)
    _, large_global = calculate_metrics(["AB"], BUILTIN_FONT, None, large)
    assert large_global.total_label_material_sq_in == pytest.approx(48.0)
