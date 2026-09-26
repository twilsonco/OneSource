"""Tests for :mod:`vinyllabels.models`."""

from __future__ import annotations

from collections.abc import Callable
import dataclasses

import pytest

from vinyllabels.models import (
    CostBreakdown,
    GlobalMetrics,
    LabelMetrics,
    PdfMetrics,
    PricingConfig,
    finalize_unit_price,
    markup_unit_price,
    sum_cost_breakdowns,
)


def make_breakdown(value: float) -> CostBreakdown:
    """A breakdown where every field equals ``value`` (for arithmetic checks)."""
    return CostBreakdown(*(value,) * 8)


def test_zero_is_the_additive_identity() -> None:
    zero = CostBreakdown.zero()
    assert (zero.ink_cost, zero.total_cost, zero.unit_price) == (0.0, 0.0, 0.0)
    breakdown = make_breakdown(1.5)
    assert breakdown + zero == breakdown


def test_add_is_field_wise() -> None:
    total = make_breakdown(1.0) + make_breakdown(2.5)
    assert total == make_breakdown(3.5)


def test_scale_multiplies_every_field() -> None:
    assert make_breakdown(2.0).scale(3.0) == make_breakdown(6.0)
    assert make_breakdown(2.0).scale(0.0) == CostBreakdown.zero()


def test_sum_cost_breakdowns_ignores_none() -> None:
    total = sum_cost_breakdowns([make_breakdown(1.0), None, make_breakdown(2.0)])
    assert total == make_breakdown(3.0)


def test_sum_cost_breakdowns_returns_none_when_nothing_summable() -> None:
    assert sum_cost_breakdowns([]) is None
    assert sum_cost_breakdowns([None, None]) is None


def test_sum_cost_breakdowns_single_item_is_that_item() -> None:
    only = make_breakdown(4.0)
    assert sum_cost_breakdowns([only]) is only


@pytest.mark.parametrize(
    ("total_cost", "markup", "expected"),
    [(100.0, 10.0, 110.0), (100.0, 0.0, 100.0), (50.0, -50.0, 25.0), (0.0, 25.0, 0.0)],
)
def test_markup_unit_price(total_cost: float, markup: float, expected: float) -> None:
    assert markup_unit_price(total_cost, markup) == pytest.approx(expected)


def test_finalize_prefers_explicit_prices() -> None:
    assert finalize_unit_price(100.0, 42.0, 10.0) == 42.0


def test_finalize_falls_back_to_markup() -> None:
    assert finalize_unit_price(100.0, 0.0, 10.0) == pytest.approx(110.0)
    assert finalize_unit_price(100.0, -1.0, 10.0) == pytest.approx(110.0)


def test_cost_breakdown_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        make_breakdown(1.0).ink_cost = 2.0  # type: ignore[misc]


def test_pricing_config_carries_optional_customer_report(
    make_pricing_config: Callable[..., PricingConfig],
) -> None:
    assert make_pricing_config().customer_report is None
    flagged = make_pricing_config(customer_report={"ink_cost": True})
    assert flagged.customer_report == {"ink_cost": True}


def test_metric_dataclasses_default_to_no_pricing() -> None:
    label = LabelMetrics(
        text="A", char_count=1, horizontal_scale=1.0, ink_area_sq_in=0.5
    )
    assert label.cost_breakdown is None

    pdf = PdfMetrics(
        total_output_labels=1,
        total_characters=1,
        total_ink_area_sq_in=0.5,
        total_label_material_sq_in=24.0,
    )
    assert pdf.cost_breakdown is None

    glob = GlobalMetrics(
        total_output_labels=1,
        total_characters=1,
        total_ink_area_sq_in=0.5,
        total_label_material_sq_in=24.0,
        total_substrate_sq_in=48.0,
        material_yield_pct=50.0,
        average_ink_coverage_pct=1.0,
        page_height_in=6.0,
        linear_feet=0.5,
    )
    assert glob.cost_breakdown is None
