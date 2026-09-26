"""Tests for :mod:`vinyllabels.drawing`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from vinyllabels.drawing import (
    PT_PER_IN,
    border_edges,
    build_pdf,
    draw_label,
    draw_label_borders,
    draw_sheet_separators,
    pdf_paths_for,
)
from vinyllabels.layout import JobConfig
from vinyllabels.models import LabelMetrics

BUILTIN_FONT = "Helvetica-Bold"


def canvas(tmp_path: Path, name: str = "c.pdf") -> Canvas:
    return Canvas(str(tmp_path / name), pagesize=(1000, 1000))


def test_pt_per_in() -> None:
    assert PT_PER_IN == 72.0


def test_border_edges_of_a_single_label() -> None:
    edges = border_edges([(0.0, 0.0)], 8.0, 3.0)
    assert len(edges) == 4
    assert sorted(edges.values()) == sorted(
        [
            (0.0, 0.0, 0.0, 3.0),
            (8.0, 0.0, 8.0, 3.0),
            (0.0, 0.0, 8.0, 0.0),
            (0.0, 3.0, 8.0, 3.0),
        ]
    )
    assert {key[0] for key in edges} == {"v", "h"}


def test_border_edges_deduplicate_a_shared_edge() -> None:
    single = border_edges([(0.0, 0.0)], 8.0, 3.0)
    pair = border_edges([(0.0, 0.0), (8.0, 0.0)], 8.0, 3.0)
    assert len(single) == 4
    # Two adjacent labels: 8 raw edges collapse to 7 unique ones.
    assert len(pair) == 7


def test_border_edges_keep_disjoint_labels_apart() -> None:
    edges = border_edges([(0.0, 0.0), (20.0, 0.0)], 8.0, 3.0)
    assert len(edges) == 8


def test_border_edges_keys_are_rounded_so_drift_collapses() -> None:
    exact = border_edges([(0.0, 0.0), (8.0, 0.0)], 8.0, 3.0)
    drifted = border_edges([(0.0, 0.0), (8.0 + 1e-9, 0.0)], 8.0, 3.0)
    assert len(drifted) == len(exact)


def test_border_edges_of_no_positions() -> None:
    assert border_edges([], 8.0, 3.0) == {}


def test_draw_label_borders_with_no_positions_is_a_noop(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    draw_label_borders(canvas(tmp_path), [], make_job_config())


def test_draw_label_borders_accepts_a_real_canvas(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    out = tmp_path / "borders.pdf"
    c = Canvas(str(out), pagesize=(1000, 1000))
    draw_label_borders(c, [(0.0, 0.0), (8.0, 0.0)], make_job_config())
    c.showPage()
    c.save()
    assert out.stat().st_size > 0


def test_draw_label_borders_vertical_footprint(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    c = canvas(tmp_path)
    draw_label_borders(c, [(0.0, 0.0)], make_job_config(vertical_labels=True))


def test_draw_sheet_separators_vertical_gaps(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    c = canvas(tmp_path)
    lines: list[tuple[float, float, float, float]] = []
    c.line = lambda *args: lines.append(args)  # type: ignore[method-assign]
    config = make_job_config(labels_per_sheet_row=2, labels_per_sheet_col=2)
    draw_sheet_separators(c, config, 26.0)
    # Three 16in sheets across a 52in page leave two 2in gaps, each bisected.
    verticals = [line for line in lines if line[0] == line[2]]
    horizontals = [line for line in lines if line[1] == line[3]]
    assert [line[0] for line in verticals] == [17.0 * PT_PER_IN, 35.0 * PT_PER_IN]
    # 26in page, 6in sheets, 2in gaps: three gaps sit above the bottom margin.
    assert [line[1] for line in horizontals] == [
        18.0 * PT_PER_IN,
        10.0 * PT_PER_IN,
        2.0 * PT_PER_IN,
    ]


def test_draw_sheet_separators_single_sheet_row(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    c = canvas(tmp_path)
    lines: list[tuple[float, float, float, float]] = []
    c.line = lambda *args: lines.append(args)  # type: ignore[method-assign]
    draw_sheet_separators(c, make_job_config(labels_per_sheet_col=0), 26.0)
    assert lines == []


def test_draw_sheet_separators_stops_at_the_bottom_margin(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    c = canvas(tmp_path)
    lines: list[tuple[float, float, float, float]] = []
    c.line = lambda *args: lines.append(args)  # type: ignore[method-assign]
    config = make_job_config(labels_per_sheet_row=6, labels_per_sheet_col=8)
    draw_sheet_separators(c, config, 26.0)
    assert lines == []


def test_draw_sheet_separators_with_zero_gap(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    c = canvas(tmp_path)
    lines: list[tuple[float, float, float, float]] = []
    c.line = lambda *args: lines.append(args)  # type: ignore[method-assign]
    config = make_job_config(
        labels_per_sheet_row=2, labels_per_sheet_col=2, vertical_gap_in=0.0
    )
    draw_sheet_separators(c, config, 26.0)
    assert all(line[0] == line[2] for line in lines)


def test_draw_label_writes_text(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    c = canvas(tmp_path)
    draw_label(c, "HELLO", 1.0, 1.0, BUILTIN_FONT, 1.0, make_job_config())


def test_draw_label_vertical(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    c = canvas(tmp_path)
    draw_label(
        c, "HELLO", 1.0, 1.0, BUILTIN_FONT, 0.5, make_job_config(vertical_labels=True)
    )


def test_pdf_paths_single_page(make_job_config: Callable[..., JobConfig]) -> None:
    config = make_job_config()
    paths = pdf_paths_for(Path("/tmp/out.pdf"), [(0, 23)], config)
    assert [p.name for p in paths] == ["out_8x3_24-labels.pdf"]


def test_pdf_paths_multiple_parts(make_job_config: Callable[..., JobConfig]) -> None:
    config = make_job_config()
    paths = pdf_paths_for(Path("/tmp/out.pdf"), [(0, 11), (12, 23)], config)
    assert [p.name for p in paths] == [
        "out_8x3_part-001_12-labels.pdf",
        "out_8x3_part-002_12-labels.pdf",
    ]


def test_build_pdf_without_instances(
    tmp_path: Path, make_job_config: Callable[..., JobConfig]
) -> None:
    config = make_job_config(font_path=None)
    assert build_pdf([], tmp_path / "out.pdf", [], config) == ([], [])


def test_build_pdf_creates_a_readable_pdf(
    tmp_path: Path,
    make_job_config: Callable[..., JobConfig],
    synthetic_font_path: Path,
) -> None:
    out = tmp_path / "out.pdf"
    config = make_job_config(font_path=synthetic_font_path, copies_per_label=2)
    per_label = [
        LabelMetrics(
            text="HELLO", char_count=5, horizontal_scale=1.0, ink_area_sq_in=1.0
        )
    ]
    paths, heights = build_pdf(["HELLO"], out, per_label, config)

    assert len(paths) == 1
    assert paths[0].is_file()
    # Two instances fill one row of the 8-row sheet, which gets trimmed away.
    assert heights == [pytest.approx(5.0)]

    reader = PdfReader(str(paths[0]))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    assert "HELLO" in text
    assert text.count("HELLO") == 2


def test_build_pdf_honours_explicit_page_breaks(
    tmp_path: Path,
    make_job_config: Callable[..., JobConfig],
    synthetic_font_path: Path,
) -> None:
    out = tmp_path / "out.pdf"
    config = make_job_config(
        font_path=synthetic_font_path,
        copies_per_label=1,
        labels_per_sheet_row=2,
        labels_per_sheet_col=2,
    )
    per_label = [
        LabelMetrics(
            text=t, char_count=len(t), horizontal_scale=1.0, ink_area_sq_in=1.0
        )
        for t in ("AAA", "BBB")
    ]
    paths, heights = build_pdf(
        ["AAA", "BBB"], out, per_label, config, page_break_ranges=[(0, 0), (1, 1)]
    )
    assert len(paths) == 2
    assert len(heights) == 2
    assert all(path.is_file() for path in paths)
    assert PdfReader(str(paths[0])).pages[0].extract_text().strip() == "AAA"
    assert PdfReader(str(paths[1])).pages[0].extract_text().strip() == "BBB"


def test_build_pdf_computes_breaks_from_the_config(
    tmp_path: Path,
    make_job_config: Callable[..., JobConfig],
    synthetic_font_path: Path,
) -> None:
    out = tmp_path / "out.pdf"
    config = make_job_config(
        font_path=synthetic_font_path,
        copies_per_label=1,
        labels_per_sheet_col=0,
        soft_page_height_in=5.0,
    )
    labels = ["AAA", "BBB"] * 4
    per_label = [
        LabelMetrics(text=t, char_count=3, horizontal_scale=1.0, ink_area_sq_in=1.0)
        for t in ("AAA", "BBB")
    ]
    paths, _heights = build_pdf(labels, out, per_label, config)
    assert len(paths) > 1
    assert all(path.name.startswith("out_8x3_part-") for path in paths)


def test_build_pdf_without_borders_or_separators(
    tmp_path: Path,
    make_job_config: Callable[..., JobConfig],
    synthetic_font_path: Path,
) -> None:
    out = tmp_path / "out.pdf"
    config = make_job_config(
        font_path=synthetic_font_path,
        draw_border=False,
        draw_sheet_separators=False,
        copies_per_label=1,
    )
    per_label = [
        LabelMetrics(text="AAA", char_count=3, horizontal_scale=1.0, ink_area_sq_in=1.0)
    ]
    paths, _ = build_pdf(["AAA"], out, per_label, config)
    assert PdfReader(str(paths[0])).pages[0].extract_text().strip() == "AAA"
