"""Shared fixtures for the ``vinyllabels`` test suite.

Everything here is hermetic: fixtures only ever write under ``tmp_path`` (or
``tmp_path_factory`` directories), never touch ``data/``, and never read the
repository's real ``pricing-config.json``. Tests that exercise the CLIs pass an
explicit ``--pricing-config`` or ``monkeypatch.chdir(tmp_path)`` so the upward
config search cannot escape the temporary tree.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

import pytest

from vinyllabels.consolidate.model import JobReport
from vinyllabels.fonts import ARIAL_BOLD_NAME, register_bold_font
from vinyllabels.layout import JobConfig
from vinyllabels.models import CostBreakdown, GlobalMetrics, LabelMetrics, PricingConfig

from helpers import (
    JobConfigFactory,
    LabelFileFactory,
    PricingConfigFactory,
    ReportPayloadFactory,
    StripTimestamps,
    WriteReportJsonFactory,
)

#: A pricing config whose numbers are chosen so expected values stay hand-checkable.
DEFAULT_PRICING: Final[dict[str, float]] = {
    "ink_cost_usd_per_sqft": 1.0,
    "substrate_cost_usd_per_sqft": 2.0,
    "print_rate_hours_per_sqft": 0.5,
    "printer_run_cost_usd_per_hour": 10.0,
    "labor_rate_usd_per_hour": 20.0,
    "labor_time_factor": 0.5,
    "markup_percent": 10.0,
}

# A minimal but real TrueType font, built by fontTools so the suite never
# depends on a font being installed on the host.
_FONT_GLYPHS: Final[dict[str, tuple[tuple[int, int], ...]]] = {
    "A": ((100, 0), (500, 700), (900, 0)),
    "B": ((100, 0), (100, 700), (900, 700), (900, 0)),
}


def _build_font(path: Path) -> None:
    """Write a tiny two-glyph TTF (unitsPerEm 1000) to ``path``."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    glyph_order = [".notdef", *sorted(_FONT_GLYPHS)]
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(glyph_order)
    builder.setupCharacterMap(
        {ord(char): name for char, name in zip("AB", sorted(_FONT_GLYPHS))}
    )

    glyphs: dict[str, object] = {".notdef": TTGlyphPen(None).glyph()}
    for name, points in _FONT_GLYPHS.items():
        pen = TTGlyphPen(None)
        pen.moveTo(points[0])
        for point in points[1:]:
            pen.lineTo(point)
        pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(
        {".notdef": (500, 0), **{name: (1000, 100) for name in _FONT_GLYPHS}}
    )
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "TestSans", "styleName": "Bold"})
    builder.setupOS2(sTypoAscender=800, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    builder.save(str(path))


@pytest.fixture(scope="session")
def synthetic_font_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Path to a generated TTF that both fontTools and ReportLab accept."""
    path = tmp_path_factory.mktemp("fonts") / "TestSans-Bold.ttf"
    _build_font(path)
    return path


@pytest.fixture
def font_name(synthetic_font_path: Path) -> str:
    """A uniquely named ReportLab registration of the generated TTF.

    ReportLab caches registered faces by name, so the tests use their own name
    rather than :data:`vinyllabels.fonts.ARIAL_BOLD_NAME`; that keeps the glyph
    metrics deterministic no matter which font an earlier test registered.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont as RLTTFont

    name = "TestSansBold"
    pdfmetrics.registerFont(RLTTFont(name, str(synthetic_font_path)))
    return name


@pytest.fixture
def bold_font_name(synthetic_font_path: Path) -> str:
    """The production font name, registered from the generated TTF."""
    name, registered = register_bold_font(synthetic_font_path)
    assert name == ARIAL_BOLD_NAME
    assert registered == str(synthetic_font_path)
    return name


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test from inside its own temporary directory.

    ``find_pricing_config`` walks upward from ``Path.cwd()``, which would
    otherwise pick up the repository's real ``pricing-config.json``. Tests that
    need a different working directory still call ``monkeypatch.chdir`` themselves.
    """
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def make_job_config(tmp_path: Path) -> JobConfigFactory:
    """``JobConfig`` factory rooted in ``tmp_path``."""

    def factory(**overrides: object) -> JobConfig:
        values: dict[str, object] = {"input_path": tmp_path / "labels.txt"}
        values.update(overrides)
        return JobConfig(**values)  # type: ignore[arg-type]

    return factory


@pytest.fixture
def make_pricing_config() -> PricingConfigFactory:
    """``PricingConfig`` factory with hand-checkable defaults."""

    def factory(**overrides: object) -> PricingConfig:
        values: dict[str, object] = dict(DEFAULT_PRICING)
        values.update(overrides)
        return PricingConfig(**values)  # type: ignore[arg-type]

    return factory


@pytest.fixture
def make_cost_breakdown() -> Callable[..., CostBreakdown]:
    """``CostBreakdown`` factory with hand-checkable defaults."""

    def factory(
        ink_cost: float = 1.0,
        substrate_cost: float = 2.0,
        printer_hours: float = 0.5,
        printer_cost: float = 5.0,
        labor_hours: float = 0.25,
        labor_cost: float = 5.0,
        total_cost: float = 13.0,
        unit_price: float = 14.3,
    ) -> CostBreakdown:
        return CostBreakdown(
            ink_cost=ink_cost,
            substrate_cost=substrate_cost,
            printer_hours=printer_hours,
            printer_cost=printer_cost,
            labor_hours=labor_hours,
            labor_cost=labor_cost,
            total_cost=total_cost,
            unit_price=unit_price,
        )

    return factory


@pytest.fixture
def label_file(tmp_path: Path) -> LabelFileFactory:
    """Write a label-code list file under ``tmp_path`` and return its path."""

    def factory(
        *labels: str,
        name: str = "labels.txt",
        header: str | None = None,
    ) -> Path:
        lines: list[str] = []
        if header is not None:
            lines.append(header)
        lines.extend(labels)
        path = tmp_path / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    return factory


@pytest.fixture
def report_payload() -> ReportPayloadFactory:
    """Build a ``*_report.json`` payload matching what ``generate`` writes."""

    def factory(
        *,
        input_file: str = "labels.txt",
        page_width_in: float = 52.0,
        label_width_in: float = 8.0,
        label_height_in: float = 3.0,
        text_height_in: float = 2.0,
        copies_per_label: int = 2,
        labels: Sequence[str] = ("ALPHA", "BETA"),
        total_output_labels: int | None = None,
        ink_area_sq_in: float = 4.0,
        substrate_sq_in: float = 240.0,
        cost_breakdown: dict[str, float] | None = None,
        label_sizes: list[dict[str, float | int]] | None = None,
        per_pdf: list[dict[str, object]] | None = None,
        output_pdf: str | list[str] | None = None,
        report_type: str | None = None,
    ) -> dict[str, object]:
        count = (
            total_output_labels
            if total_output_labels is not None
            else len(labels) * copies_per_label
        )
        material_sq_in = label_width_in * label_height_in * count
        breakdown = dict(cost_breakdown) if cost_breakdown is not None else None

        per_label: list[dict[str, object]] = [
            {
                "text": text,
                "char_count": len(text),
                "horizontal_scale": 1.0,
                "ink_area_sq_in": ink_area_sq_in,
                "cost_breakdown": dict(breakdown) if breakdown else None,
            }
            for text in labels
        ]

        job: dict[str, object] = {
            "generated": "2026-01-01 00:00:00",
            "input_file": input_file,
            "page_width_in": page_width_in,
            "label_width_in": label_width_in,
            "label_height_in": label_height_in,
            "text_height_in": text_height_in,
            "copies_per_label": copies_per_label,
        }
        if output_pdf is not None:
            job["output_pdf"] = output_pdf
        if report_type is not None:
            job["report_type"] = report_type

        global_section: dict[str, object] = {
            "total_output_labels": count,
            "total_characters": sum(len(text) for text in labels) * copies_per_label,
            "total_ink_area_sq_in": ink_area_sq_in * count,
            "total_label_material_sq_in": material_sq_in,
            "total_substrate_sq_in": substrate_sq_in,
            "material_yield_pct": (
                material_sq_in / substrate_sq_in * 100.0 if substrate_sq_in else 0.0
            ),
            "average_ink_coverage_pct": (
                ink_area_sq_in * count / material_sq_in * 100.0
                if material_sq_in
                else 0.0
            ),
            "page_height_in": substrate_sq_in / page_width_in,
            "linear_feet": substrate_sq_in / page_width_in / 12.0,
            "cost_breakdown": dict(breakdown) if breakdown else None,
        }

        payload: dict[str, object] = {
            "job": job,
            "global": global_section,
            "per_label": per_label,
        }
        if label_sizes is not None:
            payload["label_sizes"] = label_sizes
        elif total_output_labels is None:
            payload["label_sizes"] = [
                {
                    "width_in": label_width_in,
                    "height_in": label_height_in,
                    "labels": count,
                }
            ]
        if per_pdf is not None:
            payload["per_pdf"] = per_pdf
        return payload

    return factory


@pytest.fixture
def write_report_json(report_payload: ReportPayloadFactory) -> WriteReportJsonFactory:
    """Write a synthetic ``*_report.json`` and return its path."""

    def factory(
        directory: Path,
        name: str = "labels_report.json",
        **overrides: object,
    ) -> Path:
        payload = report_payload(**overrides)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    return factory


@pytest.fixture
def make_job_report() -> Callable[..., JobReport]:
    """Build a :class:`JobReport` in memory for the consolidation tests."""

    def factory(
        *,
        path: Path | None = None,
        input_file: str = "labels.txt",
        page_width_in: float = 52.0,
        text_height_in: float = 2.0,
        copies_per_label: int = 2,
        label_size: tuple[float, float] | None = (8.0, 3.0),
        labels: Sequence[LabelMetrics] | None = None,
        global_metrics: GlobalMetrics | None = None,
        output_pdf_files: list[Path] | None = None,
        per_pdf_metrics: object = None,
    ) -> JobReport:
        from collections import Counter

        from vinyllabels.sizes import LabelSize

        sizes: dict[LabelSize, int] = {}
        if label_size is not None:
            sizes[label_size] = len(labels) if labels else 0
        if global_metrics is None:
            count = sum(sizes.values())
            material = (
                label_size[0] * label_size[1] * count if label_size is not None else 0.0
            )
            global_metrics = GlobalMetrics(
                total_output_labels=count,
                total_characters=sum(len(m.text) for m in (labels or ()))
                * copies_per_label,
                total_ink_area_sq_in=sum(m.ink_area_sq_in for m in (labels or ()))
                * copies_per_label,
                total_label_material_sq_in=material,
                total_substrate_sq_in=material * 2.0 if material else 0.0,
                material_yield_pct=50.0 if material else 0.0,
                average_ink_coverage_pct=0.0,
                page_height_in=6.0,
                linear_feet=0.5,
            )
        return JobReport(
            path=path or Path(f"{input_file}_report.json"),
            input_file=input_file,
            page_width_in=page_width_in,
            text_height_in=text_height_in,
            copies_per_label=copies_per_label,
            global_metrics=global_metrics,
            label_sizes=Counter(sizes),
            per_label=list(labels or []),
            output_pdf_files=output_pdf_files,
            per_pdf_metrics=per_pdf_metrics,  # type: ignore[arg-type]
        )

    return factory


@pytest.fixture
def strip_generated() -> StripTimestamps:
    """Drop the timestamped ``Generated:`` line so two runs compare equal."""

    def strip(text: str) -> str:
        return "\n".join(
            line for line in text.splitlines() if not line.startswith("Generated:")
        )

    return strip
