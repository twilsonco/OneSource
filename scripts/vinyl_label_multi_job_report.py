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
import shutil
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

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
    PdfMetrics,
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
    text_height_in: float
    copies_per_label: int
    global_metrics: GlobalMetrics
    label_sizes: Counter[LabelSize]
    per_label: list[LabelMetrics]
    output_pdf_files: list[Path] | None = None  # Multi-PDF support
    per_pdf_metrics: list[PdfMetrics] | None = None  # Per-PDF metrics


# --- Output model --------------------------------------------------------------


@dataclass(frozen=True)
class PdfFileRecord:
    """A single PDF file with its aggregated metrics."""

    filename: str
    total_output_labels: int
    total_characters: int
    total_ink_area_sq_in: float
    total_label_material_sq_in: float
    total_substrate_sq_in: float = 0.0  # Substrate area in sq inches
    label_size: str = ""  # Label dimensions as "WxH" format
    cost_breakdown: CostBreakdown | None = None


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
    job, so the per-label table sums to the global totals.
    """

    text: str
    jobs: int  # number of job reports this code appears in
    instances: int  # printed instances summed across those jobs
    char_count: int  # characters summed across those instances
    ink_area_sq_in: float  # ink area summed across those instances
    size_w_in: float = 0.0  # label width in inches
    size_h_in: float = 0.0  # label height in inches
    substrate_sq_in: float = 0.0  # substrate area in sq inches
    cost_breakdown: CostBreakdown | None = None


@dataclass(frozen=True)
class ConsolidatedLabelBySize:
    """A single label code at a specific size, summed across jobs.

    Used for per-label-by-size breakdown reporting where each unique (text, size)
    combination is tracked separately with its own aggregated metrics.
    """

    text: str
    label_size: LabelSize
    instances: int  # printed instances at this size
    char_count: int  # total characters at this size
    ink_area_sq_in: float  # ink area at this size
    substrate_sq_in: float = 0.0  # substrate area in sq inches
    horizontal_scale: float = 1.0  # average scale factor across instances
    linear_feet: float = 0.0  # linear feet of substrate used (height * copies / 12)
    text_height_in: float = 2.0  # average text height in inches
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


def _load_per_pdf(entries: list[object], path: Path) -> list[PdfMetrics]:
    """Build the ``per_pdf`` list from a report's raw entries."""
    pdfs: list[PdfMetrics] = []
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise SystemExit(f"{path}: 'per_pdf[{idx}]' is not a JSON object")
        item: dict[str, object] = {str(k): v for k, v in entry.items()}
        cost_breakdown = _load_cost_breakdown(item)
        pdfs.append(
            PdfMetrics(
                total_output_labels=_as_int(item, "total_output_labels", path),
                total_characters=_as_int(item, "total_characters", path),
                total_ink_area_sq_in=_as_float(item, "total_ink_area_sq_in", path),
                total_label_material_sq_in=_as_float(
                    item, "total_label_material_sq_in", path
                ),
                cost_breakdown=cost_breakdown,
            )
        )
    return pdfs


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

    # Load per_pdf metrics if available
    per_pdf_metrics = None
    per_pdf_raw = report.get("per_pdf")
    if per_pdf_raw is not None:
        if not isinstance(per_pdf_raw, list):
            raise SystemExit(f"{path}: 'per_pdf' is not a JSON array")
        per_pdf_metrics = _load_per_pdf(per_pdf_raw, path)

    # Parse output_pdf field - can be string (old format) or array (new format)
    output_pdf_files = None
    if "output_pdf" in job:
        output_pdf_value = job["output_pdf"]
        if isinstance(output_pdf_value, str):
            output_pdf_files = [Path(output_pdf_value)]
        elif isinstance(output_pdf_value, list):
            output_pdf_files = [Path(p) for p in output_pdf_value if isinstance(p, str)]

    return JobReport(
        path=path,
        input_file=_as_str(job, "input_file", path),
        page_width_in=_as_float(job, "page_width_in", path),
        text_height_in=_as_float(job, "text_height_in", path)
        if "text_height_in" in job
        else 2.0,
        copies_per_label=_as_int(job, "copies_per_label", path),
        global_metrics=global_metrics,
        label_sizes=_load_label_sizes(report, job, global_metrics, path),
        per_label=per_label,
        output_pdf_files=output_pdf_files,
        per_pdf_metrics=per_pdf_metrics,
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


# --- Output path organization --------------------------------------------------


def organize_output_paths(input_directory: Path, basename: str) -> dict[str, Path]:
    """Organize output paths into a "Multi-Job Report" directory adjacent to input.

    Creates a "Multi-Job Report" directory at the same level as the input directory,
    with the following structure:
    - Multi-Job Report/
      - txt files (main directory)
      - csv/ (subdirectory for CSV reports)
      - json/ (subdirectory for JSON reports)
    - PDF Files for Vendor/
      - Simplified PDF files renamed by file number

    Returns a dict mapping output type to output path, creating directories if needed.
    """
    # Create "Multi-Job Report" directory adjacent to the input directory
    output_root = input_directory.parent / "Multi-Job Report"
    csv_dir = output_root / "csv"
    json_dir = output_root / "json"
    pdf_vendor_dir = input_directory.parent / "PDF Files for Vendor"

    # Create directories if they don't exist
    for d in [output_root, csv_dir, json_dir, pdf_vendor_dir]:
        d.mkdir(parents=True, exist_ok=True)

    return {
        "txt": output_root / f"{basename}.txt",
        "json": json_dir / f"{basename}.json",
        "txt_labels": output_root / f"{basename}_labels.txt",
        "txt_labels_customer": output_root / f"{basename}_labels_customer.txt",
        "txt_labels_xlsx": output_root / f"{basename}_labels.xlsx",
        "txt_labels_customer_xlsx": output_root / f"{basename}_labels_customer.xlsx",
        "csv_size_breakdown": csv_dir / f"{basename}_size_breakdown.csv",
        "csv_size_breakdown_customer": csv_dir
        / f"{basename}_size_breakdown_customer.csv",
        "csv_job_breakdown": csv_dir / f"{basename}_job_breakdown.csv",
        "csv_job_breakdown_customer": csv_dir
        / f"{basename}_job_breakdown_customer.csv",
        "csv_file_breakdown": csv_dir / f"{basename}_file_breakdown.csv",
        "csv_file_breakdown_customer": csv_dir
        / f"{basename}_file_breakdown_customer.csv",
        "csv_file_breakdown_vendor": csv_dir / f"{basename}_file_breakdown_vendor.csv",
        "csv_label_breakdown": csv_dir / f"{basename}_label_breakdown.csv",
        "csv_label_breakdown_customer": csv_dir
        / f"{basename}_label_breakdown_customer.csv",
        "pdf_files_vendor": pdf_vendor_dir,
    }


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
        total_unit_price = 0.0

        for report in reports:
            gm = report.global_metrics
            if gm.cost_breakdown:
                total_ink_cost += gm.cost_breakdown.ink_cost
                total_substrate_cost += gm.cost_breakdown.substrate_cost
                total_printer_hours += gm.cost_breakdown.printer_hours
                total_printer_cost += gm.cost_breakdown.printer_cost
                total_labor_hours += gm.cost_breakdown.labor_hours
                total_labor_cost += gm.cost_breakdown.labor_cost
                total_unit_price += gm.cost_breakdown.unit_price

        global_cost_total = (
            total_ink_cost
            + total_substrate_cost
            + total_printer_cost
            + total_labor_cost
        )
        # If unit_prices were explicitly set (flat pricing), use their sum;
        # otherwise recalculate from markup
        if total_unit_price > 0:
            global_unit_price = total_unit_price
        else:
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
    substrate_areas: dict[str, float] = {}  # substrate area in sq inches
    label_sizes: dict[str, tuple[float, float]] = {}  # text -> (width, height)
    total_costs: dict[str, float] = {}
    unit_prices: dict[str, float] = {}
    ink_costs: dict[str, float] = {}
    substrate_costs: dict[str, float] = {}
    printer_hours: dict[str, float] = {}
    printer_costs: dict[str, float] = {}
    labor_hours: dict[str, float] = {}
    labor_costs: dict[str, float] = {}

    for report in reports:
        # Get the label size for this report (all labels in a job share the same size)
        size_w_in = 0.0
        size_h_in = 0.0
        if report.label_sizes:
            for w, h in report.label_sizes.keys():
                size_w_in = w
                size_h_in = h
                break

        for label in report.per_label:
            jobs_seen[label.text] += 1
            instances[label.text] += report.copies_per_label
            ink_areas[label.text] = ink_areas.get(label.text, 0.0) + (
                label.ink_area_sq_in * report.copies_per_label
            )
            # Calculate substrate from material using yield ratio
            label_material_sq_in = size_w_in * size_h_in * report.copies_per_label
            yield_ratio = (
                report.global_metrics.total_substrate_sq_in
                / report.global_metrics.total_label_material_sq_in
                if report.global_metrics.total_label_material_sq_in > 0
                else 1.0
            )
            substrate_sq_in = label_material_sq_in * yield_ratio
            substrate_areas[label.text] = substrate_areas.get(label.text, 0.0) + (
                substrate_sq_in
            )
            # Store the label size (assume consistent size across jobs)
            if label.text not in label_sizes:
                label_sizes[label.text] = (size_w_in, size_h_in)
            if label.cost_breakdown:
                cb = label.cost_breakdown
                total_costs[label.text] = total_costs.get(label.text, 0.0) + (
                    cb.total_cost * report.copies_per_label
                )
                # Track unit_price (total price for this label across all copies)
                unit_prices[label.text] = unit_prices.get(label.text, 0.0) + (
                    cb.unit_price * report.copies_per_label
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
            # Use unit_price if it was set (flat pricing), otherwise calculate from markup
            if text in unit_prices and unit_prices[text] > 0:
                final_unit_price = unit_prices[text]
            elif pricing_config:
                final_unit_price = total_costs[text] * (
                    1.0 + pricing_config.markup_percent / 100.0
                )
            else:
                final_unit_price = total_costs[text]

            cost_breakdown = CostBreakdown(
                ink_cost=ink_costs.get(text, 0.0),
                substrate_cost=substrate_costs.get(text, 0.0),
                printer_hours=printer_hours.get(text, 0.0),
                printer_cost=printer_costs.get(text, 0.0),
                labor_hours=labor_hours.get(text, 0.0),
                labor_cost=labor_costs.get(text, 0.0),
                total_cost=total_costs[text],
                unit_price=final_unit_price,
            )

        size_w, size_h = label_sizes.get(text, (0.0, 0.0))
        labels_list.append(
            ConsolidatedLabel(
                text=text,
                jobs=jobs_seen[text],
                instances=instances[text],
                char_count=len(text) * instances[text],
                ink_area_sq_in=ink_areas[text],
                size_w_in=size_w,
                size_h_in=size_h,
                substrate_sq_in=substrate_areas.get(text, 0.0),
                cost_breakdown=cost_breakdown,
            )
        )
    return labels_list


def consolidate_labels_by_size(
    reports: list[JobReport], pricing_config: PricingConfig | None = None
) -> list[ConsolidatedLabelBySize]:
    """Consolidate label metrics grouped by label text and size.

    Each unique (label_text, label_size) combination is tracked separately,
    allowing per-label-by-size reporting. Since all labels in a job share the
    same size, we can determine the size for each label from its report.
    """
    # Track metrics by (text, size) tuple
    # Store: (copies_per_label, ink_area_per_instance, scale, cost_breakdown, text_height_in, yield_ratio, label_width, label_height)
    label_by_size_key: dict[
        str,
        list[
            tuple[int, float, float, CostBreakdown | None, float, float, float, float]
        ],
    ] = {}

    for report in reports:
        # All labels in this report share the same size
        label_size = list(report.label_sizes.keys())[0] if report.label_sizes else None
        if label_size is None:
            continue

        # Calculate yield ratio for this report
        yield_ratio = (
            report.global_metrics.total_substrate_sq_in
            / report.global_metrics.total_label_material_sq_in
            if report.global_metrics.total_label_material_sq_in > 0
            else 1.0
        )

        for label in report.per_label:
            key_str = f"{label.text}::{label_size[0]}x{label_size[1]}"
            if key_str not in label_by_size_key:
                label_by_size_key[key_str] = []
            label_by_size_key[key_str].append(
                (
                    report.copies_per_label,
                    label.ink_area_sq_in,
                    label.horizontal_scale,
                    label.cost_breakdown,
                    report.text_height_in,
                    yield_ratio,
                    label_size[0],
                    label_size[1],
                )
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
        avg_scale = (
            total_scale_weighted / total_instances if total_instances > 0 else 1.0
        )

        # Compute average text height (weighted by copies)
        total_text_height_weighted = sum(entry[0] * entry[4] for entry in entries)
        avg_text_height = (
            total_text_height_weighted / total_instances if total_instances > 0 else 2.0
        )

        # Calculate substrate from material and yield ratio
        total_substrate_sq_in = 0.0
        for copies, _, _, _, _, yield_ratio, _, _ in entries:
            label_material_per_job = label_size[0] * label_size[1] * copies
            substrate_per_job = label_material_per_job * yield_ratio
            total_substrate_sq_in += substrate_per_job

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
        total_unit_price = 0.0

        for copies, _, _, cb, _, _, _, _ in entries:
            if cb:
                total_ink_cost += cb.ink_cost * copies
                total_substrate_cost += cb.substrate_cost * copies
                total_printer_hours += cb.printer_hours * copies
                total_printer_cost += cb.printer_cost * copies
                total_labor_hours += cb.labor_hours * copies
                total_labor_cost += cb.labor_cost * copies
                total_cost += cb.total_cost * copies
                total_unit_price += cb.unit_price * copies

        if total_cost > 0:
            # Use unit_price if it was set (flat pricing), otherwise calculate from markup
            if total_unit_price > 0:
                final_unit_price = total_unit_price
            elif pricing_config:
                final_unit_price = total_cost * (
                    1.0 + pricing_config.markup_percent / 100.0
                )
            else:
                final_unit_price = total_cost

            cost_breakdown = CostBreakdown(
                ink_cost=total_ink_cost,
                substrate_cost=total_substrate_cost,
                printer_hours=total_printer_hours,
                printer_cost=total_printer_cost,
                labor_hours=total_labor_hours,
                labor_cost=total_labor_cost,
                total_cost=total_cost,
                unit_price=final_unit_price,
            )

        result.append(
            ConsolidatedLabelBySize(
                text=text,
                label_size=label_size,
                instances=total_instances,
                char_count=total_char_count,
                ink_area_sq_in=total_ink_area,
                substrate_sq_in=total_substrate_sq_in,
                horizontal_scale=avg_scale,
                linear_feet=linear_feet,
                text_height_in=avg_text_height,
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
    unit_price_totals: dict[LabelSize, float] = {}

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
                    unit_price_totals[size] = 0.0
                cost_totals[size]["ink_cost"] += cb.ink_cost * share
                cost_totals[size]["substrate_cost"] += cb.substrate_cost * share
                cost_totals[size]["printer_hours"] += cb.printer_hours * share
                cost_totals[size]["printer_cost"] += cb.printer_cost * share
                cost_totals[size]["labor_hours"] += cb.labor_hours * share
                cost_totals[size]["labor_cost"] += cb.labor_cost * share
                cost_totals[size]["total_cost"] += cb.total_cost * share
                unit_price_totals[size] += cb.unit_price * share

    result = {}
    for size, (substrate, material, ink) in totals.items():
        cost_breakdown = None
        if size in cost_totals and cost_totals[size]["total_cost"] > 0:
            ct = cost_totals[size]
            # Use unit_price if it was set (flat pricing), otherwise calculate from markup
            if size in unit_price_totals and unit_price_totals[size] > 0:
                unit_price = unit_price_totals[size]
            elif pricing_config:
                unit_price = ct["total_cost"] * (
                    1.0 + pricing_config.markup_percent / 100.0
                )
            else:
                unit_price = ct["total_cost"]

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


def build_pdf_file_records(reports: list[JobReport]) -> list[PdfFileRecord]:
    """Build a list of PdfFileRecord from job reports.

    Extracts PDF file information from reports with per-PDF metrics,
    matching each file with its corresponding metrics and label size.
    """
    all_pdf_records: list[PdfFileRecord] = []
    for report in reports:
        if report.output_pdf_files and report.per_pdf_metrics:
            # Extract label size from this report (all labels in a job have same size)
            label_size_str = ""
            if report.label_sizes:
                for size in report.label_sizes.keys():
                    label_size_str = format_label_size(size)
                    break
            # Pair each PDF file with its corresponding metrics
            for pdf_path, pdf_metric in zip(
                report.output_pdf_files, report.per_pdf_metrics
            ):
                # Calculate substrate from material using yield ratio
                yield_ratio = (
                    report.global_metrics.total_substrate_sq_in
                    / report.global_metrics.total_label_material_sq_in
                    if report.global_metrics.total_label_material_sq_in > 0
                    else 1.0
                )
                substrate_sq_in = pdf_metric.total_label_material_sq_in * yield_ratio

                all_pdf_records.append(
                    PdfFileRecord(
                        filename=pdf_path.name,
                        total_output_labels=pdf_metric.total_output_labels,
                        total_characters=pdf_metric.total_characters,
                        total_ink_area_sq_in=pdf_metric.total_ink_area_sq_in,
                        total_label_material_sq_in=pdf_metric.total_label_material_sq_in,
                        total_substrate_sq_in=substrate_sq_in,
                        label_size=label_size_str,
                        cost_breakdown=pdf_metric.cost_breakdown,
                    )
                )
    return all_pdf_records


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
    json_path: Path | None = None,
) -> Path:
    """Write a human-readable consolidated report to ``out_path``.

    Also writes a machine-readable JSON sibling report to ``json_path``
    (or derived from out_path if not provided). Returns the path of
    the JSON report.
    """
    if json_path is None:
        json_path = out_path.with_suffix(".json")
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
    files_per_size: Counter[LabelSize] = Counter()
    for report in reports:
        jobs_per_size.update(report.label_sizes.keys())
        # Count PDF files per size
        num_files = len(report.output_pdf_files) if report.output_pdf_files else 1
        for size in report.label_sizes.keys():
            files_per_size[size] += num_files

    # Check if any size_areas has cost_breakdown to decide on table format
    has_costs = any(sa.cost_breakdown is not None for sa in size_areas.values())

    if has_costs:
        lines.append(
            f"{'Label Size (WxH)':<16} | {'Jobs':>4} | {'Files':>5} | {'Labels':>6} | "
            f"{'Substrate (sq ft)':>17} | {'Label Area (sq ft)':>18} | {'Ink (sq ft)':>11} | "
            f"{'Substrate Cost':>14} | {'Ink Cost':>10} | {'Printer Cost':>12} | "
            f"{'Labor Cost':>10} | {'Total Cost':>10}"
        )
        lines.append(
            f"{'-' * 16}-+-{'-' * 4}-+-{'-' * 5}-+-{'-' * 6}-+-{'-' * 17}-+-{'-' * 18}-+-{'-' * 11}-+-{'-' * 14}-+-{'-' * 10}-+-{'-' * 12}-+-{'-' * 10}-+-{'-' * 10}"
        )
        for size, count in sorted(sizes.items()):
            area = size_areas[size]
            cost_str = ""
            if area.cost_breakdown:
                cb = area.cost_breakdown
                cost_str = (
                    f" | ${cb.substrate_cost:>13.2f} | ${cb.ink_cost:>9.2f} | "
                    f"${cb.printer_cost:>11.2f} | ${cb.labor_cost:>9.2f} | "
                    f"${cb.total_cost:>9.2f}"
                )
            else:
                cost_str = (
                    " |              |          |            |          |         "
                )
            lines.append(
                f"{format_label_size(size):<16} | {jobs_per_size[size]:>4d} | {files_per_size[size]:>5d} | {count:>6d} | "
                f"{sq_ft(area.substrate_sq_in):>17.2f} | "
                f"{sq_ft(area.label_material_sq_in):>18.2f} | "
                f"{sq_ft(area.ink_area_sq_in):>11.2f}{cost_str}"
            )
        lines.append(
            f"{'=' * 16}=+={'=' * 4}=+={'=' * 5}=+={'=' * 6}=+={'=' * 17}=+={'=' * 18}=+={'=' * 11}=+={'=' * 14}=+={'=' * 10}=+={'=' * 12}=+={'=' * 10}=+={'=' * 10}"
        )
        total_cost_str = ""
        if metrics.cost_breakdown:
            cb = metrics.cost_breakdown
            total_cost_str = (
                f" | ${cb.substrate_cost:>13.2f} | ${cb.ink_cost:>9.2f} | "
                f"${cb.printer_cost:>11.2f} | ${cb.labor_cost:>9.2f} | "
                f"${cb.total_cost:>9.2f}"
            )
        else:
            total_cost_str = (
                " |              |          |            |          |         "
            )
        total_files = sum(files_per_size.values())
        lines.append(
            f"{'Total':<16} | {metrics.total_jobs:>4d} | {total_files:>5d} | {metrics.total_output_labels:>6d} | "
            f"{sq_ft(metrics.total_substrate_sq_in):>17.2f} | "
            f"{sq_ft(metrics.total_label_material_sq_in):>18.2f} | "
            f"{sq_ft(metrics.total_ink_area_sq_in):>11.2f}{total_cost_str}"
        )
    else:
        lines.append(
            f"{'Label Size (WxH)':<16} | {'Jobs':>4} | {'Files':>5} | {'Labels':>6} | "
            f"{'Substrate (sq ft)':>17} | {'Label Area (sq ft)':>18} | {'Ink (sq ft)':>11}"
        )
        lines.append(
            f"{'-' * 16}-+-{'-' * 4}-+-{'-' * 5}-+-{'-' * 6}-+-{'-' * 17}-+-{'-' * 18}-+-{'-' * 11}"
        )
        for size, count in sorted(sizes.items()):
            area = size_areas[size]
            lines.append(
                f"{format_label_size(size):<16} | {jobs_per_size[size]:>4d} | {files_per_size[size]:>5d} | {count:>6d} | "
                f"{sq_ft(area.substrate_sq_in):>17.2f} | "
                f"{sq_ft(area.label_material_sq_in):>18.2f} | "
                f"{sq_ft(area.ink_area_sq_in):>11.2f}"
            )
        lines.append(
            f"{'=' * 16}=+={'=' * 4}=+={'=' * 5}=+={'=' * 6}=+={'=' * 17}=+={'=' * 18}=+={'=' * 11}"
        )
        total_files = sum(files_per_size.values())
        lines.append(
            f"{'Total':<16} | {metrics.total_jobs:>4d} | {total_files:>5d} | {metrics.total_output_labels:>6d} | "
            f"{sq_ft(metrics.total_substrate_sq_in):>17.2f} | "
            f"{sq_ft(metrics.total_label_material_sq_in):>18.2f} | "
            f"{sq_ft(metrics.total_ink_area_sq_in):>11.2f}"
        )
    lines.append("")

    lines.append(subrule)
    lines.append("VENDOR LABEL SIZE BREAKDOWN")
    lines.append(subrule)
    lines.append(
        f"{'Label Size (WxH)':<16} | {'Jobs':>4} | {'Files':>5} | {'Labels':>6} | "
        f"{'Substrate (sq ft)':>17} | {'Label Area (sq ft)':>18} | {'Ink (sq ft)':>11}"
    )
    lines.append(
        f"{'-' * 16}-+-{'-' * 4}-+-{'-' * 5}-+-{'-' * 6}-+-{'-' * 17}-+-{'-' * 18}-+-{'-' * 11}"
    )
    for size, count in sorted(sizes.items()):
        area = size_areas[size]
        lines.append(
            f"{format_label_size(size):<16} | {jobs_per_size[size]:>4d} | {files_per_size[size]:>5d} | {count:>6d} | "
            f"{sq_ft(area.substrate_sq_in):>17.2f} | "
            f"{sq_ft(area.label_material_sq_in):>18.2f} | "
            f"{sq_ft(area.ink_area_sq_in):>11.2f}"
        )
    lines.append(
        f"{'=' * 16}=+={'=' * 4}=+={'=' * 5}=+={'=' * 6}=+={'=' * 17}=+={'=' * 18}=+={'=' * 11}"
    )
    total_files = sum(files_per_size.values())
    lines.append(
        f"{'Total':<16} | {metrics.total_jobs:>4d} | {total_files:>5d} | {metrics.total_output_labels:>6d} | "
        f"{sq_ft(metrics.total_substrate_sq_in):>17.2f} | "
        f"{sq_ft(metrics.total_label_material_sq_in):>18.2f} | "
        f"{sq_ft(metrics.total_ink_area_sq_in):>11.2f}"
    )
    lines.append("")

    # FILE BREAKDOWN section - list all unique PDF files with their metrics
    # Build PDF file records using helper function
    all_pdf_records = build_pdf_file_records(reports)

    if all_pdf_records:
        # Sort by label size first, then filename for vendor-friendly ordering
        sorted_records = sorted(
            all_pdf_records, key=lambda r: (r.label_size, r.filename)
        )

        lines.append(subrule)
        lines.append("FILE BREAKDOWN")
        lines.append(subrule)
        # Get max filename length for formatting
        max_filename_len = max(
            (len(record.filename) for record in sorted_records), default=80
        )
        lines.append(
            f"{'File #':>6} | {'Filename':<{max_filename_len}} | {'Label WxH':>9} | {'Labels':>6} | {'Substrate (sq ft)':>17} | {'Label Area (sq ft)':>17} | "
            f"{'Ink (sq in)':>11} | {'Ink (sq ft)':>11}"
        )
        lines.append(
            f"{'-' * 6}-+-{'-' * max_filename_len}-+-{'-' * 9}-+-{'-' * 6}-+-{'-' * 17}-+-{'-' * 17}-+-{'-' * 11}-+-{'-' * 11}"
        )
        total_labels = 0
        total_substrate_sqft = 0.0
        total_label_area_sqft = 0.0
        total_ink_sq_in = 0.0
        total_ink_sq_ft = 0.0
        for file_num, record in enumerate(sorted_records, start=1):
            lines.append(
                f"{file_num:>6d} | {record.filename:<{max_filename_len}} | {record.label_size:>9} | {record.total_output_labels:>6d} | "
                f"{sq_ft(record.total_substrate_sq_in):>17.2f} | {sq_ft(record.total_label_material_sq_in):>17.2f} | "
                f"{record.total_ink_area_sq_in:>11.2f} | "
                f"{sq_ft(record.total_ink_area_sq_in):>11.2f}"
            )
            total_labels += record.total_output_labels
            total_substrate_sqft += sq_ft(record.total_substrate_sq_in)
            total_label_area_sqft += sq_ft(record.total_label_material_sq_in)
            total_ink_sq_in += record.total_ink_area_sq_in
            total_ink_sq_ft += sq_ft(record.total_ink_area_sq_in)
        lines.append(
            f"{'=' * 6}=+={'=' * max_filename_len}=+={'=' * 9}=+={'=' * 6}=+={'=' * 17}=+={'=' * 17}=+={'=' * 11}=+={'=' * 11}"
        )
        lines.append(
            f"{'':>6} | {'TOTAL':<{max_filename_len}} | {'':>9} | {total_labels:>6d} | "
            f"{total_substrate_sqft:>17.2f} | {total_label_area_sqft:>17.2f} | "
            f"{total_ink_sq_in:>11.2f} | "
            f"{total_ink_sq_ft:>11.2f}"
        )
        lines.append("")

        # FILE BREAKDOWN VENDOR section - same as FILE BREAKDOWN but without Filename
        lines.append(subrule)
        lines.append("FILE BREAKDOWN VENDOR")
        lines.append(subrule)
        lines.append(
            f"{'File #':>6} | {'Label WxH':>9} | {'Labels':>6} | {'Substrate (sq ft)':>17} | {'Label Area (sq ft)':>17} | "
            f"{'Ink (sq in)':>11} | {'Ink (sq ft)':>11}"
        )
        lines.append(
            f"{'-' * 6}-+-{'-' * 9}-+-{'-' * 6}-+-{'-' * 17}-+-{'-' * 17}-+-{'-' * 11}-+-{'-' * 11}"
        )
        total_labels_vendor = 0
        total_substrate_sqft_vendor = 0.0
        total_label_area_sqft_vendor = 0.0
        total_ink_sq_in_vendor = 0.0
        total_ink_sq_ft_vendor = 0.0
        for file_num, record in enumerate(sorted_records, start=1):
            lines.append(
                f"{file_num:>6d} | {record.label_size:>9} | {record.total_output_labels:>6d} | "
                f"{sq_ft(record.total_substrate_sq_in):>17.2f} | {sq_ft(record.total_label_material_sq_in):>17.2f} | "
                f"{record.total_ink_area_sq_in:>11.2f} | "
                f"{sq_ft(record.total_ink_area_sq_in):>11.2f}"
            )
            total_labels_vendor += record.total_output_labels
            total_substrate_sqft_vendor += sq_ft(record.total_substrate_sq_in)
            total_label_area_sqft_vendor += sq_ft(record.total_label_material_sq_in)
            total_ink_sq_in_vendor += record.total_ink_area_sq_in
            total_ink_sq_ft_vendor += sq_ft(record.total_ink_area_sq_in)
        lines.append(
            f"{'=' * 6}=+={'=' * 9}=+={'=' * 6}=+={'=' * 17}=+={'=' * 17}=+={'=' * 11}=+={'=' * 11}"
        )
        lines.append(
            f"{'':>6} | {'TOTAL':>9} | {total_labels_vendor:>6d} | "
            f"{total_substrate_sqft_vendor:>17.2f} | {total_label_area_sqft_vendor:>17.2f} | "
            f"{total_ink_sq_in_vendor:>11.2f} | "
            f"{total_ink_sq_ft_vendor:>11.2f}"
        )
        lines.append("")

    lines.append(subrule)
    lines.append("JOB BREAKDOWN")
    lines.append(subrule)
    # Get max input file length for formatting
    max_input_file_len = max(
        (
            len(display_input_file(report.input_file, directory.parent))
            for report in reports
        ),
        default=33,
    )
    lines.append(
        f"{'Input File':<{max_input_file_len}} | {'WxH':>5} | {'# Files':>7} | {'Roll':>5} | {'Labels':>6} | {'Substrate (sq ft)':>17} | {'Label Area (sq ft)':>17} | {'Lin Ft':>9} | "
        f"{'Ink (sq in)':>11} | {'Ink (sq ft)':>11}"
    )
    lines.append(
        f"{'-' * max_input_file_len}-+-{'-' * 5}-+-{'-' * 7}-+-{'-' * 5}-+-{'-' * 6}-+-{'-' * 17}-+-{'-' * 17}-+-{'-' * 9}-+-{'-' * 11}-+-{'-' * 11}"
    )
    total_job_files = 0
    total_job_labels = 0
    total_job_substrate_sqft = 0.0
    total_job_label_area_sqft = 0.0
    total_job_lin_ft = 0.0
    total_job_ink_sq_in = 0.0
    total_job_ink_sq_ft = 0.0
    for report in reports:
        gm = report.global_metrics
        input_file = display_input_file(report.input_file, directory.parent)
        num_files = len(report.output_pdf_files) if report.output_pdf_files else 1
        # Extract label size (all labels in a job have same size)
        label_size_str = ""
        if report.label_sizes:
            for size in report.label_sizes.keys():
                label_size_str = format_label_size(size)
                break
        lines.append(
            f"{input_file:<{max_input_file_len}} | {label_size_str:>5} | {num_files:>7d} | {report.page_width_in:>5.0f} | "
            f"{gm.total_output_labels:>6d} | {sq_ft(gm.total_substrate_sq_in):>17.2f} | {sq_ft(gm.total_label_material_sq_in):>17.2f} | {gm.linear_feet:>9.2f} | "
            f"{gm.total_ink_area_sq_in:>11.2f} | "
            f"{sq_ft(gm.total_ink_area_sq_in):>11.2f}"
        )
        total_job_files += num_files
        total_job_labels += gm.total_output_labels
        total_job_substrate_sqft += sq_ft(gm.total_substrate_sq_in)
        total_job_label_area_sqft += sq_ft(gm.total_label_material_sq_in)
        total_job_lin_ft += gm.linear_feet
        total_job_ink_sq_in += gm.total_ink_area_sq_in
        total_job_ink_sq_ft += sq_ft(gm.total_ink_area_sq_in)
    lines.append(
        f"{'=' * max_input_file_len}=+={'=' * 5}=+={'=' * 7}=+={'=' * 5}=+={'=' * 6}=+={'=' * 17}=+={'=' * 17}=+={'=' * 9}=+={'=' * 11}=+={'=' * 11}"
    )
    lines.append(
        f"{'TOTAL':<{max_input_file_len}} | {'':>5} | {total_job_files:>7d} | {'':>5} | {total_job_labels:>6d} | {total_job_substrate_sqft:>17.2f} | {total_job_label_area_sqft:>17.2f} | {total_job_lin_ft:>9.2f} | "
        f"{total_job_ink_sq_in:>11.2f} | "
        f"{total_job_ink_sq_ft:>11.2f}"
    )
    lines.append("")
    lines.append(subrule)
    lines.append("PER-LABEL BREAKDOWN")
    lines.append(subrule)
    # Get max label code length for formatting, ensuring at least as wide as header
    max_label_code_len = max((len(label.text) for label in labels), default=16)
    max_label_code_len = max(max_label_code_len, 10)  # Minimum width for "Label Code"
    lines.append(
        f"{'Label Code':<{max_label_code_len}} | {'Jobs':>4} | {'Copies':>5} | {'Chars':>5} | "
        f"{'Label Area (sq ft)':>18} | {'Substrate (sq ft)':>17} | "
        f"{'Ink Area (sq in)':>16} | {'Ink Area (sq ft)':>16}"
    )
    lines.append(
        f"{'-' * max_label_code_len}-+-{'-' * 4}-+-{'-' * 6}-+-{'-' * 5}-+-{'-' * 18}-+-{'-' * 17}-+-{'-' * 16}-+-{'-' * 16}"
    )
    total_per_label_jobs = 0
    total_per_label_copies = 0
    total_per_label_chars = 0
    total_per_label_area_sqft = 0.0
    total_per_label_substrate_sqft = 0.0
    total_per_label_ink_sq_in = 0.0
    total_per_label_ink_sq_ft = 0.0
    for label in labels:
        # Calculate label area: design size * number of instances
        label_area = (label.size_w_in * label.size_h_in) * label.instances
        lines.append(
            f"{label.text:<{max_label_code_len}} | {label.jobs:>4d} | {label.instances:>6d} | "
            f"{label.char_count:>5d} | {sq_ft(label_area):>18.4f} | {sq_ft(label.substrate_sq_in):>17.2f} | "
            f"{label.ink_area_sq_in:>16.4f} | "
            f"{sq_ft(label.ink_area_sq_in):>16.4f}"
        )
        total_per_label_jobs += label.jobs
        total_per_label_copies += label.instances
        total_per_label_chars += label.char_count
        total_per_label_area_sqft += sq_ft(label_area)
        total_per_label_substrate_sqft += sq_ft(label.substrate_sq_in)
        total_per_label_ink_sq_in += label.ink_area_sq_in
        total_per_label_ink_sq_ft += sq_ft(label.ink_area_sq_in)
    lines.append(
        f"{'=' * max_label_code_len}=+={'=' * 4}=+={'=' * 5}=+={'=' * 5}=+={'=' * 18}=+={'=' * 17}=+={'=' * 16}=+={'=' * 16}"
    )
    lines.append(
        f"{'TOTAL':<{max_label_code_len}} | {total_per_label_jobs:>4d} | {total_per_label_copies:>6d} | "
        f"{total_per_label_chars:>5d} | {total_per_label_area_sqft:>18.4f} | {total_per_label_substrate_sqft:>17.2f} | "
        f"{total_per_label_ink_sq_in:>16.4f} | "
        f"{total_per_label_ink_sq_ft:>16.4f}"
    )
    lines.append(rule)
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")

    write_consolidated_report_json(
        metrics, sizes, size_areas, labels, reports, json_path, directory, timestamp
    )
    return json_path


def write_per_label_report(
    reports: list[JobReport],
    output_path: Path,
    directory: Path,
    include_costs: bool = True,
) -> None:
    """Write per-label metrics from all jobs to a formatted text report.

    Each job's labels are shown separately, with a "Job" column identifying
    which report each label came from. If include_costs is True, includes full
    cost breakdown (for internal use). If False, includes only code and unit
    price (for customers).
    """
    lines: list[str] = []

    if include_costs:
        # Internal report: all columns
        lines.append("-" * 180)
        header = (
            f"{'Job':<30} | {'Label Code':<16} | {'Char Count':>5} | {'Scale':>7} | "
            f"{'Ink (sq in)':>12} | {'Ink (sq ft)':>11} | "
            f"{'Ink ($)':>10} | {'Substrate ($)':>13} | {'Print Hrs':>9} | "
            f"{'Print ($)':>10} | {'Labor Hrs':>9} | {'Labor ($)':>10} | "
            f"{'Total ($)':>10} | {'Unit ($)':>10}"
        )
        lines.append(header)
        lines.append("-" * 180)

        for report in reports:
            job_name = display_input_file(report.input_file, directory.parent)
            job_name = Path(job_name).stem  # Remove file extension
            for m in report.per_label:
                label_code = m.text[:16]
                char_count = str(m.char_count)
                scale = f"{m.horizontal_scale:.3f}"
                ink_sq_in = f"{m.ink_area_sq_in:.4f}"
                ink_sq_ft = f"{sq_ft(m.ink_area_sq_in):.4f}"

                if m.cost_breakdown:
                    ink_cost = f"{m.cost_breakdown.ink_cost:.2f}"
                    substrate_cost = f"{m.cost_breakdown.substrate_cost:.2f}"
                    printer_hours = f"{m.cost_breakdown.printer_hours:.2f}"
                    printer_cost = f"{m.cost_breakdown.printer_cost:.2f}"
                    labor_hours = f"{m.cost_breakdown.labor_hours:.2f}"
                    labor_cost = f"{m.cost_breakdown.labor_cost:.2f}"
                    total_cost = f"{m.cost_breakdown.total_cost:.2f}"
                    unit_price = f"{m.cost_breakdown.unit_price:.2f}"
                else:
                    ink_cost = ""
                    substrate_cost = ""
                    printer_hours = ""
                    printer_cost = ""
                    labor_hours = ""
                    labor_cost = ""
                    total_cost = ""
                    unit_price = ""

                row = (
                    f"{job_name:<30} | {label_code:<16} | {char_count:>5} | {scale:>7} | "
                    f"{ink_sq_in:>12} | {ink_sq_ft:>11} | "
                    f"{ink_cost:>10} | {substrate_cost:>13} | {printer_hours:>9} | "
                    f"{printer_cost:>10} | {labor_hours:>9} | {labor_cost:>10} | "
                    f"{total_cost:>10} | {unit_price:>10}"
                )
                lines.append(row)
    else:
        # Customer report: only job, label code, and unit price
        lines.append("-" * 60)
        header = f"{'Job':<30} | {'Label Code':<16} | {'Unit Price ($)':>12}"
        lines.append(header)
        lines.append("-" * 60)

        for report in reports:
            job_name = display_input_file(report.input_file, directory.parent)
            job_name = Path(job_name).stem  # Remove file extension
            for m in report.per_label:
                label_code = m.text[:16]
                unit_price = (
                    f"{m.cost_breakdown.unit_price:.2f}" if m.cost_breakdown else ""
                )
                row = f"{job_name:<30} | {label_code:<16} | {unit_price:>12}"
                lines.append(row)

    lines.append("-" * 180 if include_costs else "-" * 60)
    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def write_per_label_report_xlsx(
    reports: list[JobReport],
    output_path: Path,
    directory: Path,
    include_costs: bool = True,
    customer_report_config: dict[str, bool] | None = None,
) -> None:
    """Write per-label metrics to an Excel workbook with one sheet per job.

    Creates a sheet for each job, showing one row per label in that job.
    If include_costs is True, includes full cost breakdown (for internal use).
    If False, uses customer_report_config to determine which columns to include.
    """
    wb = Workbook()
    default_sheet = wb.active
    if default_sheet is not None:
        wb.remove(default_sheet)  # Remove default sheet

    # Define header style
    header_fill = PatternFill(
        start_color="D3D3D3", end_color="D3D3D3", fill_type="solid"
    )
    header_font = Font(bold=True)

    if include_costs:
        # Internal version: all columns for each job sheet
        headers = [
            "Label Code",
            "Label Size (WxH)",
            "Char Count",
            "Scale",
            "Ink (sq in)",
            "Ink (sq ft)",
            "Ink ($)",
            "Substrate ($)",
            "Print Hrs",
            "Print ($)",
            "Labor Hrs",
            "Labor ($)",
            "Total ($)",
            "Unit ($)",
        ]

        for report in reports:
            # Create sheet for this job
            job_name = display_input_file(report.input_file, directory.parent)
            job_name = Path(job_name).stem  # Remove file extension
            # Sanitize sheet name: remove invalid Excel characters (/, \, ?, *, :, [, ])
            # and limit to 31 characters (Excel limit)
            sanitized = (
                job_name.replace("/", "-")
                .replace("\\", "-")
                .replace("?", "")
                .replace("*", "")
                .replace(":", "")
                .replace("[", "")
                .replace("]", "")
            )
            sheet_name = sanitized[:31]
            ws = wb.create_sheet(title=sheet_name)

            # Add headers
            for col_num, header in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col_num, value=header)
                cell.fill = header_fill
                cell.font = header_font

            # Get label size for this job (all labels in a job share the same size)
            label_size = (
                list(report.label_sizes.keys())[0] if report.label_sizes else None
            )
            label_size_str = format_label_size(label_size) if label_size else ""

            # Add label rows
            row_num = 2
            for label in report.per_label:
                ws.cell(row=row_num, column=1, value=label.text)
                ws.cell(row=row_num, column=2, value=label_size_str)
                ws.cell(row=row_num, column=3, value=label.char_count)
                ws.cell(row=row_num, column=4, value=label.horizontal_scale)
                ws.cell(row=row_num, column=5, value=label.ink_area_sq_in)
                ws.cell(row=row_num, column=6, value=sq_ft(label.ink_area_sq_in))

                if label.cost_breakdown:
                    ws.cell(row=row_num, column=7, value=label.cost_breakdown.ink_cost)
                    ws.cell(
                        row=row_num, column=8, value=label.cost_breakdown.substrate_cost
                    )
                    ws.cell(
                        row=row_num, column=9, value=label.cost_breakdown.printer_hours
                    )
                    ws.cell(
                        row=row_num, column=10, value=label.cost_breakdown.printer_cost
                    )
                    ws.cell(
                        row=row_num, column=11, value=label.cost_breakdown.labor_hours
                    )
                    ws.cell(
                        row=row_num, column=12, value=label.cost_breakdown.labor_cost
                    )
                    ws.cell(
                        row=row_num, column=13, value=label.cost_breakdown.total_cost
                    )
                    ws.cell(
                        row=row_num, column=14, value=label.cost_breakdown.unit_price
                    )

                row_num += 1

            # Auto-adjust column widths
            for col_num in range(1, len(headers) + 1):
                ws.column_dimensions[
                    ws.cell(row=1, column=col_num).column_letter
                ].width = min(
                    max(
                        len(str(ws.cell(row=r, column=col_num).value or ""))
                        for r in range(1, row_num)
                    ),
                    50,
                )
    else:
        # Customer version: columns based on customer_report_config
        # Always include label code and label size
        headers = ["Label Code"]

        # Map config keys to column names
        config_map = {
            "label_size_wxh": "Label Size (WxH)",
            "char_count": "Char Count",
            "scale": "Scale",
            "label_size_sq_in": "Label Size (sq in)",
            "label_area_sq_ft": "Label Area (sq ft)",
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

        # Build header list based on customer_report_config
        if customer_report_config:
            for config_key, col_name in config_map.items():
                if customer_report_config.get(config_key, False):
                    headers.append(col_name)
        else:
            # Default to just label code and unit price if no config
            headers.append("Unit Price ($)")

        for report in reports:
            # Create sheet for this job
            job_name = display_input_file(report.input_file, directory.parent)
            job_name = Path(job_name).stem  # Remove file extension
            # Sanitize sheet name: remove invalid Excel characters (/, \, ?, *, :, [, ])
            # and limit to 31 characters (Excel limit)
            sanitized = (
                job_name.replace("/", "-")
                .replace("\\", "-")
                .replace("?", "")
                .replace("*", "")
                .replace(":", "")
                .replace("[", "")
                .replace("]", "")
            )
            sheet_name = sanitized[:31]
            ws = wb.create_sheet(title=sheet_name)

            # Add headers
            for col_num, header in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col_num, value=header)
                cell.fill = header_fill
                cell.font = header_font

            # Get label size for this job (all labels in a job share the same size)
            label_size = (
                list(report.label_sizes.keys())[0] if report.label_sizes else None
            )
            label_size_str = format_label_size(label_size) if label_size else ""

            # Add label rows
            row_num = 2
            for label in report.per_label:
                ws.cell(row=row_num, column=1, value=label.text)
                ws.cell(row=row_num, column=2, value=label_size_str)

                # Add optional columns based on configuration
                col_num = 3
                if customer_report_config:
                    for config_key in config_map.keys():
                        # Skip label_size_wxh since it's already added as a fixed column
                        if config_key == "label_size_wxh":
                            continue
                        if customer_report_config.get(config_key, False):
                            col_name = config_map[config_key]
                            value: float | int | str | None = None

                            if config_key == "char_count":
                                value = label.char_count
                            elif config_key == "scale":
                                value = label.horizontal_scale
                            elif config_key == "ink_sq_in":
                                value = label.ink_area_sq_in
                            elif config_key == "ink_sq_ft":
                                value = sq_ft(label.ink_area_sq_in)
                            elif label.cost_breakdown:
                                if config_key == "ink_cost":
                                    value = label.cost_breakdown.ink_cost
                                elif config_key == "substrate_cost":
                                    value = label.cost_breakdown.substrate_cost
                                elif config_key == "printer_hours":
                                    value = label.cost_breakdown.printer_hours
                                elif config_key == "printer_cost":
                                    value = label.cost_breakdown.printer_cost
                                elif config_key == "labor_hours":
                                    value = label.cost_breakdown.labor_hours
                                elif config_key == "labor_cost":
                                    value = label.cost_breakdown.labor_cost
                                elif config_key == "total_cost":
                                    value = label.cost_breakdown.total_cost
                                elif config_key == "price":
                                    value = label.cost_breakdown.unit_price
                                elif config_key == "unit_price":
                                    value = label.cost_breakdown.unit_price

                            ws.cell(row=row_num, column=col_num, value=value)
                            col_num += 1
                else:
                    # Default: just unit price
                    if label.cost_breakdown:
                        ws.cell(
                            row=row_num, column=3, value=label.cost_breakdown.unit_price
                        )

                row_num += 1

            # Auto-adjust column widths
            for col_num in range(1, len(headers) + 1):
                ws.column_dimensions[
                    ws.cell(row=1, column=col_num).column_letter
                ].width = min(
                    max(
                        len(str(ws.cell(row=r, column=col_num).value or ""))
                        for r in range(1, row_num)
                    ),
                    50,
                )

    wb.save(output_path)


def write_size_breakdown_csv(
    sizes: Counter[LabelSize],
    size_areas: dict[LabelSize, SizeAreas],
    output_path: Path,
    include_costs: bool = True,
    reports: list[JobReport] | None = None,
    customer_report_config: dict[str, bool] | None = None,
) -> None:
    """Write size breakdown to a CSV file.

    reports is used to determine text_height_in for each label size.
    If not provided, a default of 2.0 is used.
    """
    # Build a mapping of size -> list of text heights (to take the first/primary one)
    # Also build files_per_size counter for PDF file tracking
    size_to_text_heights: dict[LabelSize, list[float]] = {}
    files_per_size: Counter[LabelSize] = Counter()
    if reports:
        for report in reports:
            label_size = (
                list(report.label_sizes.keys())[0] if report.label_sizes else None
            )
            if label_size:
                if label_size not in size_to_text_heights:
                    size_to_text_heights[label_size] = []
                size_to_text_heights[label_size].append(report.text_height_in)
                # Count PDF files per size
                num_files = (
                    len(report.output_pdf_files) if report.output_pdf_files else 1
                )
                files_per_size[label_size] += num_files

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        if include_costs:
            fieldnames = [
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

                # Get text height for this size (use first one if multiple)
                text_heights = size_to_text_heights.get(size, [2.0])
                text_height = text_heights[0] if text_heights else 2.0

                row = {
                    "Label Size (WxH)": format_label_size(size),
                    "Text Height (in)": f"{text_height:.2f}",
                    "Files": files_per_size.get(size, 0),
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
                            "Unit Price ($)": f"{cb.unit_price / count:.2f}"
                            if count > 0
                            else "",
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
            total_files = sum(files_per_size.values())
            total_row = {
                "Label Size (WxH)": "TOTAL",
                "Text Height (in)": "",  # Non-summable
                "Files": total_files,
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
            # Customer CSV: configurable based on customer_report_config
            fieldnames = ["Label Size (WxH)", "Files", "Labels"]

            # Map config keys to column names
            config_map = {
                "text_height_in": "Text Height (in)",
                "substrate_sq_ft": "Substrate (sq ft)",
                "label_area_sq_ft": "Label Area (sq ft)",
                "ink_sq_ft": "Ink (sq ft)",
                "substrate_cost": "Substrate Cost ($)",
                "ink_cost": "Ink Cost ($)",
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
            else:
                # Default customer columns if no config provided
                fieldnames.extend(["Price ($)", "Unit Price ($)"])

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

                # Get text height for this size (use first one if multiple)
                text_heights = size_to_text_heights.get(size, [2.0])
                text_height = text_heights[0] if text_heights else 2.0

                row = {
                    "Label Size (WxH)": format_label_size(size),
                    "Files": files_per_size.get(size, 0),
                    "Labels": count,
                }

                # Add optional columns based on configuration
                optional_values = {
                    "Text Height (in)": f"{text_height:.2f}",
                    "Substrate (sq ft)": f"{substrate_sqft:.2f}",
                    "Label Area (sq ft)": f"{label_area_sqft:.2f}",
                    "Ink (sq ft)": f"{ink_sqft:.2f}",
                }

                # Add optional values to row
                for key, value in optional_values.items():
                    if key in fieldnames:
                        row[key] = value

                # Add cost-related columns if configured
                if area.cost_breakdown:
                    cost_values = {
                        "Substrate Cost ($)": f"{area.cost_breakdown.substrate_cost:.2f}",
                        "Ink Cost ($)": f"{area.cost_breakdown.ink_cost:.2f}",
                        "Printer Hours": f"{area.cost_breakdown.printer_hours:.2f}",
                        "Printer Cost ($)": f"{area.cost_breakdown.printer_cost:.2f}",
                        "Labor Hours": f"{area.cost_breakdown.labor_hours:.2f}",
                        "Labor Cost ($)": f"{area.cost_breakdown.labor_cost:.2f}",
                        "Total Cost ($)": f"{area.cost_breakdown.total_cost:.2f}",
                        "Price ($)": f"{area.cost_breakdown.unit_price:.2f}",
                        "Unit Price ($)": f"{area.cost_breakdown.unit_price / count:.2f}"
                        if count > 0
                        else "",
                    }
                    for key, value in cost_values.items():
                        if key in fieldnames:
                            row[key] = value

                    # Accumulate costs
                    total_substrate_cost += area.cost_breakdown.substrate_cost
                    total_ink_cost += area.cost_breakdown.ink_cost
                    total_printer_hours += area.cost_breakdown.printer_hours
                    total_printer_cost += area.cost_breakdown.printer_cost
                    total_labor_hours += area.cost_breakdown.labor_hours
                    total_labor_cost += area.cost_breakdown.labor_cost
                    total_cost += area.cost_breakdown.total_cost
                    total_price += area.cost_breakdown.unit_price
                else:
                    # Empty costs if no cost breakdown
                    cost_keys = [
                        k
                        for k in [
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
                        if k in fieldnames
                    ]
                    for key in cost_keys:
                        row[key] = ""

                total_labels += count
                total_substrate_sqft += substrate_sqft
                total_label_area_sqft += label_area_sqft
                total_ink_sqft += ink_sqft

                writer.writerow(row)

            # Write TOTAL row
            total_files = sum(files_per_size.values())
            total_row = {
                "Label Size (WxH)": "TOTAL",
                "Files": total_files,
                "Labels": total_labels,
            }

            # Add optional columns to TOTAL row
            for key in fieldnames[3:]:  # Skip Label Size, Files, and Labels
                if key == "Text Height (in)":
                    total_row[key] = ""  # Non-summable
                elif key == "Substrate (sq ft)":
                    total_row[key] = f"{total_substrate_sqft:.2f}"
                elif key == "Label Area (sq ft)":
                    total_row[key] = f"{total_label_area_sqft:.2f}"
                elif key == "Ink (sq ft)":
                    total_row[key] = f"{total_ink_sqft:.2f}"
                elif key == "Substrate Cost ($)":
                    total_row[key] = f"{total_substrate_cost:.2f}"
                elif key == "Ink Cost ($)":
                    total_row[key] = f"{total_ink_cost:.2f}"
                elif key == "Printer Hours":
                    total_row[key] = f"{total_printer_hours:.2f}"
                elif key == "Printer Cost ($)":
                    total_row[key] = f"{total_printer_cost:.2f}"
                elif key == "Labor Hours":
                    total_row[key] = f"{total_labor_hours:.2f}"
                elif key == "Labor Cost ($)":
                    total_row[key] = f"{total_labor_cost:.2f}"
                elif key == "Total Cost ($)":
                    total_row[key] = f"{total_cost:.2f}"
                elif key == "Price ($)":
                    total_row[key] = f"{total_price:.2f}"
                elif key == "Unit Price ($)":
                    total_row[key] = ""  # Non-summable

            writer.writerow(total_row)


def write_job_breakdown_csv(
    reports: list[JobReport],
    directory: Path,
    output_path: Path,
    include_costs: bool = True,
    customer_report_config: dict[str, bool] | None = None,
) -> None:
    """Write job breakdown to a CSV file."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        if include_costs:
            fieldnames = [
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
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            # Accumulators for TOTAL row
            total_labels = 0
            total_substrate_sqft = 0.0
            total_linear_feet = 0.0
            total_label_area_sqft = 0.0
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
                input_file = display_input_file(report.input_file, directory.parent)
                # Format label sizes as "WxH" (e.g., "10x4, 12x4")
                label_size_str = (
                    ", ".join(
                        format_label_size(size)
                        for size in sorted(report.label_sizes.keys())
                    )
                    if report.label_sizes
                    else ""
                )
                substrate_sqft = sq_ft(gm.total_substrate_sq_in)
                label_area_sqft = sq_ft(gm.total_label_material_sq_in)
                num_files = (
                    len(report.output_pdf_files) if report.output_pdf_files else 1
                )
                row = {
                    "Input File": input_file,
                    "Label Size (WxH)": label_size_str,
                    "Text Height (in)": f"{report.text_height_in:.2f}",
                    "Files": num_files,
                    "Labels": gm.total_output_labels,
                    "Substrate (sq ft)": f"{substrate_sqft:.2f}",
                    "Label Area (sq ft)": f"{label_area_sqft:.2f}",
                    "Ink (sq in)": f"{gm.total_ink_area_sq_in:.2f}",
                    "Ink (sq ft)": f"{sq_ft(gm.total_ink_area_sq_in):.2f}",
                }
                # Accumulate numeric values
                total_labels += gm.total_output_labels
                total_substrate_sqft += substrate_sqft
                total_linear_feet += gm.linear_feet
                total_label_area_sqft += label_area_sqft
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
                            "Unit Price ($)": f"{cb.unit_price / gm.total_output_labels:.2f}"
                            if gm.total_output_labels > 0
                            else "",
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
            total_files = sum(
                len(report.output_pdf_files) if report.output_pdf_files else 1
                for report in reports
            )
            total_row = {
                "Input File": "TOTAL",
                "Label Size (WxH)": "",  # Non-summable
                "Text Height (in)": "",  # Non-summable
                "Files": total_files,
                "Labels": total_labels,
                "Substrate (sq ft)": f"{total_substrate_sqft:.2f}",
                "Label Area (sq ft)": f"{total_label_area_sqft:.2f}",
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
            # Customer CSV: configurable columns based on customer_report_config
            # Always include Input File, Files, and Labels
            fieldnames = ["Input File", "Files", "Labels"]

            # Map config keys to column names and extract configuration
            config_map = {
                "label_size_wxh": "Label Size (WxH)",
                "text_height_in": "Text Height (in)",
                "substrate_sq_ft": "Substrate (sq ft)",
                "linear_feet": "Linear Feet",
                "label_area_sq_ft": "Label Area (sq ft)",
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
            else:
                # Default customer columns if no config provided
                fieldnames.extend(["Label Area (sq ft)", "Price ($)", "Unit Price ($)"])

            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            # Accumulators for TOTAL row
            total_labels = 0
            total_substrate_sqft = 0.0
            total_linear_feet = 0.0
            total_label_area_sqft = 0.0
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
                input_file = display_input_file(report.input_file, directory.parent)
                substrate_sqft = sq_ft(gm.total_substrate_sq_in)
                label_area_sqft = sq_ft(gm.total_label_material_sq_in)
                num_files = (
                    len(report.output_pdf_files) if report.output_pdf_files else 1
                )

                row = {
                    "Input File": input_file,
                    "Files": num_files,
                    "Labels": gm.total_output_labels,
                }

                # Add optional columns based on configuration
                label_size_str = (
                    ", ".join(
                        format_label_size(size)
                        for size in sorted(report.label_sizes.keys())
                    )
                    if report.label_sizes
                    else ""
                )

                optional_values = {
                    "Label Size (WxH)": label_size_str,
                    "Text Height (in)": f"{report.text_height_in:.2f}",
                    "Substrate (sq ft)": f"{substrate_sqft:.2f}",
                    "Linear Feet": f"{gm.linear_feet:.2f}",
                    "Label Area (sq ft)": f"{label_area_sqft:.2f}",
                    "Ink (sq in)": f"{gm.total_ink_area_sq_in:.2f}",
                    "Ink (sq ft)": f"{sq_ft(gm.total_ink_area_sq_in):.2f}",
                }

                # Add optional values to row
                for key, value in optional_values.items():
                    if key in fieldnames:
                        row[key] = value

                # Add cost-related columns if configured
                if gm.cost_breakdown:
                    cost_values = {
                        "Ink Cost ($)": f"{gm.cost_breakdown.ink_cost:.2f}",
                        "Substrate Cost ($)": f"{gm.cost_breakdown.substrate_cost:.2f}",
                        "Printer Hours": f"{gm.cost_breakdown.printer_hours:.2f}",
                        "Printer Cost ($)": f"{gm.cost_breakdown.printer_cost:.2f}",
                        "Labor Hours": f"{gm.cost_breakdown.labor_hours:.2f}",
                        "Labor Cost ($)": f"{gm.cost_breakdown.labor_cost:.2f}",
                        "Total Cost ($)": f"{gm.cost_breakdown.total_cost:.2f}",
                        "Price ($)": f"{gm.cost_breakdown.unit_price:.2f}",
                        "Unit Price ($)": f"{gm.cost_breakdown.unit_price / gm.total_output_labels:.2f}"
                        if gm.total_output_labels > 0
                        else "",
                    }
                    for key, value in cost_values.items():
                        if key in fieldnames:
                            row[key] = value

                    # Accumulate costs
                    total_ink_cost += gm.cost_breakdown.ink_cost
                    total_substrate_cost += gm.cost_breakdown.substrate_cost
                    total_printer_hours += gm.cost_breakdown.printer_hours
                    total_printer_cost += gm.cost_breakdown.printer_cost
                    total_labor_hours += gm.cost_breakdown.labor_hours
                    total_labor_cost += gm.cost_breakdown.labor_cost
                    total_cost += gm.cost_breakdown.total_cost
                    total_price += gm.cost_breakdown.unit_price
                else:
                    # Empty costs if no cost breakdown
                    cost_keys = [
                        k
                        for k in [
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
                        if k in fieldnames
                    ]
                    for key in cost_keys:
                        row[key] = ""

                total_labels += gm.total_output_labels
                total_substrate_sqft += substrate_sqft
                total_label_area_sqft += label_area_sqft
                total_ink_sq_in += gm.total_ink_area_sq_in
                total_linear_feet += gm.linear_feet

                writer.writerow(row)

            # Write TOTAL row
            total_files = sum(
                len(report.output_pdf_files) if report.output_pdf_files else 1
                for report in reports
            )
            total_row = {
                "Input File": "TOTAL",
                "Files": total_files,
                "Labels": total_labels,
            }

            # Add optional columns to TOTAL row
            for key in fieldnames[3:]:  # Skip Input File, Files, and Labels
                if key == "Label Size (WxH)":
                    total_row[key] = ""  # Non-summable
                elif key == "Text Height (in)":
                    total_row[key] = ""  # Non-summable
                elif key == "Substrate (sq ft)":
                    total_row[key] = f"{total_substrate_sqft:.2f}"
                elif key == "Linear Feet":
                    total_row[key] = f"{total_linear_feet:.2f}"
                elif key == "Label Area (sq ft)":
                    total_row[key] = f"{total_label_area_sqft:.2f}"
                elif key == "Ink (sq in)":
                    total_row[key] = f"{total_ink_sq_in:.2f}"
                elif key == "Ink (sq ft)":
                    total_row[key] = f"{sq_ft(total_ink_sq_in):.2f}"
                elif key == "Ink Cost ($)":
                    total_row[key] = f"{total_ink_cost:.2f}"
                elif key == "Substrate Cost ($)":
                    total_row[key] = f"{total_substrate_cost:.2f}"
                elif key == "Printer Hours":
                    total_row[key] = f"{total_printer_hours:.2f}"
                elif key == "Printer Cost ($)":
                    total_row[key] = f"{total_printer_cost:.2f}"
                elif key == "Labor Hours":
                    total_row[key] = f"{total_labor_hours:.2f}"
                elif key == "Labor Cost ($)":
                    total_row[key] = f"{total_labor_cost:.2f}"
                elif key == "Total Cost ($)":
                    total_row[key] = f"{total_cost:.2f}"
                elif key == "Price ($)":
                    total_row[key] = f"{total_price:.2f}"
                elif key == "Unit Price ($)":
                    total_row[key] = ""  # Non-summable

            writer.writerow(total_row)


def write_pdf_file_breakdown_csv(
    pdf_records: list[PdfFileRecord],
    output_path: Path,
    include_costs: bool = True,
    customer_report_config: dict[str, bool] | None = None,
) -> None:
    """Write PDF file breakdown to a CSV file.

    If include_costs is True, includes full cost breakdown (for internal use).
    If False, uses customer_report_config to determine which columns to include.
    """
    if not pdf_records:
        return

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        if include_costs:
            fieldnames = [
                "Filename",
                "Label Size (WxH)",
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
                "Unit Price ($)",
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            # Accumulators for TOTAL row
            total_labels = 0
            total_substrate_sqft = 0.0
            total_label_area_sqft = 0.0
            total_ink_sq_in = 0.0
            total_ink_cost = 0.0
            total_substrate_cost = 0.0
            total_printer_hours = 0.0
            total_printer_cost = 0.0
            total_labor_hours = 0.0
            total_labor_cost = 0.0
            total_cost = 0.0

            for record in sorted(pdf_records, key=lambda r: r.filename):
                substrate_sqft = sq_ft(record.total_substrate_sq_in)
                label_area_sqft = sq_ft(record.total_label_material_sq_in)
                ink_sqft = sq_ft(record.total_ink_area_sq_in)

                row = {
                    "Filename": record.filename,
                    "Label Size (WxH)": record.label_size,
                    "Labels": record.total_output_labels,
                    "Substrate (sq ft)": f"{substrate_sqft:.2f}",
                    "Label Area (sq ft)": f"{label_area_sqft:.2f}",
                    "Ink (sq in)": f"{record.total_ink_area_sq_in:.2f}",
                    "Ink (sq ft)": f"{ink_sqft:.2f}",
                }

                # Accumulate numeric values
                total_labels += record.total_output_labels
                total_substrate_sqft += substrate_sqft
                total_label_area_sqft += label_area_sqft
                total_ink_sq_in += record.total_ink_area_sq_in

                if record.cost_breakdown:
                    cb = record.cost_breakdown
                    row.update(
                        {
                            "Ink Cost ($)": f"{cb.ink_cost:.2f}",
                            "Substrate Cost ($)": f"{cb.substrate_cost:.2f}",
                            "Printer Hours": f"{cb.printer_hours:.2f}",
                            "Printer Cost ($)": f"{cb.printer_cost:.2f}",
                            "Labor Hours": f"{cb.labor_hours:.2f}",
                            "Labor Cost ($)": f"{cb.labor_cost:.2f}",
                            "Total Cost ($)": f"{cb.total_cost:.2f}",
                            "Unit Price ($)": f"{cb.unit_price:.2f}",
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
                            "Unit Price ($)": "",
                        }
                    )
                writer.writerow(row)

            # Write TOTAL row
            total_ink_sqft = sq_ft(total_ink_sq_in)
            total_row = {
                "Filename": "TOTAL",
                "Label Size (WxH)": "",  # Non-summable
                "Labels": total_labels,
                "Substrate (sq ft)": f"{total_substrate_sqft:.2f}",
                "Label Area (sq ft)": f"{total_label_area_sqft:.2f}",
                "Ink (sq in)": f"{total_ink_sq_in:.2f}",
                "Ink (sq ft)": f"{total_ink_sqft:.2f}",
                "Ink Cost ($)": f"{total_ink_cost:.2f}",
                "Substrate Cost ($)": f"{total_substrate_cost:.2f}",
                "Printer Hours": f"{total_printer_hours:.2f}",
                "Printer Cost ($)": f"{total_printer_cost:.2f}",
                "Labor Hours": f"{total_labor_hours:.2f}",
                "Labor Cost ($)": f"{total_labor_cost:.2f}",
                "Total Cost ($)": f"{total_cost:.2f}",
                "Unit Price ($)": "",  # Non-summable
            }
            writer.writerow(total_row)
        else:
            # Customer CSV: configurable based on customer_report_config
            fieldnames = ["Filename", "Labels"]

            # Map config keys to column names
            config_map = {
                "label_size_wxh": "Label Size (WxH)",
                "substrate_sq_ft": "Substrate (sq ft)",
                "label_area_sq_ft": "Label Area (sq ft)",
                "ink_sq_in": "Ink (sq in)",
                "ink_sq_ft": "Ink (sq ft)",
                "ink_cost": "Ink Cost ($)",
                "substrate_cost": "Substrate Cost ($)",
                "printer_hours": "Printer Hours",
                "printer_cost": "Printer Cost ($)",
                "labor_hours": "Labor Hours",
                "labor_cost": "Labor Cost ($)",
                "total_cost": "Total Cost ($)",
                "unit_price": "Unit Price ($)",
            }

            # Build fieldnames based on customer_report_config
            if customer_report_config:
                for config_key, col_name in config_map.items():
                    if customer_report_config.get(config_key, False):
                        fieldnames.append(col_name)
            else:
                # Default customer columns if no config provided
                fieldnames.extend(["Label Size (WxH)", "Unit Price ($)"])

            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            # Accumulators for TOTAL row
            total_labels = 0
            total_substrate_sqft = 0.0
            total_label_area_sqft = 0.0
            total_ink_sq_in = 0.0
            total_ink_cost = 0.0
            total_substrate_cost = 0.0
            total_printer_hours = 0.0
            total_printer_cost = 0.0
            total_labor_hours = 0.0
            total_labor_cost = 0.0
            total_cost = 0.0

            for record in sorted(pdf_records, key=lambda r: r.filename):
                substrate_sqft = sq_ft(record.total_substrate_sq_in)
                label_area_sqft = sq_ft(record.total_label_material_sq_in)
                ink_sqft = sq_ft(record.total_ink_area_sq_in)

                row = {
                    "Filename": record.filename,
                    "Labels": record.total_output_labels,
                }

                # Add optional columns based on configuration
                optional_values = {
                    "Label Size (WxH)": record.label_size,
                    "Substrate (sq ft)": f"{substrate_sqft:.2f}",
                    "Label Area (sq ft)": f"{label_area_sqft:.2f}",
                    "Ink (sq in)": f"{record.total_ink_area_sq_in:.2f}",
                    "Ink (sq ft)": f"{ink_sqft:.2f}",
                }

                # Add optional values to row
                for key, value in optional_values.items():
                    if key in fieldnames:
                        row[key] = value

                # Add cost-related columns if configured
                if record.cost_breakdown:
                    cost_values = {
                        "Ink Cost ($)": f"{record.cost_breakdown.ink_cost:.2f}",
                        "Substrate Cost ($)": f"{record.cost_breakdown.substrate_cost:.2f}",
                        "Printer Hours": f"{record.cost_breakdown.printer_hours:.2f}",
                        "Printer Cost ($)": f"{record.cost_breakdown.printer_cost:.2f}",
                        "Labor Hours": f"{record.cost_breakdown.labor_hours:.2f}",
                        "Labor Cost ($)": f"{record.cost_breakdown.labor_cost:.2f}",
                        "Total Cost ($)": f"{record.cost_breakdown.total_cost:.2f}",
                        "Unit Price ($)": f"{record.cost_breakdown.unit_price:.2f}",
                    }
                    for key, value in cost_values.items():
                        if key in fieldnames:
                            row[key] = value

                    # Accumulate costs
                    total_ink_cost += record.cost_breakdown.ink_cost
                    total_substrate_cost += record.cost_breakdown.substrate_cost
                    total_printer_hours += record.cost_breakdown.printer_hours
                    total_printer_cost += record.cost_breakdown.printer_cost
                    total_labor_hours += record.cost_breakdown.labor_hours
                    total_labor_cost += record.cost_breakdown.labor_cost
                    total_cost += record.cost_breakdown.total_cost
                else:
                    # Empty costs if no cost breakdown
                    cost_keys = [
                        k
                        for k in [
                            "Ink Cost ($)",
                            "Substrate Cost ($)",
                            "Printer Hours",
                            "Printer Cost ($)",
                            "Labor Hours",
                            "Labor Cost ($)",
                            "Total Cost ($)",
                            "Unit Price ($)",
                        ]
                        if k in fieldnames
                    ]
                    for key in cost_keys:
                        row[key] = ""

                total_labels += record.total_output_labels
                total_substrate_sqft += substrate_sqft
                total_label_area_sqft += label_area_sqft
                total_ink_sq_in += record.total_ink_area_sq_in

                writer.writerow(row)

            # Write TOTAL row
            total_ink_sqft = sq_ft(total_ink_sq_in)
            total_row = {
                "Filename": "TOTAL",
                "Labels": total_labels,
            }

            # Add optional columns to TOTAL row
            for key in fieldnames[2:]:  # Skip Filename and Labels
                if key == "Label Size (WxH)":
                    total_row[key] = ""  # Non-summable
                elif key == "Substrate (sq ft)":
                    total_row[key] = f"{total_substrate_sqft:.2f}"
                elif key == "Label Area (sq ft)":
                    total_row[key] = f"{total_label_area_sqft:.2f}"
                elif key == "Ink (sq in)":
                    total_row[key] = f"{total_ink_sq_in:.2f}"
                elif key == "Ink (sq ft)":
                    total_row[key] = f"{total_ink_sqft:.2f}"
                elif key == "Ink Cost ($)":
                    total_row[key] = f"{total_ink_cost:.2f}"
                elif key == "Substrate Cost ($)":
                    total_row[key] = f"{total_substrate_cost:.2f}"
                elif key == "Printer Hours":
                    total_row[key] = f"{total_printer_hours:.2f}"
                elif key == "Printer Cost ($)":
                    total_row[key] = f"{total_printer_cost:.2f}"
                elif key == "Labor Hours":
                    total_row[key] = f"{total_labor_hours:.2f}"
                elif key == "Labor Cost ($)":
                    total_row[key] = f"{total_labor_cost:.2f}"
                elif key == "Total Cost ($)":
                    total_row[key] = f"{total_cost:.2f}"
                elif key == "Unit Price ($)":
                    total_row[key] = ""  # Non-summable

            writer.writerow(total_row)


def write_pdf_file_breakdown_vendor_csv(
    pdf_records: list[PdfFileRecord],
    output_path: Path,
) -> None:
    """Write vendor-friendly PDF file breakdown to a CSV file.

    Includes file number (sequential), label dimensions, labels count,
    substrate and ink metrics. No filename or price columns for vendor simplicity.
    Records are sorted by (label_size, filename) for consistent ordering.
    """
    if not pdf_records:
        return

    # Sort by label size first, then filename
    sorted_records = sorted(pdf_records, key=lambda r: (r.label_size, r.filename))

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "File #",
            "Label Size (WxH)",
            "Labels",
            "Substrate (sq ft)",
            "Label Area (sq ft)",
            "Ink (sq in)",
            "Ink (sq ft)",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        total_substrate = 0.0
        total_label_area = 0.0
        total_ink_sq_in = 0.0
        total_ink_sq_ft = 0.0
        total_labels = 0

        for file_num, record in enumerate(sorted_records, start=1):
            substrate_sq_ft = sq_ft(record.total_substrate_sq_in)
            label_area_sq_ft = sq_ft(record.total_label_material_sq_in)
            ink_sq_ft = sq_ft(record.total_ink_area_sq_in)

            writer.writerow(
                {
                    "File #": file_num,
                    "Label Size (WxH)": record.label_size,
                    "Labels": record.total_output_labels,
                    "Substrate (sq ft)": f"{substrate_sq_ft:.2f}",
                    "Label Area (sq ft)": f"{label_area_sq_ft:.2f}",
                    "Ink (sq in)": f"{record.total_ink_area_sq_in:.2f}",
                    "Ink (sq ft)": f"{ink_sq_ft:.2f}",
                }
            )

            total_substrate += substrate_sq_ft
            total_label_area += label_area_sq_ft
            total_ink_sq_in += record.total_ink_area_sq_in
            total_ink_sq_ft += ink_sq_ft
            total_labels += record.total_output_labels

        # Write totals row
        writer.writerow(
            {
                "File #": "",
                "Label Size (WxH)": "TOTAL",
                "Labels": total_labels,
                "Substrate (sq ft)": f"{total_substrate:.2f}",
                "Label Area (sq ft)": f"{total_label_area:.2f}",
                "Ink (sq in)": f"{total_ink_sq_in:.2f}",
                "Ink (sq ft)": f"{total_ink_sq_ft:.2f}",
            }
        )


def write_per_label_breakdown_csv(
    labels: list[ConsolidatedLabelBySize],
    output_path: Path,
    include_costs: bool = True,
    customer_report_config: dict[str, bool] | None = None,
) -> None:
    """Write per-label breakdown to a CSV file.

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
            total_substrate_sq_in = 0.0
            total_ink_cost = 0.0
            total_substrate_cost = 0.0
            total_printer_hours = 0.0
            total_printer_cost = 0.0
            total_labor_hours = 0.0
            total_labor_cost = 0.0
            total_cost = 0.0
            total_price = 0.0

            for label in labels:
                # Calculate total label area (multiplied by copies)
                label_size_sq_in_per_copy = label.label_size[0] * label.label_size[1]
                label_total_size_sq_in = label_size_sq_in_per_copy * label.instances
                label_total_area_sqft = sq_ft(label_total_size_sq_in)
                # ink_area_sq_in is already total (multiplied by copies in consolidation)
                label_ink_sqft = sq_ft(label.ink_area_sq_in)
                row = {
                    "Label Code": label.text,
                    "Copies": label.instances,
                    "Label Size (WxH)": f"{label.label_size[0]:g}x{label.label_size[1]:g}",
                    "Label Size (sq in)": f"{label_total_size_sq_in:.2f}",
                    "Text Height (in)": f"{label.text_height_in:.2f}",
                    "Char Count": label.char_count,
                    "Scale": f"{label.horizontal_scale:.4f}",
                    "Label Area (sq ft)": f"{label_total_area_sqft:.4f}",
                    "Linear Feet": f"{label.linear_feet:.2f}",
                    "Ink (sq in)": f"{label.ink_area_sq_in:.2f}",
                    "Ink (sq ft)": f"{label_ink_sqft:.4f}",
                }
                # Accumulate numeric values
                total_copies += label.instances
                total_char_count += label.char_count
                total_label_size_sq_in += label_total_size_sq_in
                total_label_area_sqft += label_total_area_sqft
                total_linear_feet += label.linear_feet
                total_substrate_sq_in += label.substrate_sq_in
                total_ink_sq_in += label.ink_area_sq_in
                total_ink_sqft += label_ink_sqft

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
                            "Unit Price ($)": f"{cb.unit_price / label.instances:.2f}"
                            if label.instances > 0
                            else "",
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
                "Label Size (sq in)": f"{total_label_size_sq_in:.2f}",
                "Text Height (in)": "",  # Non-summable
                "Char Count": total_char_count,
                "Scale": "",  # Non-summable
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
                        row["Label Size (WxH)"] = (
                            f"{label.label_size[0]:g}x{label.label_size[1]:g}"
                        )
                    if customer_report_config.get("text_height_in", False):
                        row["Text Height (in)"] = "2.00"
                    if customer_report_config.get("char_count", False):
                        row["Char Count"] = label.char_count
                        total_char_count += label.char_count
                    if customer_report_config.get("scale", False):
                        row["Scale"] = f"{label.horizontal_scale:.4f}"
                    if customer_report_config.get("label_size_sq_in", False):
                        label_size_sq_in_per_copy = (
                            label.label_size[0] * label.label_size[1]
                        )
                        label_total_size_sq_in = (
                            label_size_sq_in_per_copy * label.instances
                        )
                        row["Label Size (sq in)"] = f"{label_total_size_sq_in:.2f}"
                        total_label_size_sq_in += label_total_size_sq_in
                    if customer_report_config.get("label_area_sq_ft", False):
                        label_size_sq_in_per_copy = (
                            label.label_size[0] * label.label_size[1]
                        )
                        label_total_size_sq_in = (
                            label_size_sq_in_per_copy * label.instances
                        )
                        label_total_area_sqft = sq_ft(label_total_size_sq_in)
                        row["Label Area (sq ft)"] = f"{label_total_area_sqft:.4f}"
                        total_label_area_sqft += label_total_area_sqft
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

    # Organize output paths into subdirectories
    basename = "vinyl_labels_combined"
    organized_paths = organize_output_paths(directory, basename)

    # Override output path if provided, but still use organized subdirs for reports
    if args.output is not None:
        out_path: Path = args.output
    else:
        out_path = organized_paths["txt"]

    # Build skip set to avoid re-processing our own output
    skip_paths = {out_path.resolve(), organized_paths["json"].resolve()}
    skip = frozenset(skip_paths)

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
        metrics,
        sizes,
        size_areas,
        labels,
        reports,
        out_path,
        directory,
        organized_paths["json"],
    )

    # Write CSV reports
    write_size_breakdown_csv(
        sizes,
        size_areas,
        organized_paths["csv_size_breakdown"],
        include_costs=True,
        reports=reports,
    )
    write_size_breakdown_csv(
        sizes,
        size_areas,
        organized_paths["csv_size_breakdown_customer"],
        include_costs=False,
        reports=reports,
        customer_report_config=pricing_config.customer_report
        if pricing_config
        else None,
    )
    write_job_breakdown_csv(
        reports,
        directory,
        organized_paths["csv_job_breakdown"],
        include_costs=True,
    )
    write_job_breakdown_csv(
        reports,
        directory,
        organized_paths["csv_job_breakdown_customer"],
        include_costs=False,
        customer_report_config=pricing_config.customer_report
        if pricing_config
        else None,
    )
    write_per_label_report(
        reports,
        organized_paths["txt_labels"],
        directory,
        include_costs=True,
    )
    write_per_label_report(
        reports,
        organized_paths["txt_labels_customer"],
        directory,
        include_costs=False,
    )
    write_per_label_report_xlsx(
        reports,
        organized_paths["txt_labels_xlsx"],
        directory,
        include_costs=True,
    )
    write_per_label_report_xlsx(
        reports,
        organized_paths["txt_labels_customer_xlsx"],
        directory,
        include_costs=False,
        customer_report_config=pricing_config.customer_report
        if pricing_config
        else None,
    )
    # Write PDF file breakdown CSV
    pdf_records = build_pdf_file_records(reports)
    write_pdf_file_breakdown_csv(
        pdf_records,
        organized_paths["csv_file_breakdown"],
        include_costs=True,
    )
    write_pdf_file_breakdown_csv(
        pdf_records,
        organized_paths["csv_file_breakdown_customer"],
        include_costs=False,
        customer_report_config=pricing_config.customer_report
        if pricing_config
        else None,
    )
    write_pdf_file_breakdown_vendor_csv(
        pdf_records,
        organized_paths["csv_file_breakdown_vendor"],
    )

    # Copy PDF files to vendor directory with simplified numbering
    if pdf_records:
        # Sort by label size first, then filename (same as vendor report)
        sorted_records = sorted(pdf_records, key=lambda r: (r.label_size, r.filename))
        # Find all PDF files across all reports and create mapping
        pdf_path_map: dict[str, Path] = {}
        for report in reports:
            if report.output_pdf_files:
                for pdf_file in report.output_pdf_files:
                    pdf_path_map[pdf_file.name] = pdf_file
        # Copy files with new names
        vendor_dir = organized_paths["pdf_files_vendor"]
        for file_num, record in enumerate(sorted_records, start=1):
            src_path = pdf_path_map.get(record.filename)
            if src_path and src_path.exists():
                dst_path = vendor_dir / f"{file_num}.pdf"
                try:
                    shutil.copy2(src_path, dst_path)
                except OSError as e:
                    print(f"Warning: Could not copy {src_path} to {dst_path}: {e}")

    write_per_label_breakdown_csv(
        labels_by_size,
        organized_paths["csv_label_breakdown"],
        include_costs=True,
    )
    write_per_label_breakdown_csv(
        labels_by_size,
        organized_paths["csv_label_breakdown_customer"],
        include_costs=False,
        customer_report_config=pricing_config.customer_report
        if pricing_config
        else None,
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
