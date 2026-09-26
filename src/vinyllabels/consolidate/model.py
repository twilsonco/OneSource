"""Value objects for the consolidated view of many jobs.

``JobReport`` is the input side (one parsed ``*_report.json``); the rest are the
aggregates the report writers render. Field order is part of the JSON contract,
since these dataclasses are serialised with ``dataclasses.asdict``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from vinyllabels.models import CostBreakdown, GlobalMetrics, LabelMetrics, PdfMetrics
from vinyllabels.sizes import LabelSize

__all__ = [
    "ConsolidatedLabel",
    "ConsolidatedLabelBySize",
    "ConsolidatedMetrics",
    "JobReport",
    "PdfFileRecord",
    "SizeAreas",
]


@dataclass(frozen=True)
class JobReport:
    """One parsed ``*_report.json`` file, plus the metadata consolidation needs."""

    path: Path
    input_file: str
    page_width_in: float
    text_height_in: float
    copies_per_label: int
    global_metrics: GlobalMetrics
    label_sizes: Counter[LabelSize]
    per_label: list[LabelMetrics]
    output_pdf_files: list[Path] | None = None
    per_pdf_metrics: list[PdfMetrics] | None = None

    @property
    def num_pdf_files(self) -> int:
        """PDF files this job emitted, defaulting to one when unrecorded."""
        return len(self.output_pdf_files) if self.output_pdf_files else 1

    @property
    def label_size(self) -> LabelSize | None:
        """The job's design size.

        A job prints a single design size, so its size counter has one entry;
        ``None`` when the report carried no sizes at all.
        """
        for size in self.label_sizes:
            return size
        return None

    @property
    def yield_ratio(self) -> float:
        """Substrate actually consumed per unit of label material.

        Substrate is bought by the roll and includes the waste between labels, so
        a label's share of the roll is its own area scaled back by this ratio.
        """
        gm = self.global_metrics
        if gm.total_label_material_sq_in <= 0:
            return 1.0
        return gm.total_substrate_sq_in / gm.total_label_material_sq_in


@dataclass(frozen=True)
class PdfFileRecord:
    """A single PDF file with its aggregated metrics."""

    filename: str
    total_output_labels: int
    total_characters: int
    total_ink_area_sq_in: float
    total_label_material_sq_in: float
    total_substrate_sq_in: float = 0.0
    label_size: str = ""
    linear_feet: float = 0.0
    cost_breakdown: CostBreakdown | None = None


@dataclass(frozen=True)
class ConsolidatedMetrics:
    """Totals summed across every job report.

    Percentages are recomputed from the summed areas rather than averaged, and
    ``linear_feet`` sums each job's own roll length so jobs with different page
    widths consolidate correctly.
    """

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
    jobs: int
    instances: int
    char_count: int
    ink_area_sq_in: float
    size_w_in: float = 0.0
    size_h_in: float = 0.0
    substrate_sq_in: float = 0.0
    cost_breakdown: CostBreakdown | None = None

    @property
    def label_area_sq_in(self) -> float:
        """Design area of every printed instance of this code."""
        return self.size_w_in * self.size_h_in * self.instances


@dataclass(frozen=True)
class ConsolidatedLabelBySize:
    """A single label code at one size, summed across jobs.

    Keyed by ``(text, size)`` so the same code printed at two sizes appears
    twice, each with its own metrics.
    """

    text: str
    label_size: LabelSize
    instances: int
    char_count: int
    ink_area_sq_in: float
    substrate_sq_in: float = 0.0
    horizontal_scale: float = 1.0
    linear_feet: float = 0.0
    text_height_in: float = 2.0
    cost_breakdown: CostBreakdown | None = None

    @property
    def label_area_sq_in(self) -> float:
        """Design area of every printed instance at this size."""
        return self.label_size[0] * self.label_size[1] * self.instances


@dataclass(frozen=True)
class SizeAreas:
    """Area totals (sq inches) accumulated for one label size across jobs."""

    substrate_sq_in: float = 0.0
    label_material_sq_in: float = 0.0
    ink_area_sq_in: float = 0.0
    linear_feet: float = 0.0
    cost_breakdown: CostBreakdown | None = None
