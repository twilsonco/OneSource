"""Shared value objects: metrics, cost breakdown, and pricing configuration.

These dataclasses are the vocabulary shared by the layout engine, the per-job
reports, and the multi-job consolidation. They carry no I/O and no third-party
types, so importing this module is cheap and safe from anywhere.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

__all__ = [
    "CostBreakdown",
    "GlobalMetrics",
    "LabelMetrics",
    "PdfMetrics",
    "PricingConfig",
    "finalize_unit_price",
    "markup_unit_price",
    "sum_cost_breakdowns",
]


@dataclass(frozen=True)
class CostBreakdown:
    """Per-unit cost breakdown for label production.

    ``unit_price`` is the *total* price for the unit this breakdown describes
    (one label, one job, one size bucket, ...), either a flat price or the
    markup-adjusted ``total_cost``.
    """

    ink_cost: float
    substrate_cost: float
    printer_hours: float
    printer_cost: float
    labor_hours: float
    labor_cost: float
    total_cost: float
    unit_price: float  # Either markup-based or flat price

    @staticmethod
    def zero() -> CostBreakdown:
        """Return an all-zero breakdown (the additive identity)."""
        return CostBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    def __add__(self, other: CostBreakdown) -> CostBreakdown:
        """Return the field-wise sum of two breakdowns."""
        return CostBreakdown(
            ink_cost=self.ink_cost + other.ink_cost,
            substrate_cost=self.substrate_cost + other.substrate_cost,
            printer_hours=self.printer_hours + other.printer_hours,
            printer_cost=self.printer_cost + other.printer_cost,
            labor_hours=self.labor_hours + other.labor_hours,
            labor_cost=self.labor_cost + other.labor_cost,
            total_cost=self.total_cost + other.total_cost,
            unit_price=self.unit_price + other.unit_price,
        )

    def scale(self, factor: float) -> CostBreakdown:
        """Return a breakdown with every field multiplied by ``factor``.

        Used when a stored per-instance breakdown must represent ``factor``
        printed copies, or a fractional share of a job's totals.
        """
        return CostBreakdown(
            ink_cost=self.ink_cost * factor,
            substrate_cost=self.substrate_cost * factor,
            printer_hours=self.printer_hours * factor,
            printer_cost=self.printer_cost * factor,
            labor_hours=self.labor_hours * factor,
            labor_cost=self.labor_cost * factor,
            total_cost=self.total_cost * factor,
            unit_price=self.unit_price * factor,
        )


def sum_cost_breakdowns(items: Iterable[CostBreakdown | None]) -> CostBreakdown | None:
    """Field-wise sum of ``items``, ignoring ``None`` entries.

    Returns ``None`` when nothing was summable, so callers can keep rendering
    "no pricing available" as an absent breakdown rather than a row of zeros.
    """
    total: CostBreakdown | None = None
    for item in items:
        if item is None:
            continue
        total = item if total is None else total + item
    return total


def markup_unit_price(total_cost: float, markup_percent: float) -> float:
    """Return ``total_cost`` with ``markup_percent`` applied.

    The single place the markup formula is written, so a job's headline price and
    its aggregated parents cannot drift apart.
    """
    return total_cost * (1.0 + markup_percent / 100.0)


def finalize_unit_price(
    total_cost: float, summed_unit_price: float, markup_percent: float
) -> float:
    """Resolve the price to report for an aggregated breakdown.

    When the contributing breakdowns already carried explicit prices (flat
    pricing) their summed price is authoritative; otherwise the price is derived
    from ``total_cost`` plus ``markup_percent``. Every aggregation in the
    codebase follows this same rule, which is why it lives here once.
    """
    if summed_unit_price > 0:
        return summed_unit_price
    return markup_unit_price(total_cost, markup_percent)


@dataclass(frozen=True)
class PricingConfig:
    """Cost configuration for computing label production costs."""

    ink_cost_usd_per_sqft: float
    substrate_cost_usd_per_sqft: float
    print_rate_hours_per_sqft: float
    printer_run_cost_usd_per_hour: float
    labor_rate_usd_per_hour: float
    labor_time_factor: float
    markup_percent: float
    customer_report: dict[str, bool] | None = None


@dataclass(frozen=True)
class LabelMetrics:
    """Per-unique-label metrics. All areas are for a single instance."""

    text: str
    char_count: int
    horizontal_scale: float
    ink_area_sq_in: float  # already includes the horizontal scale factor
    cost_breakdown: CostBreakdown | None = None


@dataclass(frozen=True)
class GlobalMetrics:
    """Aggregated metrics across all printed instances."""

    total_output_labels: int
    total_characters: int
    total_ink_area_sq_in: float
    total_label_material_sq_in: float
    total_substrate_sq_in: float
    material_yield_pct: float
    average_ink_coverage_pct: float
    page_height_in: float
    linear_feet: float  # substrate length along the roll, in linear feet
    cost_breakdown: CostBreakdown | None = None


@dataclass(frozen=True)
class PdfMetrics:
    """Aggregated metrics for a single PDF file (one or more pages)."""

    total_output_labels: int
    total_characters: int
    total_ink_area_sq_in: float
    total_label_material_sq_in: float
    cost_breakdown: CostBreakdown | None = None
