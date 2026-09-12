"""Consolidate vinyl label job metrics from every ``*_report.json`` in a directory.

Reads all ``*_report.json`` files in the target directory (the machine-readable
reports emitted by ``scripts/vinyl_label_prep.py``) and writes one consolidated
text report covering the combined material yield and ink usage across all jobs,
in the same style as the per-job reports. A machine-readable JSON sibling with
the same data is written next to it, along with a per-job and a per-label
breakdown.

Percentages are recomputed from the summed areas (averaging the per-job
percentages would weight them wrongly), and the linear footage sums each job's
own roll length, so jobs with different page widths consolidate correctly.

Usage::

    uv run python scripts/vinyl_label_multi_job_report.py <directory>
    uv run python scripts/vinyl_label_multi_job_report.py <directory> -o out/combined.txt
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

# Make the sibling module importable whether this script is run directly
# (``python scripts/vinyl_label_multi_job_report.py``) or as a module
# (``python -m scripts.vinyl_label_multi_job_report``).
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from vinyl_label_prep import (  # noqa: E402
    CostBreakdown,
    GlobalMetrics,
    LabelMetrics,
    LabelSize,
    PricingConfig,
    format_label_size,
    label_sizes_to_json,
    resolve_pricing_config,
    sq_ft,
    with_sq_ft,
)

# --- Input model ---------------------------------------------------------------


@dataclass(frozen=True)
class JobReport:
    """One parsed ``*_report.json`` file, plus the metadata this script needs."""

    path: Path
    input_file: str
    page_width_in: float
    copies_per_label: int
    global_metrics: GlobalMetrics
    label_sizes: Counter[LabelSize]
    per_label: list[LabelMetrics]


# --- Output model --------------------------------------------------------------


@dataclass(frozen=True)
class ConsolidatedMetrics:
    """Totals summed across every job report."""

    total_jobs: int
    total_output_labels: int
    total_characters: int
    total_ink_area_sq_in: float
    total_label_material_sq_in: float
    total_substrate_sq_in: float
    material_yield_pct: float
    average_ink_coverage_pct: float
    linear_feet: float
    cost_breakdown: CostBreakdown | None = None


@dataclass(frozen=True)
class ConsolidatedLabel:
    """A single label code, summed over every printed instance of it.

    ``instances`` and ``ink_area_sq_in`` count every copy printed across every
    job, so the per-tag table sums to the global totals.
    """

    text: str
    jobs: int  # number of job reports this code appears in
    instances: int  # printed instances summed across those jobs
    char_count: int  # characters summed across those instances
    ink_area_sq_in: float  # ink area summed across those instances
    cost_breakdown: CostBreakdown | None = None


@dataclass(frozen=True)
class ConsolidatedLabelBySize:
    """A single label code at a specific size, summed across jobs.

    Used for per-tag-by-size breakdown reporting where each unique (text, size)
    combination is tracked separately with its own aggregated metrics.
    """

    text: str
    label_size: LabelSize
    instances: int  # printed instances at this size
    char_count: int  # total characters at this size
    ink_area_sq_in: float  # ink area at this size
    horizontal_scale: float  # average scale factor across instances
    linear_feet: float  # linear feet of substrate used (height * copies / 12)
    cost_breakdown: CostBreakdown | None = None


@dataclass(frozen=True)
class SizeAreas:
    """Area totals (sq inches) accumulated for one label size across jobs."""

    substrate_sq_in: float = 0.0
    label_material_sq_in: float = 0.0
    ink_area_sq_in: float = 0.0
    cost_breakdown: CostBreakdown | None = None


# --- Loading -------------------------------------------------------------------


def _field(mapping: dict[str, object], key: str, path: Path) -> object:
    """Return ``mapping[key]`` or exit with a message naming the bad report."""
    if key not in mapping:
        raise SystemExit(f"{path}: missing '{key}'")
    return mapping[key]


def _as_float(mapping: dict[str, object], key: str, path: Path) -> float:
    """Return ``mapping[key]`` as a float or exit with a message."""
    value = _field(mapping, key, path)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SystemExit(f"{path}: '{key}' is not a number (got {value!r})")
    return float(value)


def _as_int(mapping: dict[str, object], key: str, path: Path) -> int:
    """Return ``mapping[key]`` as an int or exit with a message."""
    value = _field(mapping, key, path)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SystemExit(f"{path}: '{key}' is not an integer (got {value!r})")
    return int(value)


def _as_str(mapping: dict[str, object], key: str, path: Path) -> str:
    """Return ``mapping[key]`` as a string or exit with a message."""
    value = _field(mapping, key, path)
    if not isinstance(value, str):
        raise SystemExit(f"{path}: '{key}' is not a string (got {value!r})")
    return value


def _as_section(report: dict[str, object], key: str, path: Path) -> dict[str, object]:
    """Return ``report[key]`` as an object or exit with a message."""
    value = _field(report, key, path)
    if not isinstance(value, dict):
        raise SystemExit(f"{path}: '{key}' is not a JSON object")
    return {str(k): v for k, v in value.items()}


def _load_cost_breakdown(section: dict[str, object]) -> CostBreakdown | None:
    """Load a CostBreakdown from JSON section, returning None if fields missing."""
    # Check if section has a nested 'cost_breakdown' object (from JSON structure)
    cost_data = section.get("cost_breakdown")
    if isinstance(cost_data, dict):
        section_to_parse = cost_data
    else:
        # Fallback to looking for cost fields directly in section
        section_to_parse = section

    required_keys = {
        "ink_cost",
        "substrate_cost",
        "printer_hours",
        "printer_cost",
        "labor_hours",
        "labor_cost",
        "total_cost",
        "unit_price",
    }
    if not required_keys.issubset(section_to_parse.keys()):
        return None
    try:
        # Type-check before conversion
        for key in required_keys:
            val = section_to_parse[key]
            if isinstance(val, bool) or not isinstance(val, int | float):
                return None
        # Now we know all values are int or float, use cast to tell mypy
        return CostBreakdown(
            ink_cost=float(section_to_parse["ink_cost"]),
            substrate_cost=float(section_to_parse["substrate_cost"]),
            printer_hours=float(section_to_parse["printer_hours"]),
            printer_cost=float(section_to_parse["printer_cost"]),
            labor_hours=float(section_to_parse["labor_hours"]),
            labor_cost=float(section_to_parse["labor_cost"]),
            total_cost=float(section_to_parse["total_cost"]),
            unit_price=float(section_to_parse["unit_price"]),
        )
    except (ValueError, TypeError):
        return None


def _load_global(section: dict[str, object], path: Path) -> GlobalMetrics:
    """Build a :class:`GlobalMetrics` from a report's ``global`` section."""
    cost_breakdown = _load_cost_breakdown(section)
    return GlobalMetrics(
        total_output_labels=_as_int(section, "total_output_labels", path),
        total_characters=_as_int(section, "total_characters", path),
        total_ink_area_sq_in=_as_float(section, "total_ink_area_sq_in", path),
        total_label_material_sq_in=_as_float(
            section, "total_label_material_sq_in", path
        ),
        total_substrate_sq_in=_as_float(section, "total_substrate_sq_in", path),
        material_yield_pct=_as_float(section, "material_yield_pct", path),
        average_ink_coverage_pct=_as_float(section, "average_ink_coverage_pct", path),
        page_height_in=_as_float(section, "page_height_in", path),
        linear_feet=_as_float(section, "linear_feet", path),
        cost_breakdown=cost_breakdown,
    )


def _load_per_label(entries: list[object], path: Path) -> list[LabelMetrics]:
    """Build the ``per_label`` list from a report's raw entries."""
    labels: list[LabelMetrics] = []
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise SystemExit(f"{path}: 'per_label[{idx}]' is not a JSON object")
        item: dict[str, object] = {str(k): v for k, v in entry.items()}
        cost_breakdown = _load_cost_breakdown(item)
        labels.append(
            LabelMetrics(
                text=_as_str(item, "text", path),
                char_count=_as_int(item, "char_count", path),
                horizontal_scale=_as_float(item, "horizontal_scale", path),
                ink_area_sq_in=_as_float(item, "ink_area_sq_in", path),
                cost_breakdown=cost_breakdown,
            )
        )
    return labels


def _load_label_sizes(
    report: dict[str, object],
    job: dict[str, object],
    global_metrics: GlobalMetrics,
    path: Path,
) -> Counter[LabelSize]:
    """Return the report's printed-label counts keyed by label size.

    New reports carry a ``label_sizes`` array. Reports written before that
    field existed are derived from the job's single design size instead.
    """
    raw = report.get("label_sizes")
    if raw is None:
        return Counter(
            {
                (
                    _as_float(job, "label_width_in", path),
                    _as_float(job, "label_height_in", path),
                ): global_metrics.total_output_labels
            }
        )
    if not isinstance(raw, list):
        raise SystemExit(f"{path}: 'label_sizes' is not a JSON array")
    sizes: Counter[LabelSize] = Counter()
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise SystemExit(f"{path}: 'label_sizes[{idx}]' is not a JSON object")
        item: dict[str, object] = {str(k): v for k, v in entry.items()}
        size: LabelSize = (
            _as_float(item, "width_in", path),
            _as_float(item, "height_in", path),
        )
        sizes[size] += _as_int(item, "labels", path)
    return sizes


def load_report(path: Path) -> JobReport:
    """Parse a single ``*_report.json`` file into a :class:`JobReport`."""
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SystemExit(f"{path}: top level is not a JSON object")
    report: dict[str, object] = {str(k): v for k, v in raw.items()}

    job = _as_section(report, "job", path)
    global_metrics = _load_global(_as_section(report, "global", path), path)

    per_label_raw = _field(report, "per_label", path)
    if not isinstance(per_label_raw, list):
        raise SystemExit(f"{path}: 'per_label' is not a JSON array")
    per_label = _load_per_label(per_label_raw, path)

    return JobReport(
        path=path,
        input_file=_as_str(job, "input_file", path),
        page_width_in=_as_float(job, "page_width_in", path),
        copies_per_label=_as_int(job, "copies_per_label", path),
        global_metrics=global_metrics,
        label_sizes=_load_label_sizes(report, job, global_metrics, path),
        per_label=per_label,
    )


def is_consolidated_report(path: Path) -> bool:
    """Whether ``path`` is a consolidated report written by this script.

    Consolidated reports carry ``job.report_type == "consolidated"``; skipping
    them keeps re-runs idempotent even when the output is named ``*_report``.
    """
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(raw, dict):
        return False
    job: object = raw.get("job")
    return isinstance(job, dict) and job.get("report_type") == "consolidated"


def load_reports(directory: Path, skip: frozenset[Path]) -> list[JobReport]:
    """Parse every ``*_report.json`` in ``directory``, sorted by name.

    ``skip`` holds already-resolved output paths, and files carrying this
    script's consolidated marker are ignored, so its own reports are never
    folded into a later run.
    """
    paths = [
        p
        for p in sorted(directory.glob("*_report.json"))
        if p not in skip and not is_consolidated_report(p)
    ]
    if not paths:
        raise SystemExit(f"No *_report.json files found in {directory}")
    return [load_report(path) for path in paths]


# --- Consolidation -------------------------------------------------------------


def consolidate(
    reports: list[JobReport], pricing_config: PricingConfig | None = None
) -> ConsolidatedMetrics:
    """Sum the per-job metrics into a single :class:`ConsolidatedMetrics`."""
    total_substrate = sum(r.global_metrics.total_substrate_sq_in for r in reports)
    total_material = sum(r.global_metrics.total_label_material_sq_in for r in reports)
    total_ink = sum(r.global_metrics.total_ink_area_sq_in for r in reports)

    # Compute cost breakdown if pricing_config provided
    global_cost_breakdown = None
    if pricing_config is not None:
        total_ink_cost = 0.0
        total_substrate_cost = 0.0
        total_printer_hours = 0.0
        total_printer_cost = 0.0
        total_labor_hours = 0.0
        total_labor_cost = 0.0

        for report in reports:
            gm = report.global_metrics
            if gm.cost_breakdown:
                total_ink_cost += gm.cost_breakdown.ink_cost
                total_substrate_cost += gm.cost_breakdown.substrate_cost
                total_printer_hours += gm.cost_breakdown.printer_hours
                total_printer_cost += gm.cost_breakdown.printer_cost
                total_labor_hours += gm.cost_breakdown.labor_hours
                total_labor_cost += gm.cost_breakdown.labor_cost

        global_cost_total = (
            total_ink_cost
            + total_substrate_cost
            + total_printer_cost
            + total_labor_cost
        )
        global_unit_price = global_cost_total * (
            1.0 + pricing_config.markup_percent / 100.0
        )
        global_cost_breakdown = CostBreakdown(
            ink_cost=total_ink_cost,
            substrate_cost=total_substrate_cost,
            printer_hours=total_printer_hours,
            printer_cost=total_printer_cost,
            labor_hours=total_labor_hours,
            labor_cost=total_labor_cost,
            total_cost=global_cost_total,
            unit_price=global_unit_price,
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
        cost_breakdown=global_cost_breakdown,
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
    total_costs: dict[str, float] = {}
    ink_costs: dict[str, float] = {}
    substrate_costs: dict[str, float] = {}
    printer_hours: dict[str, float] = {}
    printer_costs: dict[str, float] = {}
    labor_hours: dict[str, float] = {}
    labor_costs: dict[str, float] = {}

    for report in reports:
        for label in report.per_label:
            jobs_seen[label.text] += 1
            instances[label.text] += report.copies_per_label
            ink_areas[label.text] = ink_areas.get(label.text, 0.0) + (
                label.ink_area_sq_in * report.copies_per_label
            )
            if label.cost_breakdown:
                cb = label.cost_breakdown
                total_costs[label.text] = total_costs.get(label.text, 0.0) + (
                    cb.total_cost * report.copies_per_label
                )
                ink_costs[label.text] = ink_costs.get(label.text, 0.0) + (
                    cb.ink_cost * report.copies_per_label
                )
                substrate_costs[label.text] = substrate_costs.get(label.text, 0.0) + (
                    cb.substrate_cost * report.copies_per_label
                )
                printer_hours[label.text] = printer_hours.get(label.text, 0.0) + (
                    cb.printer_hours * report.copies_per_label
                )
                printer_costs[label.text] = printer_costs.get(label.text, 0.0) + (
                    cb.printer_cost * report.copies_per_label
                )
                labor_hours[label.text] = labor_hours.get(label.text, 0.0) + (
                    cb.labor_hours * report.copies_per_label
                )
                labor_costs[label.text] = labor_costs.get(label.text, 0.0) + (
                    cb.labor_cost * report.copies_per_label
                )

    labels_list = []
    for text in sorted(ink_areas):
        # Build cost breakdown if costs were accumulated
        cost_breakdown = None
        if text in total_costs:
            unit_price = (
                total_costs[text] * (1.0 + pricing_config.markup_percent / 100.0)
                if pricing_config
                else total_costs[text]
            )
            cost_breakdown = CostBreakdown(
                ink_cost=ink_costs.get(text, 0.0),
                substrate_cost=substrate_costs.get(text, 0.0),
                printer_hours=printer_hours.get(text, 0.0),
                printer_cost=printer_costs.get(text, 0.0),
                labor_hours=labor_hours.get(text, 0.0),
                labor_cost=labor_costs.get(text, 0.0),
                total_cost=total_costs[text],
                unit_price=unit_price,
            )

        labels_list.append(
            ConsolidatedLabel(
                text=text,
                jobs=jobs_seen[text],
                instances=instances[text],
                char_count=len(text) * instances[text],
                ink_area_sq_in=ink_areas[text],
                cost_breakdown=cost_breakdown,
            )
        )
    return labels_list


def consolidate_labels_by_size(
    reports: list[JobReport], pricing_config: PricingConfig | None = None
) -> list[ConsolidatedLabelBySize]:
    """Consolidate label metrics grouped by label text and size.

    Each unique (label_text, label_size) combination is tracked separately,
    allowing per-tag-by-size reporting. Since all labels in a job share the
    same size, we can determine the size for each label from its report.
    """
    # Track metrics by (text, size) tuple
    # Store: (copies_per_label, ink_area_per_instance, scale, cost_breakdown)
    label_by_size_key: dict[str, list[tuple[int, float, float, CostBreakdown | None]]] = {}

    for report in reports:
        # All labels in this report share the same size
        label_size = list(report.label_sizes.keys())[0] if report.label_sizes else None
        if label_size is None:
            continue

        for label in report.per_label:
            key_str = f"{label.text}::{label_size[0]}x{label_size[1]}"
            if key_str not in label_by_size_key:
                label_by_size_key[key_str] = []
            label_by_size_key[key_str].append(
                (report.copies_per_label, label.ink_area_sq_in, label.horizontal_scale, label.cost_breakdown)
            )

    # Build result list
    result: list[ConsolidatedLabelBySize] = []
    for key_str in sorted(label_by_size_key.keys()):
        # Parse the key to extract text and size
        text, size_str = key_str.split("::")
        width_str, height_str = size_str.split("x")
        label_size = (float(width_str), float(height_str))
        
        entries = label_by_size_key[key_str]
        total_instances = sum(entry[0] for entry in entries)
        total_char_count = len(text) * total_instances
        total_ink_area = sum(entry[0] * entry[1] for entry in entries)
        
        # Compute average scale (weighted by copies)
        total_scale_weighted = sum(entry[0] * entry[2] for entry in entries)
        avg_scale = total_scale_weighted / total_instances if total_instances > 0 else 1.0
        
        # Calculate linear feet from label height
        linear_feet = label_size[1] * total_instances / 12.0

        # Aggregate cost breakdown
        cost_breakdown = None
        total_ink_cost = 0.0
        total_substrate_cost = 0.0
        total_printer_hours = 0.0
        total_printer_cost = 0.0
        total_labor_hours = 0.0
        total_labor_cost = 0.0
        total_cost = 0.0

        for copies, _, _, cb in entries:
            if cb:
                total_ink_cost += cb.ink_cost * copies
                total_substrate_cost += cb.substrate_cost * copies
                total_printer_hours += cb.printer_hours * copies
                total_printer_cost += cb.printer_cost * copies
                total_labor_hours += cb.labor_hours * copies
                total_labor_cost += cb.labor_cost * copies
                total_cost += cb.total_cost * copies

        if total_cost > 0:
            unit_price = (
                total_cost * (1.0 + pricing_config.markup_percent / 100.0)
                if pricing_config
                else total_cost
            )
            cost_breakdown = CostBreakdown(
                ink_cost=total_ink_cost,
                substrate_cost=total_substrate_cost,
                printer_hours=total_printer_hours,
                printer_cost=total_printer_cost,
                labor_hours=total_labor_hours,
                labor_cost=total_labor_cost,
                total_cost=total_cost,
                unit_price=unit_price,
            )

        result.append(
            ConsolidatedLabelBySize(
                text=text,
                label_size=label_size,
                instances=total_instances,
                char_count=total_char_count,
                ink_area_sq_in=total_ink_area,
                horizontal_scale=avg_scale,
                linear_feet=linear_feet,
                cost_breakdown=cost_breakdown,
            )
        )

    return result


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

    A job prints a single design size, so its full substrate/label/ink areas
    land on that size; should a job ever mix sizes, its areas are split by
    label-count share.
    """
    totals: dict[LabelSize, list[float]] = {}
    cost_totals: dict[LabelSize, dict[str, float]] = {}

    for report in reports:
        gm = report.global_metrics
        total_labels = sum(report.label_sizes.values()) or 1
        for size, count in report.label_sizes.items():
            share = count / total_labels
            bucket = totals.setdefault(size, [0.0, 0.0, 0.0])
            bucket[0] += gm.total_substrate_sq_in * share
            bucket[1] += gm.total_label_material_sq_in * share
            bucket[2] += gm.total_ink_area_sq_in * share

            # Accumulate cost data
            if gm.cost_breakdown:
                cb = gm.cost_breakdown
                if size not in cost_totals:
                    cost_totals[size] = {
                        "ink_cost": 0.0,
                        "substrate_cost": 0.0,
                        "printer_hours": 0.0,
                        "printer_cost": 0.0,
                        "labor_hours": 0.0,
                        "labor_cost": 0.0,
                        "total_cost": 0.0,
                    }
                cost_totals[size]["ink_cost"] += cb.ink_cost * share
                cost_totals[size]["substrate_cost"] += cb.substrate_cost * share
                cost_totals[size]["printer_hours"] += cb.printer_hours * share
                cost_totals[size]["printer_cost"] += cb.printer_cost * share
                cost_totals[size]["labor_hours"] += cb.labor_hours * share
                cost_totals[size]["labor_cost"] += cb.labor_cost * share
                cost_totals[size]["total_cost"] += cb.total_cost * share

    result = {}
    for size, (substrate, material, ink) in totals.items():
        cost_breakdown = None
        if size in cost_totals and cost_totals[size]["total_cost"] > 0:
            ct = cost_totals[size]
            unit_price = (
                ct["total_cost"] * (1.0 + pricing_config.markup_percent / 100.0)
                if pricing_config
                else ct["total_cost"]
            )
            cost_breakdown = CostBreakdown(
                ink_cost=ct["ink_cost"],
                substrate_cost=ct["substrate_cost"],
                printer_hours=ct["printer_hours"],
                printer_cost=ct["printer_cost"],
                labor_hours=ct["labor_hours"],
                labor_cost=ct["labor_cost"],
                total_cost=ct["total_cost"],
                unit_price=unit_price,
            )

        result[size] = SizeAreas(
            substrate_sq_in=substrate,
            label_material_sq_in=material,
            ink_area_sq_in=ink,
            cost_breakdown=cost_breakdown,
        )
    return result


def roll_widths_in(reports: list[JobReport]) -> list[float]:
    """Return the distinct roll widths (inches) used across ``reports``, sorted."""
    return sorted({report.page_width_in for report in reports})


def display_input_file(input_file: str, directory: Path) -> str:
    """Return ``input_file`` relative to ``directory`` when it lies inside it.

    The per-job reports store the input path as it was passed to
    ``vinyl_label_prep.py``; for the job-breakdown table the source directory
    prefix is noise, so it is stripped. Paths outside ``directory`` (or that
    otherwise cannot be made relative) are returned unchanged.
    """
    try:
        return str(Path(input_file).resolve().relative_to(directory.resolve()))
    except ValueError:
        return input_file


# --- Report generation ---------------------------------------------------------


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
    report = {
        "job": {
            "report_type": "consolidated",
            "generated": timestamp,
            "source_directory": str(directory),
            "total_jobs": metrics.total_jobs,
            "report_files": [str(r.path) for r in reports],
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
                "input_file": r.input_file,
                "report_file": str(r.path),
                "page_width_in": r.page_width_in,
                "copies_per_label": r.copies_per_label,
                "global": with_sq_ft(asdict(r.global_metrics)),
                "label_sizes": label_sizes_to_json(r.label_sizes),
            }
            for r in reports
        ],
        "per_label": [with_sq_ft(asdict(label)) for label in labels],
    }
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def write_consolidated_report(
    metrics: ConsolidatedMetrics,
    sizes: Counter[LabelSize],
    size_areas: dict[LabelSize, SizeAreas],
    labels: list[ConsolidatedLabel],
    reports: list[JobReport],
    out_path: Path,
    directory: Path,
) -> Path:
    """Write a human-readable consolidated report to ``out_path``.

    Also writes a machine-readable JSON sibling report next to it (the
    ``.txt`` suffix, if any, is replaced with ``.json``). Returns the path of
    the JSON report.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    roll_desc = "/".join(f"{width:g}in" for width in roll_widths_in(reports))

    lines: list[str] = []
    rule = "=" * 80
    subrule = "-" * 80
    lines.append(rule)
    lines.append("VINYL LABEL PRINTING REPORT - CONSOLIDATED".center(80))
    lines.append(rule)
    lines.append(f"Generated:         {timestamp}")
    lines.append(f"Source Directory:  {directory}")
    lines.append(f"Jobs Consolidated: {metrics.total_jobs}")
    lines.append("")
    lines.append(subrule)
    lines.append("GLOBAL PRINTING METRICS")
    lines.append(subrule)
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
    lines.append(subrule)
    lines.append("INK USAGE METRICS")
    lines.append(subrule)
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

    # Cost breakdown section if available
    if metrics.cost_breakdown is not None:
        cb = metrics.cost_breakdown
        lines.append(subrule)
        lines.append("COST BREAKDOWN")
        lines.append(subrule)
        lines.append(f"Ink Cost:                  ${cb.ink_cost:>10.2f}")
        lines.append(f"Substrate Cost:            ${cb.substrate_cost:>10.2f}")
        lines.append(f"Printer Hours:             {cb.printer_hours:>10.2f} hrs")
        lines.append(f"Printer Cost:              ${cb.printer_cost:>10.2f}")
        lines.append(f"Labor Hours:               {cb.labor_hours:>10.2f} hrs")
        lines.append(f"Labor Cost:                ${cb.labor_cost:>10.2f}")
        lines.append(f"Total Cost:                ${cb.total_cost:>10.2f}")
        lines.append(f"Total Price:               ${cb.unit_price:>10.2f}")
        lines.append("")

    lines.append(subrule)
    lines.append("LABEL SIZE BREAKDOWN")
    lines.append(subrule)
    jobs_per_size: Counter[LabelSize] = Counter()
    for report in reports:
        jobs_per_size.update(report.label_sizes.keys())

    # Check if any size_areas has cost_breakdown to decide on table format
    has_costs = any(sa.cost_breakdown is not None for sa in size_areas.values())

    if has_costs:
        lines.append(
            f"{'Label Size (WxH)':<16} | {'Jobs':>4} | {'Labels':>6} | "
            f"{'Substrate (sq ft)':>17} | {'Label Area (sq ft)':>18} | {'Ink (sq ft)':>11} | "
            f"{'Substrate Cost':>14} | {'Ink Cost':>10} | {'Printer Cost':>12} | "
            f"{'Labor Cost':>10} | {'Total Cost':>10}"
        )
        lines.append(
            f"{'-' * 16}-+-{'-' * 4}-+-{'-' * 6}-+-{'-' * 17}-+-{'-' * 18}-+-{'-' * 11}-+-{'-' * 14}-+-{'-' * 10}-+-{'-' * 12}-+-{'-' * 10}-+-{'-' * 10}"
        )
        for size, count in sorted(sizes.items()):
            area = size_areas[size]
            cost_str = ""
            if area.cost_breakdown:
                cb = area.cost_breakdown
                cost_str = (
                    f" | ${cb.substrate_cost:>12.2f} | ${cb.ink_cost:>8.2f} | "
                    f"${cb.printer_cost:>10.2f} | ${cb.labor_cost:>8.2f} | "
                    f"${cb.total_cost:>8.2f}"
                )
            else:
                cost_str = (
                    " |              |          |            |          |         "
                )
            lines.append(
                f"{format_label_size(size):<16} | {jobs_per_size[size]:>4d} | {count:>6d} | "
                f"{sq_ft(area.substrate_sq_in):>17.2f} | "
                f"{sq_ft(area.label_material_sq_in):>18.2f} | "
                f"{sq_ft(area.ink_area_sq_in):>11.2f}{cost_str}"
            )
        lines.append(
            f"{'=' * 16}=+={'=' * 4}=+={'=' * 6}=+={'=' * 17}=+={'=' * 18}=+={'=' * 11}=+={'=' * 14}=+={'=' * 10}=+={'=' * 12}=+={'=' * 10}=+={'=' * 10}"
        )
        total_cost_str = ""
        if metrics.cost_breakdown:
            cb = metrics.cost_breakdown
            total_cost_str = (
                f" | ${cb.substrate_cost:>12.2f} | ${cb.ink_cost:>8.2f} | "
                f"${cb.printer_cost:>10.2f} | ${cb.labor_cost:>8.2f} | "
                f"${cb.total_cost:>8.2f}"
            )
        else:
            total_cost_str = (
                " |              |          |            |          |         "
            )
        lines.append(
            f"{'Total':<16} | {metrics.total_jobs:>4d} | {metrics.total_output_labels:>6d} | "
            f"{sq_ft(metrics.total_substrate_sq_in):>17.2f} | "
            f"{sq_ft(metrics.total_label_material_sq_in):>18.2f} | "
            f"{sq_ft(metrics.total_ink_area_sq_in):>11.2f}{total_cost_str}"
        )
    else:
        lines.append(
            f"{'Label Size (WxH)':<16} | {'Jobs':>4} | {'Labels':>6} | "
            f"{'Substrate (sq ft)':>17} | {'Label Area (sq ft)':>18} | {'Ink (sq ft)':>11}"
        )
        lines.append(
            f"{'-' * 16}-+-{'-' * 4}-+-{'-' * 6}-+-{'-' * 17}-+-{'-' * 18}-+-{'-' * 11}"
        )
        for size, count in sorted(sizes.items()):
            area = size_areas[size]
            lines.append(
                f"{format_label_size(size):<16} | {jobs_per_size[size]:>4d} | {count:>6d} | "
                f"{sq_ft(area.substrate_sq_in):>17.2f} | "
                f"{sq_ft(area.label_material_sq_in):>18.2f} | "
                f"{sq_ft(area.ink_area_sq_in):>11.2f}"
            )
        lines.append(
            f"{'=' * 16}=+={'=' * 4}=+={'=' * 6}=+={'=' * 17}=+={'=' * 18}=+={'=' * 11}"
        )
        lines.append(
            f"{'Total':<16} | {metrics.total_jobs:>4d} | {metrics.total_output_labels:>6d} | "
            f"{sq_ft(metrics.total_substrate_sq_in):>17.2f} | "
            f"{sq_ft(metrics.total_label_material_sq_in):>18.2f} | "
            f"{sq_ft(metrics.total_ink_area_sq_in):>11.2f}"
        )
    lines.append("")
    lines.append(subrule)
    lines.append("JOB BREAKDOWN")
    lines.append(subrule)
    lines.append(
        f"{'Input File':<38} | {'Roll':>5} | {'Labels':>6} | {'Lin Ft':>9} | "
        f"{'Ink (sq in)':>11} | {'Ink (sq ft)':>11}"
    )
    lines.append(
        f"{'-' * 38}-+-{'-' * 5}-+-{'-' * 6}-+-{'-' * 9}-+-{'-' * 11}-+-{'-' * 11}"
    )
    for report in reports:
        gm = report.global_metrics
        input_file = display_input_file(report.input_file, directory)
        lines.append(
            f"{input_file:<38} | {report.page_width_in:>5.0f} | "
            f"{gm.total_output_labels:>6d} | {gm.linear_feet:>9.2f} | "
            f"{gm.total_ink_area_sq_in:>11.2f} | "
            f"{sq_ft(gm.total_ink_area_sq_in):>11.2f}"
        )
    lines.append("")
    lines.append(subrule)
    lines.append("PER-TAG BREAKDOWN")
    lines.append(subrule)
    lines.append(
        f"{'Label Code':<16} | {'Jobs':>4} | {'Copies':>5} | {'Chars':>5} | "
        f"{'Ink Area (sq in)':>16} | {'Ink Area (sq ft)':>16}"
    )
    lines.append(
        f"{'-' * 16}-+-{'-' * 4}-+-{'-' * 5}-+-{'-' * 5}-+-{'-' * 18}-+-{'-' * 18}"
    )
    for label in labels:
        lines.append(
            f"{label.text:<16} | {label.jobs:>4d} | {label.instances:>5d} | "
            f"{label.char_count:>5d} | {label.ink_area_sq_in:>16.4f} | "
            f"{sq_ft(label.ink_area_sq_in):>16.4f}"
        )
    lines.append(rule)
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")

    json_path = out_path.with_suffix(".json")
    write_consolidated_report_json(
        metrics, sizes, size_areas, labels, reports, json_path, directory, timestamp
    )
    return json_path


def write_size_breakdown_csv(
    sizes: Counter[LabelSize],
    size_areas: dict[LabelSize, SizeAreas],
    output_path: Path,
    include_costs: bool = True,
) -> None:
    """Write size breakdown to a CSV file."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        if include_costs:
            fieldnames = [
                "Label Size (WxH)",
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
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            # Accumulators for TOTAL row
            total_labels = 0
            total_substrate_sqft = 0.0
            total_label_area_sqft = 0.0
            total_ink_sqft = 0.0
            total_substrate_cost = 0.0
            total_ink_cost = 0.0
            total_printer_hours = 0.0
            total_printer_cost = 0.0
            total_labor_hours = 0.0
            total_labor_cost = 0.0
            total_cost = 0.0
            total_price = 0.0
            
            for size, count in sorted(sizes.items()):
                area = size_areas[size]
                substrate_sqft = sq_ft(area.substrate_sq_in)
                label_area_sqft = sq_ft(area.label_material_sq_in)
                ink_sqft = sq_ft(area.ink_area_sq_in)
                row = {
                    "Label Size (WxH)": format_label_size(size),
                    "Labels": count,
                    "Substrate (sq ft)": f"{substrate_sqft:.2f}",
                    "Label Area (sq ft)": f"{label_area_sqft:.2f}",
                    "Ink (sq ft)": f"{ink_sqft:.2f}",
                }
                # Accumulate numeric values
                total_labels += count
                total_substrate_sqft += substrate_sqft
                total_label_area_sqft += label_area_sqft
                total_ink_sqft += ink_sqft
                
                if area.cost_breakdown:
                    cb = area.cost_breakdown
                    row.update(
                        {
                            "Substrate Cost ($)": f"{cb.substrate_cost:.2f}",
                            "Ink Cost ($)": f"{cb.ink_cost:.2f}",
                            "Printer Hours": f"{cb.printer_hours:.2f}",
                            "Printer Cost ($)": f"{cb.printer_cost:.2f}",
                            "Labor Hours": f"{cb.labor_hours:.2f}",
                            "Labor Cost ($)": f"{cb.labor_cost:.2f}",
                            "Total Cost ($)": f"{cb.total_cost:.2f}",
                            "Price ($)": f"{cb.unit_price:.2f}",
                            "Unit Price ($)": f"{cb.unit_price / count:.2f}" if count > 0 else "",
                        }
                    )
                    # Accumulate costs
                    total_substrate_cost += cb.substrate_cost
                    total_ink_cost += cb.ink_cost
                    total_printer_hours += cb.printer_hours
                    total_printer_cost += cb.printer_cost
                    total_labor_hours += cb.labor_hours
                    total_labor_cost += cb.labor_cost
                    total_cost += cb.total_cost
                    total_price += cb.unit_price
                else:
                    row.update(
                        {
                            "Substrate Cost ($)": "",
                            "Ink Cost ($)": "",
                            "Printer Hours": "",
                            "Printer Cost ($)": "",
                            "Labor Hours": "",
                            "Labor Cost ($)": "",
                            "Total Cost ($)": "",
                            "Price ($)": "",
                            "Unit Price ($)": "",
                        }
                    )
                writer.writerow(row)
            
            # Write TOTAL row
            total_row = {
                "Label Size (WxH)": "TOTAL",
                "Labels": total_labels,
                "Substrate (sq ft)": f"{total_substrate_sqft:.2f}",
                "Label Area (sq ft)": f"{total_label_area_sqft:.2f}",
                "Ink (sq ft)": f"{total_ink_sqft:.2f}",
                "Substrate Cost ($)": f"{total_substrate_cost:.2f}",
                "Ink Cost ($)": f"{total_ink_cost:.2f}",
                "Printer Hours": f"{total_printer_hours:.2f}",
                "Printer Cost ($)": f"{total_printer_cost:.2f}",
                "Labor Hours": f"{total_labor_hours:.2f}",
                "Labor Cost ($)": f"{total_labor_cost:.2f}",
                "Total Cost ($)": f"{total_cost:.2f}",
                "Price ($)": f"{total_price:.2f}",
                "Unit Price ($)": "",  # Non-summable
            }
            writer.writerow(total_row)
        else:
            # Customer CSV: price and unit price per label
            fieldnames = ["Label Size (WxH)", "Labels", "Price ($)", "Unit Price ($)"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            # Accumulators for TOTAL row
            total_labels = 0
            total_price = 0.0
            
            for size, count in sorted(sizes.items()):
                area = size_areas[size]
                row = {
                    "Label Size (WxH)": format_label_size(size),
                    "Labels": count,
                }
                total_labels += count
                if area.cost_breakdown:
                    row["Price ($)"] = f"{area.cost_breakdown.unit_price:.2f}"
                    row["Unit Price ($)"] = f"{area.cost_breakdown.unit_price / count:.2f}" if count > 0 else ""
                    total_price += area.cost_breakdown.unit_price
                else:
                    row["Price ($)"] = ""
                    row["Unit Price ($)"] = ""
                writer.writerow(row)
            
            # Write TOTAL row
            total_row = {
                "Label Size (WxH)": "TOTAL",
                "Labels": total_labels,
                "Price ($)": f"{total_price:.2f}",
                "Unit Price ($)": "",  # Non-summable
            }
            writer.writerow(total_row)


def write_job_breakdown_csv(
    reports: list[JobReport],
    directory: Path,
    output_path: Path,
    include_costs: bool = True,
) -> None:
    """Write job breakdown to a CSV file."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        if include_costs:
            fieldnames = [
                "Input File",
                "Label Size (WxH)",
                "Labels",
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
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            # Accumulators for TOTAL row
            total_labels = 0
            total_linear_feet = 0.0
            total_ink_sq_in = 0.0
            total_ink_cost = 0.0
            total_substrate_cost = 0.0
            total_printer_hours = 0.0
            total_printer_cost = 0.0
            total_labor_hours = 0.0
            total_labor_cost = 0.0
            total_cost = 0.0
            total_price = 0.0
            
            for report in reports:
                gm = report.global_metrics
                input_file = display_input_file(report.input_file, directory)
                # Format label sizes as "WxH" (e.g., "10x4, 12x4")
                label_size_str = ", ".join(
                    format_label_size(size) for size in sorted(report.label_sizes.keys())
                ) if report.label_sizes else ""
                row = {
                    "Input File": input_file,
                    "Label Size (WxH)": label_size_str,
                    "Labels": gm.total_output_labels,
                    "Linear Feet": f"{gm.linear_feet:.2f}",
                    "Ink (sq in)": f"{gm.total_ink_area_sq_in:.2f}",
                    "Ink (sq ft)": f"{sq_ft(gm.total_ink_area_sq_in):.2f}",
                }
                # Accumulate numeric values
                total_labels += gm.total_output_labels
                total_linear_feet += gm.linear_feet
                total_ink_sq_in += gm.total_ink_area_sq_in
                
                if gm.cost_breakdown:
                    cb = gm.cost_breakdown
                    row.update(
                        {
                            "Ink Cost ($)": f"{cb.ink_cost:.2f}",
                            "Substrate Cost ($)": f"{cb.substrate_cost:.2f}",
                            "Printer Hours": f"{cb.printer_hours:.2f}",
                            "Printer Cost ($)": f"{cb.printer_cost:.2f}",
                            "Labor Hours": f"{cb.labor_hours:.2f}",
                            "Labor Cost ($)": f"{cb.labor_cost:.2f}",
                            "Total Cost ($)": f"{cb.total_cost:.2f}",
                            "Price ($)": f"{cb.unit_price:.2f}",
                            "Unit Price ($)": f"{cb.unit_price / gm.total_output_labels:.2f}" if gm.total_output_labels > 0 else "",
                        }
                    )
                    # Accumulate costs
                    total_ink_cost += cb.ink_cost
                    total_substrate_cost += cb.substrate_cost
                    total_printer_hours += cb.printer_hours
                    total_printer_cost += cb.printer_cost
                    total_labor_hours += cb.labor_hours
                    total_labor_cost += cb.labor_cost
                    total_cost += cb.total_cost
                    total_price += cb.unit_price
                else:
                    row.update(
                        {
                            "Ink Cost ($)": "",
                            "Substrate Cost ($)": "",
                            "Printer Hours": "",
                            "Printer Cost ($)": "",
                            "Labor Hours": "",
                            "Labor Cost ($)": "",
                            "Total Cost ($)": "",
                            "Price ($)": "",
                            "Unit Price ($)": "",
                        }
                    )
                writer.writerow(row)
            
            # Write TOTAL row
            total_row = {
                "Input File": "TOTAL",
                "Label Size (WxH)": "",  # Non-summable
                "Labels": total_labels,
                "Linear Feet": f"{total_linear_feet:.2f}",
                "Ink (sq in)": f"{total_ink_sq_in:.2f}",
                "Ink (sq ft)": f"{sq_ft(total_ink_sq_in):.2f}",
                "Ink Cost ($)": f"{total_ink_cost:.2f}",
                "Substrate Cost ($)": f"{total_substrate_cost:.2f}",
                "Printer Hours": f"{total_printer_hours:.2f}",
                "Printer Cost ($)": f"{total_printer_cost:.2f}",
                "Labor Hours": f"{total_labor_hours:.2f}",
                "Labor Cost ($)": f"{total_labor_cost:.2f}",
                "Total Cost ($)": f"{total_cost:.2f}",
                "Price ($)": f"{total_price:.2f}",
                "Unit Price ($)": "",  # Non-summable
            }
            writer.writerow(total_row)
        else:
            # Customer CSV: price and unit price per label
            fieldnames = ["Input File", "Labels", "Price ($)", "Unit Price ($)"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            # Accumulators for TOTAL row
            total_labels = 0
            total_price = 0.0
            
            for report in reports:
                gm = report.global_metrics
                input_file = display_input_file(report.input_file, directory)
                row = {
                    "Input File": input_file,
                    "Labels": gm.total_output_labels,
                }
                total_labels += gm.total_output_labels
                if gm.cost_breakdown:
                    row["Price ($)"] = f"{gm.cost_breakdown.unit_price:.2f}"
                    row["Unit Price ($)"] = f"{gm.cost_breakdown.unit_price / gm.total_output_labels:.2f}" if gm.total_output_labels > 0 else ""
                    total_price += gm.cost_breakdown.unit_price
                else:
                    row["Price ($)"] = ""
                    row["Unit Price ($)"] = ""
                writer.writerow(row)
            
            # Write TOTAL row
            total_row = {
                "Input File": "TOTAL",
                "Labels": total_labels,
                "Price ($)": f"{total_price:.2f}",
                "Unit Price ($)": "",  # Non-summable
            }
            writer.writerow(total_row)


def write_per_tag_breakdown_csv(
    labels: list[ConsolidatedLabelBySize],
    output_path: Path,
    include_costs: bool = True,
    customer_report_config: dict[str, bool] | None = None,
) -> None:
    """Write per-tag breakdown to a CSV file.

    If include_costs is False, only customer-facing columns are written,
    controlled by customer_report_config. If include_costs is True, all
    internal columns are written regardless of configuration.
    """
    if not labels:
        return

    # Define all available columns
    all_columns = [
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
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        if include_costs:
            # Internal CSV: all columns
            fieldnames = all_columns
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            # Accumulators for TOTAL row
            total_copies = 0
            total_char_count = 0
            total_label_size_sq_in = 0.0
            total_label_area_sqft = 0.0
            total_linear_feet = 0.0
            total_ink_sq_in = 0.0
            total_ink_sqft = 0.0
            total_ink_cost = 0.0
            total_substrate_cost = 0.0
            total_printer_hours = 0.0
            total_printer_cost = 0.0
            total_labor_hours = 0.0
            total_labor_cost = 0.0
            total_cost = 0.0
            total_price = 0.0
            
            for label in labels:
                label_size_sq_in = label.label_size[0] * label.label_size[1]
                label_area_sqft = sq_ft(label_size_sq_in)
                ink_sqft = sq_ft(label.ink_area_sq_in)
                row = {
                    "Label Code": label.text,
                    "Copies": label.instances,
                    "Label Size (WxH)": f"{label.label_size[0]:g}x{label.label_size[1]:g}",
                    "Text Height (in)": "2.00",
                    "Char Count": label.char_count,
                    "Scale": f"{label.horizontal_scale:.4f}",
                    "Label Size (sq in)": f"{label_size_sq_in:.2f}",
                    "Label Area (sq ft)": f"{label_area_sqft:.4f}",
                    "Linear Feet": f"{label.linear_feet:.2f}",
                    "Ink (sq in)": f"{label.ink_area_sq_in:.2f}",
                    "Ink (sq ft)": f"{ink_sqft:.4f}",
                }
                # Accumulate numeric values
                total_copies += label.instances
                total_char_count += label.char_count
                total_label_size_sq_in += label_size_sq_in
                total_label_area_sqft += label_area_sqft
                total_linear_feet += label.linear_feet
                total_ink_sq_in += label.ink_area_sq_in
                total_ink_sqft += ink_sqft
                
                if label.cost_breakdown:
                    cb = label.cost_breakdown
                    row.update(
                        {
                            "Ink Cost ($)": f"{cb.ink_cost:.2f}",
                            "Substrate Cost ($)": f"{cb.substrate_cost:.2f}",
                            "Printer Hours": f"{cb.printer_hours:.4f}",
                            "Printer Cost ($)": f"{cb.printer_cost:.2f}",
                            "Labor Hours": f"{cb.labor_hours:.4f}",
                            "Labor Cost ($)": f"{cb.labor_cost:.2f}",
                            "Total Cost ($)": f"{cb.total_cost:.2f}",
                            "Price ($)": f"{cb.unit_price:.2f}",
                            "Unit Price ($)": f"{cb.unit_price / label.instances:.2f}" if label.instances > 0 else "",
                        }
                    )
                    # Accumulate costs
                    total_ink_cost += cb.ink_cost
                    total_substrate_cost += cb.substrate_cost
                    total_printer_hours += cb.printer_hours
                    total_printer_cost += cb.printer_cost
                    total_labor_hours += cb.labor_hours
                    total_labor_cost += cb.labor_cost
                    total_cost += cb.total_cost
                    total_price += cb.unit_price
                else:
                    row.update(
                        {
                            "Ink Cost ($)": "",
                            "Substrate Cost ($)": "",
                            "Printer Hours": "",
                            "Printer Cost ($)": "",
                            "Labor Hours": "",
                            "Labor Cost ($)": "",
                            "Total Cost ($)": "",
                            "Price ($)": "",
                            "Unit Price ($)": "",
                        }
                    )
                writer.writerow(row)
            
            # Write TOTAL row
            total_row = {
                "Label Code": "TOTAL",
                "Copies": total_copies,
                "Label Size (WxH)": "",  # Non-summable
                "Text Height (in)": "",  # Non-summable
                "Char Count": total_char_count,
                "Scale": "",  # Non-summable
                "Label Size (sq in)": f"{total_label_size_sq_in:.2f}",
                "Label Area (sq ft)": f"{total_label_area_sqft:.4f}",
                "Linear Feet": f"{total_linear_feet:.2f}",
                "Ink (sq in)": f"{total_ink_sq_in:.2f}",
                "Ink (sq ft)": f"{total_ink_sqft:.4f}",
                "Ink Cost ($)": f"{total_ink_cost:.2f}",
                "Substrate Cost ($)": f"{total_substrate_cost:.2f}",
                "Printer Hours": f"{total_printer_hours:.2f}",
                "Printer Cost ($)": f"{total_printer_cost:.2f}",
                "Labor Hours": f"{total_labor_hours:.2f}",
                "Labor Cost ($)": f"{total_labor_cost:.2f}",
                "Total Cost ($)": f"{total_cost:.2f}",
                "Price ($)": f"{total_price:.2f}",
                "Unit Price ($)": "",  # Non-summable
            }
            writer.writerow(total_row)
        else:
            # Customer CSV: use configuration to determine which columns to include
            # Always include label code and copies
            fieldnames = ["Label Code", "Copies"]

            # Map config keys to column names and extract configuration
            config_map = {
                "label_size_wxh": "Label Size (WxH)",
                "text_height_in": "Text Height (in)",
                "char_count": "Char Count",
                "scale": "Scale",
                "label_size_sq_in": "Label Size (sq in)",
                "label_area_sq_ft": "Label Area (sq ft)",
                "linear_feet": "Linear Feet",
                "ink_sq_in": "Ink (sq in)",
                "ink_sq_ft": "Ink (sq ft)",
                "ink_cost": "Ink Cost ($)",
                "substrate_cost": "Substrate Cost ($)",
                "printer_hours": "Printer Hours",
                "printer_cost": "Printer Cost ($)",
                "labor_hours": "Labor Hours",
                "labor_cost": "Labor Cost ($)",
                "total_cost": "Total Cost ($)",
                "price": "Price ($)",
                "unit_price": "Unit Price ($)",
            }

            # Build fieldnames based on customer_report_config
            if customer_report_config:
                for config_key, col_name in config_map.items():
                    if customer_report_config.get(config_key, False):
                        fieldnames.append(col_name)

            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            # Accumulators for TOTAL row (for all possible summable columns)
            total_copies = 0
            total_char_count = 0
            total_label_size_sq_in = 0.0
            total_label_area_sqft = 0.0
            total_linear_feet = 0.0
            total_ink_sq_in = 0.0
            total_ink_sqft = 0.0
            total_ink_cost = 0.0
            total_substrate_cost = 0.0
            total_printer_hours = 0.0
            total_printer_cost = 0.0
            total_labor_hours = 0.0
            total_labor_cost = 0.0
            total_cost = 0.0
            total_price = 0.0
            
            for label in labels:
                row = {
                    "Label Code": label.text,
                    "Copies": label.instances,
                }
                
                # Accumulate copies
                total_copies += label.instances

                # Populate optional fields based on configuration
                if customer_report_config:
                    if customer_report_config.get("label_size_wxh", False):
                        row["Label Size (WxH)"] = f"{label.label_size[0]:g}x{label.label_size[1]:g}"
                    if customer_report_config.get("text_height_in", False):
                        row["Text Height (in)"] = "2.00"
                    if customer_report_config.get("char_count", False):
                        row["Char Count"] = label.char_count
                        total_char_count += label.char_count
                    if customer_report_config.get("scale", False):
                        row["Scale"] = f"{label.horizontal_scale:.4f}"
                    if customer_report_config.get("label_size_sq_in", False):
                        label_size_sq_in = label.label_size[0] * label.label_size[1]
                        row["Label Size (sq in)"] = f"{label_size_sq_in:.2f}"
                        total_label_size_sq_in += label_size_sq_in
                    if customer_report_config.get("label_area_sq_ft", False):
                        label_area_sqft = sq_ft(label.label_size[0] * label.label_size[1])
                        row["Label Area (sq ft)"] = f"{label_area_sqft:.4f}"
                        total_label_area_sqft += label_area_sqft
                    if customer_report_config.get("linear_feet", False):
                        row["Linear Feet"] = f"{label.linear_feet:.2f}"
                        total_linear_feet += label.linear_feet
                    if customer_report_config.get("ink_sq_in", False):
                        row["Ink (sq in)"] = f"{label.ink_area_sq_in:.2f}"
                        total_ink_sq_in += label.ink_area_sq_in
                    if customer_report_config.get("ink_sq_ft", False):
                        ink_sqft = sq_ft(label.ink_area_sq_in)
                        row["Ink (sq ft)"] = f"{ink_sqft:.4f}"
                        total_ink_sqft += ink_sqft
                    if customer_report_config.get("ink_cost", False):
                        row["Ink Cost ($)"] = (
                            f"{label.cost_breakdown.ink_cost:.2f}"
                            if label.cost_breakdown
                            else ""
                        )
                        if label.cost_breakdown:
                            total_ink_cost += label.cost_breakdown.ink_cost
                    if customer_report_config.get("substrate_cost", False):
                        row["Substrate Cost ($)"] = (
                            f"{label.cost_breakdown.substrate_cost:.2f}"
                            if label.cost_breakdown
                            else ""
                        )
                        if label.cost_breakdown:
                            total_substrate_cost += label.cost_breakdown.substrate_cost
                    if customer_report_config.get("printer_hours", False):
                        row["Printer Hours"] = (
                            f"{label.cost_breakdown.printer_hours:.4f}"
                            if label.cost_breakdown
                            else ""
                        )
                        if label.cost_breakdown:
                            total_printer_hours += label.cost_breakdown.printer_hours
                    if customer_report_config.get("printer_cost", False):
                        row["Printer Cost ($)"] = (
                            f"{label.cost_breakdown.printer_cost:.2f}"
                            if label.cost_breakdown
                            else ""
                        )
                        if label.cost_breakdown:
                            total_printer_cost += label.cost_breakdown.printer_cost
                    if customer_report_config.get("labor_hours", False):
                        row["Labor Hours"] = (
                            f"{label.cost_breakdown.labor_hours:.4f}"
                            if label.cost_breakdown
                            else ""
                        )
                        if label.cost_breakdown:
                            total_labor_hours += label.cost_breakdown.labor_hours
                    if customer_report_config.get("labor_cost", False):
                        row["Labor Cost ($)"] = (
                            f"{label.cost_breakdown.labor_cost:.2f}"
                            if label.cost_breakdown
                            else ""
                        )
                        if label.cost_breakdown:
                            total_labor_cost += label.cost_breakdown.labor_cost
                    if customer_report_config.get("total_cost", False):
                        row["Total Cost ($)"] = (
                            f"{label.cost_breakdown.total_cost:.2f}"
                            if label.cost_breakdown
                            else ""
                        )
                        if label.cost_breakdown:
                            total_cost += label.cost_breakdown.total_cost
                    if customer_report_config.get("price", False):
                        row["Price ($)"] = (
                            f"{label.cost_breakdown.unit_price:.2f}"
                            if label.cost_breakdown
                            else ""
                        )
                        if label.cost_breakdown:
                            total_price += label.cost_breakdown.unit_price
                    if customer_report_config.get("unit_price", False):
                        row["Unit Price ($)"] = (
                            f"{label.cost_breakdown.unit_price / label.instances:.2f}"
                            if label.cost_breakdown and label.instances > 0
                            else ""
                        )

                writer.writerow(row)
            
            # Write TOTAL row
            total_row = {
                "Label Code": "TOTAL",
                "Copies": total_copies,
            }
            if customer_report_config:
                if customer_report_config.get("label_size_wxh", False):
                    total_row["Label Size (WxH)"] = ""  # Non-summable
                if customer_report_config.get("text_height_in", False):
                    total_row["Text Height (in)"] = ""  # Non-summable
                if customer_report_config.get("char_count", False):
                    total_row["Char Count"] = total_char_count
                if customer_report_config.get("scale", False):
                    total_row["Scale"] = ""  # Non-summable
                if customer_report_config.get("label_size_sq_in", False):
                    total_row["Label Size (sq in)"] = f"{total_label_size_sq_in:.2f}"
                if customer_report_config.get("label_area_sq_ft", False):
                    total_row["Label Area (sq ft)"] = f"{total_label_area_sqft:.4f}"
                if customer_report_config.get("linear_feet", False):
                    total_row["Linear Feet"] = f"{total_linear_feet:.2f}"
                if customer_report_config.get("ink_sq_in", False):
                    total_row["Ink (sq in)"] = f"{total_ink_sq_in:.2f}"
                if customer_report_config.get("ink_sq_ft", False):
                    total_row["Ink (sq ft)"] = f"{total_ink_sqft:.4f}"
                if customer_report_config.get("ink_cost", False):
                    total_row["Ink Cost ($)"] = f"{total_ink_cost:.2f}"
                if customer_report_config.get("substrate_cost", False):
                    total_row["Substrate Cost ($)"] = f"{total_substrate_cost:.2f}"
                if customer_report_config.get("printer_hours", False):
                    total_row["Printer Hours"] = f"{total_printer_hours:.2f}"
                if customer_report_config.get("printer_cost", False):
                    total_row["Printer Cost ($)"] = f"{total_printer_cost:.2f}"
                if customer_report_config.get("labor_hours", False):
                    total_row["Labor Hours"] = f"{total_labor_hours:.2f}"
                if customer_report_config.get("labor_cost", False):
                    total_row["Labor Cost ($)"] = f"{total_labor_cost:.2f}"
                if customer_report_config.get("total_cost", False):
                    total_row["Total Cost ($)"] = f"{total_cost:.2f}"
                if customer_report_config.get("price", False):
                    total_row["Price ($)"] = f"{total_price:.2f}"
                if customer_report_config.get("unit_price", False):
                    total_row["Unit Price ($)"] = ""  # Non-summable
            writer.writerow(total_row)


# --- CLI -----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """Parse CLI args, load every report in the directory, write the outputs."""
    parser = argparse.ArgumentParser(
        description=(
            "Consolidate the metrics of every *_report.json file in a directory "
            "into one report."
        ),
    )
    parser.add_argument(
        "directory",
        type=Path,
        help="Directory containing *_report.json files from vinyl_label_prep.py.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=(
            "Path for the consolidated text report "
            "(default: <directory>/vinyl_labels_combined.txt)."
        ),
    )

    pricing = parser.add_argument_group("pricing", "cost configuration and pricing")
    pricing.add_argument(
        "--pricing-config",
        type=Path,
        default=None,
        help="Path to JSON pricing config file (default: pricing-config.json in project root).",
    )
    pricing.add_argument(
        "--ink-cost",
        type=float,
        default=None,
        help="Ink cost per square foot (USD) (overrides config file).",
    )
    pricing.add_argument(
        "--substrate-cost",
        type=float,
        default=None,
        help="Substrate cost per square foot (USD) (overrides config file).",
    )
    pricing.add_argument(
        "--print-rate",
        type=float,
        default=None,
        help="Print rate in hours per square foot (overrides config file).",
    )
    pricing.add_argument(
        "--printer-cost",
        type=float,
        default=None,
        help="Printer run cost per hour (USD) (overrides config file).",
    )
    pricing.add_argument(
        "--labor-rate",
        type=float,
        default=None,
        help="Labor rate per hour (USD) (overrides config file).",
    )
    pricing.add_argument(
        "--labor-factor",
        type=float,
        default=None,
        help="Labor time multiplier relative to printer hours (overrides config file).",
    )
    pricing.add_argument(
        "--markup",
        type=float,
        default=None,
        help="Markup percentage on total cost (overrides config file).",
    )

    args = parser.parse_args(argv)

    directory: Path = args.directory
    if not directory.is_dir():
        raise SystemExit(f"Not a directory: {directory}")

    out_path: Path = args.output or directory / "vinyl_labels_combined.txt"
    skip = frozenset({out_path.resolve(), out_path.with_suffix(".json").resolve()})

    # Load pricing config
    pricing_config_path = None
    if args.pricing_config:
        pricing_config_path = args.pricing_config
    else:
        # Try to find pricing-config.json in the project root
        default_pricing = Path(__file__).parent.parent / "pricing-config.json"
        if default_pricing.exists():
            pricing_config_path = default_pricing

    # Build CLI overrides dict
    cli_overrides = {}
    if args.ink_cost is not None:
        cli_overrides["ink_cost"] = args.ink_cost
    if args.substrate_cost is not None:
        cli_overrides["substrate_cost"] = args.substrate_cost
    if args.print_rate is not None:
        cli_overrides["print_rate"] = args.print_rate
    if args.printer_cost is not None:
        cli_overrides["printer_cost"] = args.printer_cost
    if args.labor_rate is not None:
        cli_overrides["labor_rate"] = args.labor_rate
    if args.labor_factor is not None:
        cli_overrides["labor_factor"] = args.labor_factor
    if args.markup is not None:
        cli_overrides["markup"] = args.markup

    # Resolve pricing config
    pricing_config = resolve_pricing_config(pricing_config_path, cli_overrides)

    reports = load_reports(directory, skip)
    metrics = consolidate(reports, pricing_config)
    sizes = consolidate_label_sizes(reports)
    size_areas = consolidate_size_areas(reports, pricing_config)
    labels = consolidate_labels(reports, pricing_config)
    labels_by_size = consolidate_labels_by_size(reports, pricing_config)
    json_report_path = write_consolidated_report(
        metrics, sizes, size_areas, labels, reports, out_path, directory
    )

    # Write CSV reports
    write_size_breakdown_csv(
        sizes,
        size_areas,
        out_path.with_name(f"{out_path.stem}_size_breakdown.csv"),
        include_costs=True,
    )
    write_size_breakdown_csv(
        sizes,
        size_areas,
        out_path.with_name(f"{out_path.stem}_size_breakdown_customer.csv"),
        include_costs=False,
    )
    write_job_breakdown_csv(
        reports,
        directory,
        out_path.with_name(f"{out_path.stem}_job_breakdown.csv"),
        include_costs=True,
    )
    write_job_breakdown_csv(
        reports,
        directory,
        out_path.with_name(f"{out_path.stem}_job_breakdown_customer.csv"),
        include_costs=False,
    )
    write_per_tag_breakdown_csv(
        labels_by_size,
        out_path.with_name(f"{out_path.stem}_tag_breakdown.csv"),
        include_costs=True,
    )
    write_per_tag_breakdown_csv(
        labels_by_size,
        out_path.with_name(f"{out_path.stem}_tag_breakdown_customer.csv"),
        include_costs=False,
        customer_report_config=pricing_config.customer_report if pricing_config else None,
    )

    print(
        f"Consolidated {metrics.total_jobs} job report(s) "
        f"({metrics.total_output_labels} labels, "
        f"{metrics.linear_feet:.2f} linear ft, "
        f"{metrics.total_ink_area_sq_in:.2f} sq in ink)\n"
        f"Wrote consolidated report to {out_path}\n"
        f"Wrote consolidated JSON report to {json_report_path}"
    )


if __name__ == "__main__":
    main()
