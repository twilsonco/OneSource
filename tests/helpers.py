"""Shared, non-fixture builders for the consolidation tests.

Kept out of ``conftest.py`` so the consolidation tests can import them directly
and so pytest never tries to collect them as tests.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path

from vinyllabels.consolidate.model import JobReport
from vinyllabels.layout import JobConfig
from vinyllabels.models import (
    CostBreakdown,
    GlobalMetrics,
    LabelMetrics,
    PdfMetrics,
    PricingConfig,
)

Size = tuple[float, float]

# Fixture signatures. They live here rather than in ``conftest.py`` so test
# modules can annotate their factory-typed parameters by importing a plain
# module instead of reaching into the conftest.
JobConfigFactory = Callable[..., JobConfig]
PricingConfigFactory = Callable[..., PricingConfig]
LabelFileFactory = Callable[..., Path]
ReportPayloadFactory = Callable[..., dict[str, object]]
WriteReportJsonFactory = Callable[..., Path]
StripTimestamps = Callable[[str], str]


def cost_breakdown(
    ink: float, substrate: float, printer: float, labor: float, price: float
) -> CostBreakdown:
    """A breakdown whose ``total_cost`` is the sum of its four money fields."""
    return CostBreakdown(
        ink_cost=ink,
        substrate_cost=substrate,
        printer_hours=0.5,
        printer_cost=printer,
        labor_hours=0.25,
        labor_cost=labor,
        total_cost=ink + substrate + printer + labor,
        unit_price=price,
    )


def label_metrics(
    text: str, ink: float, cost: CostBreakdown | None = None, scale: float = 1.0
) -> LabelMetrics:
    """One per-label metrics row, priced when ``cost`` is given."""
    return LabelMetrics(
        text=text,
        char_count=len(text),
        horizontal_scale=scale,
        ink_area_sq_in=ink,
        cost_breakdown=cost,
    )


def pdf_metrics(
    labels: int,
    chars: int,
    ink: float,
    material: float,
    cost: CostBreakdown | None = None,
) -> PdfMetrics:
    """One per-PDF metrics row."""
    return PdfMetrics(
        total_output_labels=labels,
        total_characters=chars,
        total_ink_area_sq_in=ink,
        total_label_material_sq_in=material,
        cost_breakdown=cost,
    )


def job_report(
    *,
    path: Path | None = None,
    input_file: str = "job.txt",
    page_width_in: float = 52.0,
    text_height_in: float = 2.0,
    copies: int = 2,
    size: Size | None = (8.0, 3.0),
    labels: Sequence[LabelMetrics] = (),
    substrate: float = 96.0,
    cost: CostBreakdown | None = None,
    pdfs: list[Path] | None = None,
    per_pdf: list[PdfMetrics] | None = None,
    sizes: Counter[Size] | None = None,
) -> JobReport:
    """A :class:`JobReport` with fully explicit numbers.

    ``total_label_material_sq_in`` is derived from the label count and copy
    count, so ``substrate`` alone controls the job's yield ratio.
    """
    count = len(labels) * copies
    material = (size[0] * size[1] * count) if size is not None else 0.0
    global_metrics = GlobalMetrics(
        total_output_labels=count,
        total_characters=sum(len(m.text) for m in labels) * copies,
        total_ink_area_sq_in=sum(m.ink_area_sq_in for m in labels) * copies,
        total_label_material_sq_in=material,
        total_substrate_sq_in=substrate,
        material_yield_pct=(material / substrate * 100.0) if substrate else 0.0,
        average_ink_coverage_pct=0.0,
        page_height_in=substrate / page_width_in,
        linear_feet=substrate / page_width_in / 12.0,
        cost_breakdown=cost,
    )
    default_sizes: Counter[Size] = Counter({size: count} if size else {})
    return JobReport(
        path=path or Path(f"{input_file}_report.json"),
        input_file=input_file,
        page_width_in=page_width_in,
        text_height_in=text_height_in,
        copies_per_label=copies,
        global_metrics=global_metrics,
        label_sizes=sizes if sizes is not None else default_sizes,
        per_label=list(labels),
        output_pdf_files=pdfs,
        per_pdf_metrics=per_pdf,
    )
