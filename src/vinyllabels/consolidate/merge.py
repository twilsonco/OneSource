"""Folding many job reports into consolidated aggregates.

Every function here is pure: it takes parsed :class:`JobReport` values and
returns aggregates, so the arithmetic can be pinned down in tests without
touching the filesystem.

Two rules recur throughout and are worth stating once:

* Areas are summed, but percentages are *recomputed* from the summed areas --
  averaging per-job percentages would weight a small job the same as a large one.
* A price is either the sum of the prices it was built from (flat pricing, which
  carries no cost signal to markup from) or the summed cost with markup applied.
  :func:`~vinyllabels.models.finalize_unit_price` encodes exactly that choice.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from vinyllabels.consolidate.model import (
    ConsolidatedLabel,
    ConsolidatedLabelBySize,
    ConsolidatedMetrics,
    JobReport,
    PdfFileRecord,
    SizeAreas,
)
from vinyllabels.models import (
    CostBreakdown,
    PricingConfig,
    finalize_unit_price,
    sum_cost_breakdowns,
)
from vinyllabels.sizes import LabelSize, format_label_size

__all__ = [
    "build_pdf_file_records",
    "consolidate",
    "consolidate_label_sizes",
    "consolidate_labels",
    "consolidate_labels_by_size",
    "consolidate_size_areas",
    "display_input_file",
    "job_name",
    "roll_widths_in",
    "sort_pdf_records",
]

# Assumed roll width when a size bucket somehow has no job to read one from.
_DEFAULT_ROLL_WIDTH_IN = 52.0


def _markup_percent(pricing_config: PricingConfig | None) -> float:
    """Markup to fall back to when a breakdown carries no explicit price."""
    return pricing_config.markup_percent if pricing_config is not None else 0.0


def _weighted_sum(
    items: Iterable[tuple[CostBreakdown | None, float]],
) -> CostBreakdown | None:
    """Field-wise sum of each breakdown scaled by its weight.

    The weight is a job's ``copies_per_label`` when scaling one instance up to
    what was printed, or a size bucket's share of a job when splitting one job's
    totals across the sizes it printed.
    """
    return sum_cost_breakdowns(
        breakdown.scale(weight) for breakdown, weight in items if breakdown is not None
    )


def _finalize(
    summed: CostBreakdown | None, markup_percent: float
) -> CostBreakdown | None:
    """Resolve a summed breakdown's price, or ``None`` when there was nothing."""
    if summed is None:
        return None
    return replace(
        summed,
        unit_price=finalize_unit_price(
            summed.total_cost, summed.unit_price, markup_percent
        ),
    )


def consolidate(
    reports: list[JobReport], pricing_config: PricingConfig | None = None
) -> ConsolidatedMetrics:
    """Sum the per-job metrics into a single :class:`ConsolidatedMetrics`."""
    total_substrate = sum(r.global_metrics.total_substrate_sq_in for r in reports)
    total_material = sum(r.global_metrics.total_label_material_sq_in for r in reports)
    total_ink = sum(r.global_metrics.total_ink_area_sq_in for r in reports)

    global_cost: CostBreakdown | None = None
    if pricing_config is not None:
        summed = _weighted_sum((r.global_metrics.cost_breakdown, 1.0) for r in reports)
        if summed is not None:
            # Rebuild the total from its components rather than summing each
            # job's own total, so the headline figure cannot drift from the
            # lines printed above it.
            total_cost = (
                summed.ink_cost
                + summed.substrate_cost
                + summed.printer_cost
                + summed.labor_cost
            )
            global_cost = replace(
                summed,
                total_cost=total_cost,
                unit_price=finalize_unit_price(
                    total_cost, summed.unit_price, pricing_config.markup_percent
                ),
            )

    return ConsolidatedMetrics(
        total_jobs=len(reports),
        total_output_labels=sum(r.global_metrics.total_output_labels for r in reports),
        total_characters=sum(r.global_metrics.total_characters for r in reports),
        total_ink_area_sq_in=total_ink,
        total_label_material_sq_in=total_material,
        total_substrate_sq_in=total_substrate,
        material_yield_pct=(
            total_material / total_substrate * 100.0 if total_substrate else 0.0
        ),
        average_ink_coverage_pct=(
            total_ink / total_material * 100.0 if total_material else 0.0
        ),
        linear_feet=sum(r.global_metrics.linear_feet for r in reports),
        cost_breakdown=global_cost,
    )


def consolidate_labels(
    reports: list[JobReport], pricing_config: PricingConfig | None = None
) -> list[ConsolidatedLabel]:
    """Merge every job's per-label metrics by code, scaled up to printed copies.

    Each report stores one instance's ink area and character count, so both are
    multiplied by that job's ``copies_per_label`` before summing.
    """
    jobs_seen: Counter[str] = Counter()
    instances: Counter[str] = Counter()
    ink_areas: dict[str, float] = {}
    substrate_areas: dict[str, float] = {}
    sizes: dict[str, LabelSize] = {}
    costs: dict[str, list[tuple[CostBreakdown | None, float]]] = {}

    for report in reports:
        copies = report.copies_per_label
        size = report.label_size or (0.0, 0.0)
        material_per_copy = size[0] * size[1]
        yield_ratio = report.yield_ratio

        for label in report.per_label:
            text = label.text
            jobs_seen[text] += 1
            instances[text] += copies
            ink_areas[text] = ink_areas.get(text, 0.0) + label.ink_area_sq_in * copies
            substrate_areas[text] = substrate_areas.get(text, 0.0) + (
                material_per_copy * copies * yield_ratio
            )
            sizes.setdefault(text, size)
            costs.setdefault(text, []).append((label.cost_breakdown, copies))

    markup = _markup_percent(pricing_config)
    consolidated: list[ConsolidatedLabel] = []
    for text in sorted(ink_areas):
        entries = costs[text]
        has_costs = any(breakdown is not None for breakdown, _ in entries)
        consolidated.append(
            ConsolidatedLabel(
                text=text,
                jobs=jobs_seen[text],
                instances=instances[text],
                char_count=len(text) * instances[text],
                ink_area_sq_in=ink_areas[text],
                size_w_in=sizes[text][0],
                size_h_in=sizes[text][1],
                substrate_sq_in=substrate_areas.get(text, 0.0),
                cost_breakdown=_finalize(_weighted_sum(entries), markup)
                if has_costs
                else None,
            )
        )
    return consolidated


def _label_size_key_sort_key(key: tuple[str, LabelSize]) -> str:
    """Sort key reproducing the historical ``(text, size)`` string ordering.

    Rows have always come out ordered by the flattened ``"text::WxH"`` string,
    which sorts the size components lexicographically (``10x4`` before ``8x3``).
    The ordering is cosmetic but stable, so it is kept rather than silently
    renumbering rows in existing reports.
    """
    text, (width, height) = key
    return f"{text}::{width}x{height}"


def consolidate_labels_by_size(
    reports: list[JobReport], pricing_config: PricingConfig | None = None
) -> list[ConsolidatedLabelBySize]:
    """Consolidate label metrics grouped by label code *and* size.

    Each unique ``(text, size)`` pair is tracked separately, so one code printed
    at two sizes yields two rows. Since every label in a job shares that job's
    size, the size comes from the report.
    """
    # One entry per contributing job: (copies, ink, scale, text height). Kept as
    # a list because the same code can arrive from several jobs at the same size
    # and each job contributes its own share.
    contributions: dict[
        tuple[str, LabelSize], list[tuple[int, float, float, float]]
    ] = {}
    cost_contributions: dict[
        tuple[str, LabelSize], list[tuple[CostBreakdown | None, float]]
    ] = {}
    substrate: dict[tuple[str, LabelSize], float] = {}

    for report in reports:
        size = report.label_size
        if size is None:
            continue
        copies = report.copies_per_label
        yield_ratio = report.yield_ratio
        for label in report.per_label:
            bucket = (label.text, size)
            contributions.setdefault(bucket, []).append(
                (
                    copies,
                    label.ink_area_sq_in,
                    label.horizontal_scale,
                    report.text_height_in,
                )
            )
            cost_contributions.setdefault(bucket, []).append(
                (label.cost_breakdown, copies)
            )
            # Substrate depends on the job's roll, so it is accumulated per job
            # rather than derived from the averaged fields below.
            substrate[bucket] = substrate.get(bucket, 0.0) + (
                size[0] * size[1] * copies * yield_ratio
            )

    markup = _markup_percent(pricing_config)
    result: list[ConsolidatedLabelBySize] = []
    for bucket in sorted(contributions, key=_label_size_key_sort_key):
        text, size = bucket
        entries = contributions[bucket]
        total_instances = sum(copies for copies, _, _, _ in entries)

        cost_sum = _weighted_sum(cost_contributions[bucket])
        cost_breakdown = (
            _finalize(cost_sum, markup)
            if cost_sum is not None and cost_sum.total_cost > 0
            else None
        )

        result.append(
            ConsolidatedLabelBySize(
                text=text,
                label_size=size,
                instances=total_instances,
                char_count=len(text) * total_instances,
                ink_area_sq_in=sum(copies * ink for copies, ink, _, _ in entries),
                substrate_sq_in=substrate.get(bucket, 0.0),
                horizontal_scale=_weighted_average(
                    [(copies, scale) for copies, _, scale, _ in entries], default=1.0
                ),
                linear_feet=size[1] * total_instances / 12.0,
                text_height_in=_weighted_average(
                    [(copies, height) for copies, _, _, height in entries], default=2.0
                ),
                cost_breakdown=cost_breakdown,
            )
        )
    return result


def _weighted_average(pairs: list[tuple[int, float]], *, default: float) -> float:
    """Mean of ``pairs`` weighted by their first element, or ``default`` if empty."""
    total_weight = sum(weight for weight, _ in pairs)
    if total_weight <= 0:
        return default
    return sum(weight * value for weight, value in pairs) / total_weight


def consolidate_label_sizes(reports: list[JobReport]) -> Counter[LabelSize]:
    """Merge every job's label-size counts into one counter."""
    total: Counter[LabelSize] = Counter()
    for report in reports:
        total.update(report.label_sizes)
    return total


def consolidate_size_areas(
    reports: list[JobReport], pricing_config: PricingConfig | None = None
) -> dict[LabelSize, SizeAreas]:
    """Sum each job's area totals into the label size(s) it printed.

    A job prints a single design size, so its full substrate/label/ink areas land
    on that size; should a job ever mix sizes, its areas are split by
    label-count share. Linear feet is the bucket's substrate divided by the
    average roll width that printed it.
    """
    substrate: dict[LabelSize, float] = {}
    material: dict[LabelSize, float] = {}
    ink: dict[LabelSize, float] = {}
    roll_widths: dict[LabelSize, list[float]] = {}
    cost_shares: dict[LabelSize, list[tuple[CostBreakdown | None, float]]] = {}

    for report in reports:
        gm = report.global_metrics
        total_labels = sum(report.label_sizes.values()) or 1
        for size, count in report.label_sizes.items():
            share = count / total_labels
            substrate[size] = (
                substrate.get(size, 0.0) + gm.total_substrate_sq_in * share
            )
            material[size] = (
                material.get(size, 0.0) + gm.total_label_material_sq_in * share
            )
            ink[size] = ink.get(size, 0.0) + gm.total_ink_area_sq_in * share
            roll_widths.setdefault(size, []).append(report.page_width_in)
            cost_shares.setdefault(size, []).append((gm.cost_breakdown, share))

    markup = _markup_percent(pricing_config)
    result: dict[LabelSize, SizeAreas] = {}
    for size, substrate_sq_in in substrate.items():
        widths = roll_widths.get(size) or [_DEFAULT_ROLL_WIDTH_IN]
        avg_width = sum(widths) / len(widths)

        cost_sum = _weighted_sum(cost_shares[size])
        cost_breakdown = (
            _finalize(cost_sum, markup)
            if cost_sum is not None and cost_sum.total_cost > 0
            else None
        )

        result[size] = SizeAreas(
            substrate_sq_in=substrate_sq_in,
            label_material_sq_in=material[size],
            ink_area_sq_in=ink[size],
            linear_feet=substrate_sq_in / (avg_width * 12.0),
            cost_breakdown=cost_breakdown,
        )
    return result


def roll_widths_in(reports: list[JobReport]) -> list[float]:
    """Return the distinct roll widths (inches) used across ``reports``, sorted."""
    return sorted({report.page_width_in for report in reports})


def build_pdf_file_records(reports: list[JobReport]) -> list[PdfFileRecord]:
    """Pair each emitted PDF with its metrics, for the file-breakdown reports.

    Only reports that recorded ``per_pdf`` metrics contribute; older single-PDF
    reports have nothing to attribute per file.
    """
    records: list[PdfFileRecord] = []
    for report in reports:
        if not report.output_pdf_files or not report.per_pdf_metrics:
            continue
        label_size = format_label_size(report.label_size) if report.label_size else ""
        yield_ratio = report.yield_ratio
        for pdf_path, pdf_metric in zip(
            report.output_pdf_files, report.per_pdf_metrics
        ):
            substrate_sq_in = pdf_metric.total_label_material_sq_in * yield_ratio
            records.append(
                PdfFileRecord(
                    filename=pdf_path.name,
                    total_output_labels=pdf_metric.total_output_labels,
                    total_characters=pdf_metric.total_characters,
                    total_ink_area_sq_in=pdf_metric.total_ink_area_sq_in,
                    total_label_material_sq_in=pdf_metric.total_label_material_sq_in,
                    total_substrate_sq_in=substrate_sq_in,
                    label_size=label_size,
                    linear_feet=substrate_sq_in / (report.page_width_in * 12.0),
                    cost_breakdown=pdf_metric.cost_breakdown,
                )
            )
    return records


def sort_pdf_records(records: list[PdfFileRecord]) -> list[PdfFileRecord]:
    """Order PDF records by size then filename, the vendor-friendly ordering."""
    return sorted(records, key=lambda record: (record.label_size, record.filename))


def display_input_file(input_file: str, directory: Path) -> str:
    """Return ``input_file`` relative to ``directory`` when it lies inside it.

    The per-job reports store the input path as it was passed to ``generate``;
    for the job-breakdown table that prefix is noise, so it is stripped. Paths
    outside ``directory`` (or that otherwise cannot be made relative) are
    returned unchanged.
    """
    try:
        return str(Path(input_file).resolve().relative_to(directory.resolve()))
    except ValueError:
        return input_file


def job_name(input_file: str, directory: Path) -> str:
    """Short job label for a report: the input file's stem, without directories."""
    return Path(display_input_file(input_file, directory)).stem
