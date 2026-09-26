"""Tests for :mod:`vinyllabels.consolidate.load`."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from vinyllabels.consolidate.load import (
    CONSOLIDATED_MARKER,
    is_consolidated_report,
    load_report,
    load_reports,
)
from vinyllabels.reportio.loaders import ReportLoadError

WriteReportJson = Callable[..., Path]


def test_load_report_full_shape(
    tmp_path: Path, write_report_json: WriteReportJson
) -> None:
    path = write_report_json(
        tmp_path,
        input_file="jobs/a.txt",
        page_width_in=44.0,
        copies_per_label=3,
        labels=("ALPHA", "B"),
        ink_area_sq_in=2.0,
        substrate_sq_in=400.0,
    )
    report = load_report(path)

    assert report.path == path
    assert report.input_file == "jobs/a.txt"
    assert report.page_width_in == 44.0
    assert report.text_height_in == 2.0
    assert report.copies_per_label == 3
    assert report.global_metrics.total_output_labels == 6
    assert report.global_metrics.total_characters == 18
    assert report.global_metrics.total_ink_area_sq_in == pytest.approx(12.0)
    assert report.global_metrics.total_substrate_sq_in == pytest.approx(400.0)
    assert report.label_sizes == {(8.0, 3.0): 6}
    assert [m.text for m in report.per_label] == ["ALPHA", "B"]
    assert report.per_label[0].char_count == 5
    assert report.output_pdf_files is None
    assert report.per_pdf_metrics is None
    assert report.num_pdf_files == 1


def test_load_report_without_label_sizes_derives_them(
    tmp_path: Path, write_report_json: WriteReportJson
) -> None:
    path = write_report_json(
        tmp_path, label_width_in=6.0, label_height_in=2.0, total_output_labels=9
    )
    report = load_report(path)
    assert report.label_sizes == {(6.0, 2.0): 9}


def test_load_report_with_multiple_label_sizes(
    tmp_path: Path, write_report_json: WriteReportJson
) -> None:
    path = write_report_json(
        tmp_path,
        label_sizes=[
            {"width_in": 8.0, "height_in": 3.0, "labels": 4},
            {"width_in": 4.0, "height_in": 4.0, "labels": 2},
        ],
    )
    report = load_report(path)
    assert report.label_sizes == {(8.0, 3.0): 4, (4.0, 4.0): 2}


def test_load_report_defaults_text_height_for_old_reports(
    tmp_path: Path, write_report_json: WriteReportJson
) -> None:
    path = write_report_json(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload["job"], dict)
    del payload["job"]["text_height_in"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_report(path).text_height_in == 2.0


def test_load_report_keeps_an_explicit_text_height(
    tmp_path: Path, write_report_json: WriteReportJson
) -> None:
    path = write_report_json(tmp_path, text_height_in=1.25)
    assert load_report(path).text_height_in == 1.25


def test_load_report_output_pdf_as_string(
    tmp_path: Path, write_report_json: WriteReportJson
) -> None:
    path = write_report_json(tmp_path, output_pdf="/tmp/a.pdf")
    assert load_report(path).output_pdf_files == [Path("/tmp/a.pdf")]


def test_load_report_output_pdf_as_array(
    tmp_path: Path, write_report_json: WriteReportJson
) -> None:
    path = write_report_json(tmp_path, output_pdf=["/tmp/a.pdf", "/tmp/b.pdf"])
    report = load_report(path)
    assert report.output_pdf_files == [Path("/tmp/a.pdf"), Path("/tmp/b.pdf")]
    assert report.num_pdf_files == 2


def test_load_report_ignores_non_string_pdf_entries(
    tmp_path: Path, write_report_json: WriteReportJson
) -> None:
    path = write_report_json(tmp_path, output_pdf=[1, "/tmp/a.pdf"])
    assert load_report(path).output_pdf_files == [Path("/tmp/a.pdf")]


def test_load_report_rejects_a_non_string_pdf_entry_type(
    tmp_path: Path, write_report_json: WriteReportJson
) -> None:
    path = write_report_json(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["job"]["output_pdf"] = 5
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_report(path).output_pdf_files is None


def test_load_report_reads_per_pdf_metrics(
    tmp_path: Path, write_report_json: WriteReportJson
) -> None:
    path = write_report_json(
        tmp_path,
        output_pdf=["/tmp/a.pdf", "/tmp/b.pdf"],
        per_pdf=[
            {
                "total_output_labels": 2,
                "total_characters": 10,
                "total_ink_area_sq_in": 4.0,
                "total_label_material_sq_in": 48.0,
                "cost_breakdown": None,
            },
            {
                "total_output_labels": 2,
                "total_characters": 2,
                "total_ink_area_sq_in": 0.5,
                "total_label_material_sq_in": 48.0,
                "cost_breakdown": None,
            },
        ],
    )
    report = load_report(path)
    assert report.per_pdf_metrics is not None
    assert [m.total_output_labels for m in report.per_pdf_metrics] == [2, 2]
    assert report.per_pdf_metrics[1].total_characters == 2


def test_load_report_with_costs(
    tmp_path: Path, write_report_json: WriteReportJson
) -> None:
    path = write_report_json(
        tmp_path,
        cost_breakdown={
            "ink_cost": 1.0,
            "substrate_cost": 2.0,
            "printer_hours": 0.5,
            "printer_cost": 5.0,
            "labor_hours": 0.25,
            "labor_cost": 5.0,
            "total_cost": 13.0,
            "unit_price": 14.3,
        },
    )
    report = load_report(path)
    assert report.global_metrics.cost_breakdown is not None
    assert report.global_metrics.cost_breakdown.unit_price == pytest.approx(14.3)
    assert report.per_label[0].cost_breakdown is not None


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda p: p.pop("job"), "missing 'job'"),
        (lambda p: p.pop("global"), "missing 'global'"),
        (lambda p: p.pop("per_label"), "missing 'per_label'"),
        (lambda p: p.__setitem__("job", []), "'job' is not a JSON object"),
        (lambda p: p.__setitem__("per_label", {}), "'per_label' is not a JSON array"),
        (
            lambda p: p["global"].__setitem__("total_output_labels", "x"),
            "'total_output_labels' is not an integer",
        ),
        (
            lambda p: p["job"].__setitem__("input_file", 3),
            "'input_file' is not a string",
        ),
        (
            lambda p: p["per_label"].__setitem__(0, 5),
            "'per_label[0]' is not a JSON object",
        ),
        (
            lambda p: p.__setitem__("label_sizes", "nope"),
            "'label_sizes' is not a JSON array",
        ),
        (
            lambda p: p.__setitem__("label_sizes", [7]),
            "'label_sizes[0]' is not a JSON object",
        ),
    ],
)
def test_load_report_errors(
    tmp_path: Path,
    write_report_json: WriteReportJson,
    mutate: Callable[[dict[str, object]], None],
    fragment: str,
) -> None:
    path = write_report_json(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ReportLoadError, match=fragment.replace("[", r"\[")):
        load_report(path)


def test_load_report_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ReportLoadError, match="Could not read"):
        load_report(tmp_path / "absent_report.json")


def test_is_consolidated_report(
    tmp_path: Path, write_report_json: WriteReportJson
) -> None:
    path = write_report_json(tmp_path, report_type=CONSOLIDATED_MARKER)
    assert is_consolidated_report(path) is True
    plain = write_report_json(tmp_path, name="plain_report.json")
    assert is_consolidated_report(plain) is False


def test_is_consolidated_report_is_false_for_unreadable_files(tmp_path: Path) -> None:
    broken = tmp_path / "broken_report.json"
    broken.write_text("{oops", encoding="utf-8")
    assert is_consolidated_report(broken) is False
    assert is_consolidated_report(tmp_path / "absent.json") is False


def test_is_consolidated_report_with_a_non_dict_job(
    tmp_path: Path, write_report_json: WriteReportJson
) -> None:
    path = write_report_json(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["job"] = []
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert is_consolidated_report(path) is False


def test_load_reports_sorts_and_skips(
    tmp_path: Path, write_report_json: WriteReportJson
) -> None:
    second = write_report_json(tmp_path, name="b_report.json", input_file="b.txt")
    first = write_report_json(tmp_path, name="a_report.json", input_file="a.txt")
    write_report_json(tmp_path, name="z_report.json", report_type=CONSOLIDATED_MARKER)
    skipped = write_report_json(tmp_path, name="c_report.json", input_file="c.txt")

    reports = load_reports(tmp_path, frozenset({skipped.resolve()}))
    assert [r.path for r in reports] == [first, second]


def test_load_reports_raises_when_empty(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("nothing here", encoding="utf-8")
    with pytest.raises(ReportLoadError, match="No \\*_report.json files found"):
        load_reports(tmp_path)
