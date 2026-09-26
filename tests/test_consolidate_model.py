"""Tests for :mod:`vinyllabels.consolidate.model`."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest

from vinyllabels.consolidate.model import (
    ConsolidatedLabel,
    ConsolidatedLabelBySize,
    ConsolidatedMetrics,
    JobReport,
    PdfFileRecord,
    SizeAreas,
)
from vinyllabels.models import CostBreakdown, GlobalMetrics


def metrics(material: float, substrate: float) -> GlobalMetrics:
    return GlobalMetrics(
        total_output_labels=4,
        total_characters=8,
        total_ink_area_sq_in=1.0,
        total_label_material_sq_in=material,
        total_substrate_sq_in=substrate,
        material_yield_pct=0.0,
        average_ink_coverage_pct=0.0,
        page_height_in=6.0,
        linear_feet=1.0,
    )


def test_num_pdf_files_defaults_to_one(
    make_job_report: Callable[..., JobReport],
) -> None:
    assert make_job_report(output_pdf_files=None).num_pdf_files == 1
    assert (
        make_job_report(output_pdf_files=[Path("a.pdf"), Path("b.pdf")]).num_pdf_files
        == 2
    )


def test_label_size_is_the_single_key(
    make_job_report: Callable[..., JobReport],
) -> None:
    report = make_job_report(label_size=(8.0, 3.0))
    assert report.label_size == (8.0, 3.0)


def test_label_size_is_none_when_unrecorded(
    make_job_report: Callable[..., JobReport],
) -> None:
    report = make_job_report(label_size=None)
    assert report.label_size is None


def test_yield_ratio_divides_substrate_by_material(
    make_job_report: Callable[..., JobReport],
) -> None:
    report = make_job_report()
    report = JobReport(
        path=report.path,
        input_file=report.input_file,
        page_width_in=report.page_width_in,
        text_height_in=report.text_height_in,
        copies_per_label=report.copies_per_label,
        global_metrics=metrics(24.0, 96.0),
        label_sizes=report.label_sizes,
        per_label=report.per_label,
    )
    assert report.yield_ratio == pytest.approx(4.0)


def test_yield_ratio_defaults_to_one_without_material(
    make_job_report: Callable[..., JobReport],
) -> None:
    report = make_job_report()
    report = JobReport(
        path=report.path,
        input_file=report.input_file,
        page_width_in=report.page_width_in,
        text_height_in=report.text_height_in,
        copies_per_label=report.copies_per_label,
        global_metrics=metrics(0.0, 0.0),
        label_sizes=Counter(),
        per_label=[],
    )
    assert report.yield_ratio == 1.0


def test_consolidated_label_area_multiplies_instances() -> None:
    label = ConsolidatedLabel(
        text="A",
        jobs=1,
        instances=3,
        char_count=3,
        ink_area_sq_in=1.0,
        size_w_in=8.0,
        size_h_in=3.0,
    )
    assert label.label_area_sq_in == pytest.approx(72.0)
    assert label.substrate_sq_in == 0.0
    assert label.cost_breakdown is None


def test_consolidated_label_by_size_area() -> None:
    label = ConsolidatedLabelBySize(
        text="A", label_size=(4.0, 2.0), instances=5, char_count=5, ink_area_sq_in=1.0
    )
    assert label.label_area_sq_in == pytest.approx(40.0)
    assert label.horizontal_scale == 1.0
    assert label.text_height_in == 2.0


def test_pdf_file_record_defaults() -> None:
    record = PdfFileRecord(
        filename="a.pdf",
        total_output_labels=1,
        total_characters=2,
        total_ink_area_sq_in=0.5,
        total_label_material_sq_in=24.0,
    )
    assert record.total_substrate_sq_in == 0.0
    assert record.label_size == ""
    assert record.linear_feet == 0.0
    assert record.cost_breakdown is None


def test_size_areas_defaults() -> None:
    areas = SizeAreas()
    assert (areas.substrate_sq_in, areas.label_material_sq_in) == (0.0, 0.0)
    assert areas.ink_area_sq_in == 0.0
    assert areas.linear_feet == 0.0
    assert areas.cost_breakdown is None


def test_consolidated_metrics_defaults_to_no_pricing() -> None:
    metrics_ = ConsolidatedMetrics(
        total_jobs=1,
        total_output_labels=1,
        total_characters=1,
        total_ink_area_sq_in=1.0,
        total_label_material_sq_in=1.0,
        total_substrate_sq_in=1.0,
        material_yield_pct=100.0,
        average_ink_coverage_pct=100.0,
        linear_feet=1.0,
    )
    assert metrics_.cost_breakdown is None


def test_cost_breakdown_field_is_preserved() -> None:
    areas = SizeAreas(cost_breakdown=CostBreakdown.zero())
    assert areas.cost_breakdown == CostBreakdown.zero()
