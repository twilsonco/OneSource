"""Consolidate WIC label job metrics from every ``*_report.json`` in a directory.

Reads all ``*_report.json`` files in the target directory (the machine-readable
reports emitted by ``scripts/wic_label_layout.py``) and writes one consolidated
text report covering the combined material yield and ink usage across all jobs,
in the same style as the per-job reports. A machine-readable JSON sibling with
the same data is written next to it, along with a per-job and a per-label
breakdown.

Percentages are recomputed from the summed areas (averaging the per-job
percentages would weight them wrongly), and the linear footage sums each job's
own roll length, so jobs with different page widths consolidate correctly.

Usage::

    uv run python scripts/wic_multi_job_report.py <directory>
    uv run python scripts/wic_multi_job_report.py <directory> -o out/combined.txt
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

# Make the sibling module importable whether this script is run directly
# (``python scripts/wic_multi_job_report.py``) or as a module
# (``python -m scripts.wic_multi_job_report``).
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from wic_label_layout import GlobalMetrics, LabelMetrics  # noqa: E402

# --- Input model ---------------------------------------------------------------


@dataclass(frozen=True)
class JobReport:
    """One parsed ``*_report.json`` file, plus the metadata this script needs."""

    path: Path
    input_file: str
    page_width_in: float
    copies_per_label: int
    global_metrics: GlobalMetrics
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


def _load_global(section: dict[str, object], path: Path) -> GlobalMetrics:
    """Build a :class:`GlobalMetrics` from a report's ``global`` section."""
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
    )


def _load_per_label(entries: list[object], path: Path) -> list[LabelMetrics]:
    """Build the ``per_label`` list from a report's raw entries."""
    labels: list[LabelMetrics] = []
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise SystemExit(f"{path}: 'per_label[{idx}]' is not a JSON object")
        item: dict[str, object] = {str(k): v for k, v in entry.items()}
        labels.append(
            LabelMetrics(
                text=_as_str(item, "text", path),
                char_count=_as_int(item, "char_count", path),
                horizontal_scale=_as_float(item, "horizontal_scale", path),
                ink_area_sq_in=_as_float(item, "ink_area_sq_in", path),
            )
        )
    return labels


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


def consolidate(reports: list[JobReport]) -> ConsolidatedMetrics:
    """Sum the per-job metrics into a single :class:`ConsolidatedMetrics`."""
    total_substrate = sum(r.global_metrics.total_substrate_sq_in for r in reports)
    total_material = sum(r.global_metrics.total_label_material_sq_in for r in reports)
    total_ink = sum(r.global_metrics.total_ink_area_sq_in for r in reports)
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
    )


def consolidate_labels(reports: list[JobReport]) -> list[ConsolidatedLabel]:
    """Merge every job's per-label metrics by code, scaled up to printed copies.

    Each report stores one instance's ink area and character count, so both are
    multiplied by that job's ``copies_per_label`` before summing.
    """
    jobs_seen: Counter[str] = Counter()
    instances: Counter[str] = Counter()
    ink_areas: dict[str, float] = {}
    for report in reports:
        for label in report.per_label:
            jobs_seen[label.text] += 1
            instances[label.text] += report.copies_per_label
            ink_areas[label.text] = ink_areas.get(label.text, 0.0) + (
                label.ink_area_sq_in * report.copies_per_label
            )
    return [
        ConsolidatedLabel(
            text=text,
            jobs=jobs_seen[text],
            instances=instances[text],
            char_count=len(text) * instances[text],
            ink_area_sq_in=ink_areas[text],
        )
        for text in sorted(ink_areas)
    ]


def roll_widths_in(reports: list[JobReport]) -> list[float]:
    """Return the distinct roll widths (inches) used across ``reports``, sorted."""
    return sorted({report.page_width_in for report in reports})


# --- Report generation ---------------------------------------------------------


def write_consolidated_report_json(
    metrics: ConsolidatedMetrics,
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
        "global": asdict(metrics),
        "jobs": [
            {
                "input_file": r.input_file,
                "report_file": str(r.path),
                "page_width_in": r.page_width_in,
                "copies_per_label": r.copies_per_label,
                "global": asdict(r.global_metrics),
            }
            for r in reports
        ],
        "per_label": [asdict(label) for label in labels],
    }
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def write_consolidated_report(
    metrics: ConsolidatedMetrics,
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
    lines.append("WIC LABEL PRINTING REPORT - CONSOLIDATED".center(80))
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
        f"   ({metrics.linear_feet:.2f} linear feet of {roll_desc} roll)"
    )
    lines.append(
        f"Total Label Area:          {metrics.total_label_material_sq_in:>10.2f} sq in"
    )
    lines.append(f"Material Yield:            {metrics.material_yield_pct:>10.2f} %")
    lines.append("")
    lines.append(subrule)
    lines.append("INK USAGE METRICS")
    lines.append(subrule)
    lines.append(
        f"Total Ink Area:            {metrics.total_ink_area_sq_in:>10.2f} sq in"
    )
    lines.append(
        f"Average Ink Coverage:      {metrics.average_ink_coverage_pct:>10.2f} %"
    )
    lines.append(f"Total Character Count:     {metrics.total_characters:>10d}")
    lines.append(f"Total Output Labels:       {metrics.total_output_labels:>10d}")
    lines.append("")
    lines.append(subrule)
    lines.append("JOB BREAKDOWN")
    lines.append(subrule)
    lines.append(
        f"{'Input File':<38} | {'Roll':>5} | {'Labels':>6} | {'Lin Ft':>7} | "
        f"{'Ink (sq in)':>11}"
    )
    lines.append(f"{'-' * 38}-+-{'-' * 5}-+-{'-' * 6}-+-{'-' * 7}-+-{'-' * 11}")
    for report in reports:
        gm = report.global_metrics
        lines.append(
            f"{report.input_file:<38} | {report.page_width_in:>5.0f} | "
            f"{gm.total_output_labels:>6d} | {gm.linear_feet:>7.2f} | "
            f"{gm.total_ink_area_sq_in:>11.2f}"
        )
    lines.append("")
    lines.append(subrule)
    lines.append("PER-TAG BREAKDOWN")
    lines.append(subrule)
    lines.append(
        f"{'Label Code':<16} | {'Jobs':>4} | {'Inst':>5} | {'Chars':>5} | "
        f"{'Ink Area (sq in)':>16}"
    )
    lines.append(f"{'-' * 16}-+-{'-' * 4}-+-{'-' * 5}-+-{'-' * 5}-+-{'-' * 18}")
    for label in labels:
        lines.append(
            f"{label.text:<16} | {label.jobs:>4d} | {label.instances:>5d} | "
            f"{label.char_count:>5d} | {label.ink_area_sq_in:>16.4f}"
        )
    lines.append(rule)
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")

    json_path = out_path.with_suffix(".json")
    write_consolidated_report_json(
        metrics, labels, reports, json_path, directory, timestamp
    )
    return json_path


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
        help="Directory containing *_report.json files from wic_label_layout.py.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=(
            "Path for the consolidated text report "
            "(default: <directory>/wic_jobs_combined.txt)."
        ),
    )
    args = parser.parse_args(argv)

    directory: Path = args.directory
    if not directory.is_dir():
        raise SystemExit(f"Not a directory: {directory}")

    out_path: Path = args.output or directory / "wic_jobs_combined.txt"
    skip = frozenset({out_path.resolve(), out_path.with_suffix(".json").resolve()})

    reports = load_reports(directory, skip)
    metrics = consolidate(reports)
    labels = consolidate_labels(reports)
    json_report_path = write_consolidated_report(
        metrics, labels, reports, out_path, directory
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
