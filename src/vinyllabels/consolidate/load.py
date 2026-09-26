"""Reading ``*_report.json`` files into :class:`~vinyllabels.consolidate.model.JobReport`.

Reports are written by ``generate`` but consumed long afterwards, possibly by a
different version of this tool, so loading is deliberately tolerant of the
optional fields added over time (``label_sizes``, ``per_pdf``, multi-PDF
``output_pdf``, ``text_height_in``) while still failing loudly on anything the
consolidation math actually depends on.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from vinyllabels.consolidate.model import JobReport
from vinyllabels.models import GlobalMetrics, LabelMetrics, PdfMetrics
from vinyllabels.reportio.loaders import (
    ReportLoadError,
    as_entry_list,
    as_float,
    as_int,
    as_list,
    as_section,
    as_str,
    cost_breakdown_from_json,
    load_json_object,
)
from vinyllabels.sizes import LabelSize

__all__ = ["is_consolidated_report", "load_report", "load_reports"]

CONSOLIDATED_MARKER = "consolidated"

# Assumed when a report predates the ``text_height_in`` field.
_DEFAULT_TEXT_HEIGHT_IN = 2.0


def _load_global(section: dict[str, object], path: Path) -> GlobalMetrics:
    """Build a :class:`GlobalMetrics` from a report's ``global`` section."""
    return GlobalMetrics(
        total_output_labels=as_int(section, "total_output_labels", path),
        total_characters=as_int(section, "total_characters", path),
        total_ink_area_sq_in=as_float(section, "total_ink_area_sq_in", path),
        total_label_material_sq_in=as_float(
            section, "total_label_material_sq_in", path
        ),
        total_substrate_sq_in=as_float(section, "total_substrate_sq_in", path),
        material_yield_pct=as_float(section, "material_yield_pct", path),
        average_ink_coverage_pct=as_float(section, "average_ink_coverage_pct", path),
        page_height_in=as_float(section, "page_height_in", path),
        linear_feet=as_float(section, "linear_feet", path),
        cost_breakdown=cost_breakdown_from_json(section),
    )


def _load_per_label(entries: list[object], path: Path) -> list[LabelMetrics]:
    """Build the ``per_label`` list from a report's raw entries."""
    return [
        LabelMetrics(
            text=as_str(item, "text", path),
            char_count=as_int(item, "char_count", path),
            horizontal_scale=as_float(item, "horizontal_scale", path),
            ink_area_sq_in=as_float(item, "ink_area_sq_in", path),
            cost_breakdown=cost_breakdown_from_json(item),
        )
        for item in as_entry_list(entries, "per_label", path)
    ]


def _load_per_pdf(entries: list[object], path: Path) -> list[PdfMetrics]:
    """Build the ``per_pdf`` list from a report's raw entries."""
    return [
        PdfMetrics(
            total_output_labels=as_int(item, "total_output_labels", path),
            total_characters=as_int(item, "total_characters", path),
            total_ink_area_sq_in=as_float(item, "total_ink_area_sq_in", path),
            total_label_material_sq_in=as_float(
                item, "total_label_material_sq_in", path
            ),
            cost_breakdown=cost_breakdown_from_json(item),
        )
        for item in as_entry_list(entries, "per_pdf", path)
    ]


def _load_label_sizes(
    report: dict[str, object],
    job: dict[str, object],
    global_metrics: GlobalMetrics,
    path: Path,
) -> Counter[LabelSize]:
    """Return the report's printed-label counts keyed by label size.

    Current reports carry a ``label_sizes`` array. Reports written before that
    field existed are derived from the job's single design size instead.
    """
    raw = report.get("label_sizes")
    if raw is None:
        return Counter(
            {
                (
                    as_float(job, "label_width_in", path),
                    as_float(job, "label_height_in", path),
                ): global_metrics.total_output_labels
            }
        )
    if not isinstance(raw, list):
        raise ReportLoadError(f"{path}: 'label_sizes' is not a JSON array")

    sizes: Counter[LabelSize] = Counter()
    for item in as_entry_list(raw, "label_sizes", path):
        size: LabelSize = (
            as_float(item, "width_in", path),
            as_float(item, "height_in", path),
        )
        sizes[size] += as_int(item, "labels", path)
    return sizes


def _load_output_pdfs(job: dict[str, object]) -> list[Path] | None:
    """Return the job's emitted PDFs, accepting both the string and array forms."""
    raw = job.get("output_pdf")
    if raw is None:
        return None
    if isinstance(raw, str):
        return [Path(raw)]
    if isinstance(raw, list):
        return [Path(p) for p in raw if isinstance(p, str)]
    return None


def load_report(path: Path) -> JobReport:
    """Parse a single ``*_report.json`` file into a :class:`JobReport`.

    Raises :class:`ReportLoadError` when a field the consolidation depends on is
    missing or has the wrong type; optional sections are simply left empty.
    """
    report = load_json_object(path)
    job = as_section(report, "job", path)
    global_metrics = _load_global(as_section(report, "global", path), path)

    return JobReport(
        path=path,
        input_file=as_str(job, "input_file", path),
        page_width_in=as_float(job, "page_width_in", path),
        text_height_in=as_float(job, "text_height_in", path)
        if "text_height_in" in job
        else _DEFAULT_TEXT_HEIGHT_IN,
        copies_per_label=as_int(job, "copies_per_label", path),
        global_metrics=global_metrics,
        label_sizes=_load_label_sizes(report, job, global_metrics, path),
        per_label=_load_per_label(as_list(report, "per_label", path), path),
        output_pdf_files=_load_output_pdfs(job),
        per_pdf_metrics=_load_per_pdf(as_list(report, "per_pdf", path), path)
        if "per_pdf" in report
        else None,
    )


def is_consolidated_report(path: Path) -> bool:
    """Whether ``path`` is a consolidated report written by this tool.

    Consolidated reports carry ``job.report_type == "consolidated"``; skipping
    them keeps re-runs idempotent even when the output is named ``*_report``.
    """
    try:
        raw = load_json_object(path)
    except ReportLoadError:
        return False
    job = raw.get("job")
    return isinstance(job, dict) and job.get("report_type") == CONSOLIDATED_MARKER


def load_reports(
    directory: Path, skip: frozenset[Path] = frozenset()
) -> list[JobReport]:
    """Parse every ``*_report.json`` in ``directory``, sorted by name.

    ``skip`` holds already-resolved output paths, and files carrying the
    consolidated marker are ignored, so this tool's own reports are never folded
    into a later run.
    """
    paths = [
        p
        for p in sorted(directory.glob("*_report.json"))
        if p not in skip and not is_consolidated_report(p)
    ]
    if not paths:
        raise ReportLoadError(f"No *_report.json files found in {directory}")
    return [load_report(path) for path in paths]
