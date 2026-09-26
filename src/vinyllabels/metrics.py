"""Metrics computation: per-label, per-job, and per-PDF.

Everything here is a pure function of its inputs plus a
:class:`~vinyllabels.layout.JobConfig`; nothing touches the filesystem, which
makes the numbers easy to pin down in tests.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from vinyllabels.ink import horizontal_scale, text_ink_area
from vinyllabels.layout import JobConfig, grid_page_height
from vinyllabels.models import (
    CostBreakdown,
    GlobalMetrics,
    LabelMetrics,
    PdfMetrics,
    PricingConfig,
    markup_unit_price,
    sum_cost_breakdowns,
)
from vinyllabels.pricing import compute_cost_breakdown
from vinyllabels.sizes import LabelSize
from vinyllabels.units import sq_ft

__all__ = [
    "apply_actual_page_heights",
    "calculate_metrics",
    "job_label_sizes",
    "per_pdf_metrics",
]


def calculate_metrics(
    labels: list[str],
    font_name: str,
    font_path: str | None,
    config: JobConfig,
    pricing_config: PricingConfig | None = None,
    flat_label_price: float | None = None,
) -> tuple[list[LabelMetrics], GlobalMetrics]:
    """Compute per-label and global metrics for ``labels``.

    The ink area per label is computed at the actual drawn scale (i.e. after
    horizontal compression), so summing it across instances yields the total
    ink area that will be deposited on the substrate. Per-label costs are for a
    single instance; the global breakdown scales them by the copy count.
    """
    copies = config.copies_per_label
    per_label: list[LabelMetrics] = []
    total_ink_area = 0.0
    total_characters = 0

    for text in labels:
        scale = horizontal_scale(
            text, font_name, config.text_area_w_in, config.font_size_pt
        )
        unscaled_area = text_ink_area(
            text, font_path, config.font_size_pt, config.cap_height_ratio
        )
        scaled_area = unscaled_area * scale

        cost = compute_cost_breakdown(
            sq_ft(scaled_area),
            sq_ft(config.label_area_sq_in),
            pricing_config,
            flat_label_price,
        )

        per_label.append(
            LabelMetrics(
                text=text,
                char_count=len(text),
                horizontal_scale=scale,
                ink_area_sq_in=scaled_area,
                cost_breakdown=cost,
            )
        )
        total_ink_area += scaled_area * copies
        total_characters += len(text) * copies

    total_output_labels = len(labels) * copies
    total_label_material = total_output_labels * config.label_area_sq_in
    page_h_in = grid_page_height(config, total_output_labels)
    total_substrate = config.page_w_in * page_h_in

    global_cost: CostBreakdown | None = None
    if pricing_config is not None:
        summed = sum_cost_breakdowns(
            metrics.cost_breakdown.scale(copies)
            for metrics in per_label
            if metrics.cost_breakdown is not None
        )
        # A job's headline price is derived from the job's own total, never from
        # the sum of its per-label prices: the per-label values are intermediate
        # and summing them both re-applies nothing and accumulates float drift.
        unit_price = (
            flat_label_price * total_output_labels
            if flat_label_price is not None
            else markup_unit_price(
                summed.total_cost if summed else 0.0, pricing_config.markup_percent
            )
        )
        summed_cost = summed if summed is not None else CostBreakdown.zero()
        global_cost = replace(summed_cost, unit_price=unit_price)

    global_metrics = GlobalMetrics(
        total_output_labels=total_output_labels,
        total_characters=total_characters,
        total_ink_area_sq_in=total_ink_area,
        total_label_material_sq_in=total_label_material,
        total_substrate_sq_in=total_substrate,
        material_yield_pct=(
            (total_label_material / total_substrate * 100.0) if total_substrate else 0.0
        ),
        average_ink_coverage_pct=(
            (total_ink_area / total_label_material * 100.0)
            if total_label_material
            else 0.0
        ),
        page_height_in=page_h_in,
        linear_feet=total_substrate / config.page_w_in / 12.0,
        cost_breakdown=global_cost,
    )
    return per_label, global_metrics


def per_pdf_metrics(
    per_label: list[LabelMetrics],
    page_break_ranges: list[tuple[int, int]],
    copies_per_label: int,
    label_area_sq_in: float,
) -> list[PdfMetrics]:
    """Aggregate metrics for each PDF, based on which instances it holds.

    ``page_break_ranges`` is a list of ``(start_idx, end_idx)`` tuples saying
    which instance indices belong to each PDF; instance ``i`` renders label
    ``i // copies_per_label``.
    """
    results: list[PdfMetrics] = []

    for start_idx, end_idx in page_break_ranges:
        instance_costs: list[CostBreakdown] = []
        total_labels = 0
        total_chars = 0
        total_ink_area = 0.0
        total_label_material = 0.0

        for instance_idx in range(start_idx, end_idx + 1):
            label_idx = instance_idx // copies_per_label
            if label_idx >= len(per_label):
                continue
            metric = per_label[label_idx]
            total_labels += 1
            total_chars += metric.char_count
            total_ink_area += metric.ink_area_sq_in
            total_label_material += label_area_sq_in
            if metric.cost_breakdown is not None:
                instance_costs.append(metric.cost_breakdown)

        # A PDF's price is reported per label, so replace the summed price with
        # the average cost per label rather than inheriting the summed value.
        summed = sum_cost_breakdowns(instance_costs)
        pdf_cost: CostBreakdown | None = None
        if summed is not None and (summed.ink_cost > 0 or summed.substrate_cost > 0):
            pdf_cost = replace(
                summed,
                unit_price=(
                    summed.total_cost / total_labels if total_labels > 0 else 0.0
                ),
            )

        results.append(
            PdfMetrics(
                total_output_labels=total_labels,
                total_characters=total_chars,
                total_ink_area_sq_in=total_ink_area,
                total_label_material_sq_in=total_label_material,
                cost_breakdown=pdf_cost,
            )
        )

    return results


def job_label_sizes(
    config: JobConfig, global_metrics: GlobalMetrics
) -> Counter[LabelSize]:
    """Return printed-label counts keyed by label size for a single job.

    A job prints every label at the same (design) size, so this is a single
    ``{(w, h): total_output_labels}`` entry; the ``Counter`` keeps it compatible
    with multi-job consolidation, which merges many such counters.
    """
    return Counter({config.label_size: global_metrics.total_output_labels})


def apply_actual_page_heights(
    global_metrics: GlobalMetrics, config: JobConfig, page_heights: list[float]
) -> GlobalMetrics:
    """Rebase ``global_metrics`` onto the page heights actually emitted.

    :func:`~vinyllabels.drawing.build_pdf` trims trailing whitespace from each
    page, so the substrate it consumes is smaller than the grid estimate used
    while planning. This recomputes substrate area, yield, page height and
    linear feet from the real heights.
    """
    actual_total_substrate_sq_in = sum(config.page_w_in * h for h in page_heights)
    if actual_total_substrate_sq_in <= 0:
        return global_metrics
    return replace(
        global_metrics,
        total_substrate_sq_in=actual_total_substrate_sq_in,
        page_height_in=sum(page_heights),
        material_yield_pct=(
            global_metrics.total_label_material_sq_in
            / actual_total_substrate_sq_in
            * 100.0
        ),
        linear_feet=actual_total_substrate_sq_in / config.page_w_in / 12.0,
    )
