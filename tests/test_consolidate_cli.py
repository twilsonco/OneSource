"""Tests for the ``consolidate`` command line entry point.

These drive :func:`vinyllabels.consolidate.cli.main` end to end over a temporary
directory of job reports, which is the only place the loaders, the merge maths,
every writer, and the vendor PDF drop folder are wired together.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from vinyllabels.consolidate import cli
from vinyllabels.consolidate.cli import BASENAME, copy_pdfs_for_vendor, main
from vinyllabels.consolidate.model import JobReport, PdfFileRecord
from vinyllabels.output_paths import consolidated_output_paths
from vinyllabels.pricing import PRICING_CONFIG_FILENAME
from vinyllabels.reportio.loaders import ReportLoadError

from helpers import WriteReportJsonFactory, job_report, pdf_metrics


def _directory(tmp_path: Path) -> Path:
    directory = tmp_path / "jobs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write_pdfs(*paths: Path) -> None:
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4 minimal\n")


def _summary(capsys: pytest.CaptureFixture[str]) -> str:
    return capsys.readouterr().out


# --- parser --------------------------------------------------------------------


def test_build_argument_parser_defaults() -> None:
    parser = cli.build_argument_parser()
    args = parser.parse_args(["some/dir"])
    assert args.directory == Path("some/dir")
    assert args.output is None
    # The shared pricing flags are available on the consolidated CLI too.
    priced = parser.parse_args(
        ["some/dir", "--markup", "15", "--pricing-config", "p.json"]
    )
    assert priced.markup == 15.0
    assert priced.pricing_config == Path("p.json")


def test_main_reports_a_missing_directory() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["does-not-exist"])
    assert excinfo.value.code == 2


def test_main_rejects_a_file_as_a_directory(tmp_path: Path) -> None:
    a_file = tmp_path / "a.txt"
    a_file.write_text("x\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        main([str(a_file)])
    assert not (tmp_path / "Multi-Job Report").exists()


def test_run_raises_when_no_reports_are_present(tmp_path: Path) -> None:
    directory = _directory(tmp_path)
    with pytest.raises(ReportLoadError, match=r"No \*_report\.json files found"):
        main([str(directory)])


# --- end to end ----------------------------------------------------------------


def test_main_writes_every_artifact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    write_report_json: WriteReportJsonFactory,
) -> None:
    directory = _directory(tmp_path)
    pdf_one = tmp_path / "PDF Files" / "a_1.pdf"
    pdf_two = tmp_path / "PDF Files" / "a_2.pdf"
    _write_pdfs(pdf_one, pdf_two)
    write_report_json(
        directory,
        name="a_report.json",
        input_file=str(tmp_path / "a.txt"),
        labels=("ALPHA",),
        copies_per_label=2,
        ink_area_sq_in=1.0,
        substrate_sq_in=96.0,
        output_pdf=[str(pdf_two), str(pdf_one)],
        per_pdf=[
            {
                "total_output_labels": 1,
                "total_characters": 5,
                "total_ink_area_sq_in": 1.0,
                "total_label_material_sq_in": 24.0,
            },
            {
                "total_output_labels": 1,
                "total_characters": 5,
                "total_ink_area_sq_in": 1.0,
                "total_label_material_sq_in": 24.0,
            },
        ],
    )
    write_report_json(
        directory,
        name="b_report.json",
        input_file=str(tmp_path / "b.txt"),
        labels=("BETA",),
        copies_per_label=1,
        label_width_in=4.0,
        label_height_in=2.0,
        page_width_in=24.0,
        ink_area_sq_in=2.0,
        substrate_sq_in=24.0,
    )

    assert main([str(directory)]) == 0

    organized = consolidated_output_paths(directory, BASENAME)
    out_path = organized["txt"]
    json_path = organized["json"]
    assert _summary(capsys) == (
        "Consolidated 2 job report(s) (3 labels, 0.24 linear ft, 4.00 sq in ink)\n"
        f"Wrote consolidated report to {out_path}\n"
        f"Wrote consolidated JSON report to {json_path}\n"
    )

    for key, path in organized.items():
        if key == "pdf_files_vendor":
            assert path.is_dir()
        else:
            assert path.exists(), f"{key} was not written"

    # The vendor folder mirrors the vendor breakdown numbering, not job order.
    vendor = organized["pdf_files_vendor"]
    assert sorted(child.name for child in vendor.iterdir()) == ["1.pdf", "2.pdf"]
    assert (vendor / "1.pdf").read_bytes() == b"%PDF-1.4 minimal\n"

    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["job"]["report_type"] == "consolidated"
    assert report["job"]["total_jobs"] == 2

    text = out_path.read_text(encoding="utf-8")
    assert "VINYL LABEL PRINTING REPORT - CONSOLIDATED" in text
    assert "Jobs Consolidated: 2" in text

    job_csv = organized["csv_job_breakdown"].read_text(encoding="utf-8")
    assert "a.txt" in job_csv and "b.txt" in job_csv


def test_main_honours_an_explicit_output_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    write_report_json: WriteReportJsonFactory,
) -> None:
    directory = _directory(tmp_path)
    write_report_json(directory, name="a_report.json", input_file="a.txt")
    custom = tmp_path / "somewhere-else.txt"

    assert main([str(directory), "-o", str(custom)]) == 0
    assert custom.exists()
    assert "Wrote consolidated report to " in _summary(capsys)
    # The JSON sibling still lands in the organised json/ folder.
    assert consolidated_output_paths(directory, BASENAME)["json"].exists()


def test_main_uses_a_pricing_config_file(
    tmp_path: Path,
    write_report_json: WriteReportJsonFactory,
) -> None:
    directory = _directory(tmp_path)
    write_report_json(
        directory,
        name="a_report.json",
        input_file="a.txt",
        labels=("ALPHA",),
        copies_per_label=1,
        ink_area_sq_in=144.0,
        substrate_sq_in=144.0,
        cost_breakdown={
            "ink_cost": 1.0,
            "substrate_cost": 2.0,
            "printer_hours": 0.5,
            "printer_cost": 3.0,
            "labor_hours": 0.25,
            "labor_cost": 4.0,
            "total_cost": 10.0,
            # The job stored no price, so the config's markup has to supply one.
            "unit_price": 0.0,
        },
    )
    config = tmp_path / PRICING_CONFIG_FILENAME
    config.write_text(
        json.dumps(
            {
                "ink_cost_usd_per_sqft": 1.0,
                "substrate_cost_usd_per_sqft": 2.0,
                "print_rate_hours_per_sqft": 0.5,
                "printer_run_cost_usd_per_hour": 10.0,
                "labor_rate_usd_per_hour": 20.0,
                "labor_time_factor": 0.5,
                "markup_percent": 10.0,
                "customer_report": {"linear_feet": True},
            }
        ),
        encoding="utf-8",
    )

    assert main([str(directory), "--pricing-config", str(config)]) == 0
    organized = consolidated_output_paths(directory, BASENAME)
    text = organized["txt"].read_text(encoding="utf-8")
    assert "COST BREAKDOWN" in text
    collapsed = " ".join(text.split())
    # The headline total is rebuilt from the components (1 + 2 + 3 + 4), and the
    # price comes from the config's 10% markup rather than the job's own zero.
    assert "Total Cost: $ 10.00" in collapsed
    assert "Total Price: $ 11.00" in collapsed
    # The customer_report section of the config widens the customer CSVs.
    customer = organized["csv_job_breakdown_customer"].read_text(encoding="utf-8")
    assert "Linear Feet" in customer.splitlines()[0]


def test_main_skips_file_breakdowns_without_per_pdf_metrics(
    tmp_path: Path,
    write_report_json: WriteReportJsonFactory,
) -> None:
    directory = _directory(tmp_path)
    write_report_json(directory, name="a_report.json", input_file="a.txt")
    assert main([str(directory)]) == 0
    organized = consolidated_output_paths(directory, BASENAME)
    assert not organized["csv_file_breakdown"].exists()
    assert not organized["csv_file_breakdown_customer"].exists()
    assert not organized["csv_file_breakdown_vendor"].exists()
    assert organized["pdf_files_vendor"].is_dir()
    assert list(organized["pdf_files_vendor"].iterdir()) == []


def test_main_is_idempotent(
    tmp_path: Path,
    write_report_json: WriteReportJsonFactory,
    strip_generated: Callable[[str], str],
) -> None:
    directory = _directory(tmp_path)
    write_report_json(directory, name="a_report.json", input_file="a.txt")
    organized = consolidated_output_paths(directory, BASENAME)
    main([str(directory)])
    # Only the artifacts this run actually writes are comparable; the file
    # breakdowns, for instance, need per-PDF metrics to exist at all.
    written = {key: path for key, path in organized.items() if path.is_file()}
    text_keys = tuple(key for key in written if not key.endswith("xlsx"))
    xlsx_keys = tuple(key for key in written if key.endswith("xlsx"))
    assert text_keys and xlsx_keys

    def snapshot() -> dict[str, object]:
        """Every text artifact, plus the sheet XML of each workbook.

        The workbooks are zips whose container carries a generation timestamp, so
        only their worksheet payload is comparable between runs.
        """
        texts: dict[str, object] = {
            key: strip_generated(written[key].read_text(encoding="utf-8"))
            for key in text_keys
        }
        for key in xlsx_keys:
            with zipfile.ZipFile(written[key]) as workbook:
                texts[key] = workbook.read("xl/worksheets/sheet1.xml")
        return texts

    first = snapshot()
    main([str(directory)])
    assert snapshot() == first


def test_main_does_not_fold_its_own_output_back_in(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    write_report_json: WriteReportJsonFactory,
) -> None:
    directory = _directory(tmp_path)
    write_report_json(directory, name="a_report.json", input_file="a.txt")
    # A consolidated JSON sitting in the input directory is never re-read.
    marker = directory / "z_report.json"
    marker.write_text(
        json.dumps({"job": {"report_type": "consolidated"}}), encoding="utf-8"
    )
    assert main([str(directory)]) == 0
    assert "Consolidated 1 job report(s)" in _summary(capsys)


# --- vendor PDF copies ---------------------------------------------------------


def _record(filename: str) -> PdfFileRecord:
    return PdfFileRecord(filename, 1, 1, 1.0, 1.0, label_size="8x3")


def _report_emitting(*sources: Path) -> JobReport:
    """A one-job report that emitted exactly ``sources``, in emission order."""
    return job_report(
        path=sources[0].parent / "a_report.json",
        input_file="a.txt",
        pdfs=list(sources),
        per_pdf=[pdf_metrics(1, 1, 1.0, 1.0) for _ in sources],
    )


def test_copy_pdfs_for_vendor_numbers_by_vendor_order(
    tmp_path: Path,
) -> None:
    source_one = tmp_path / "z_1.pdf"
    source_two = tmp_path / "a_1.pdf"
    source_one.write_bytes(b"z-source")
    source_two.write_bytes(b"a-source")
    report = _report_emitting(source_one, source_two)
    vendor_dir = tmp_path / "vendor"
    vendor_dir.mkdir()

    copy_pdfs_for_vendor([_record("z_1.pdf"), _record("a_1.pdf")], [report], vendor_dir)

    # a_1.pdf sorts first under the vendor ordering, so it becomes 1.pdf even
    # though the job emitted it second.
    assert sorted(child.name for child in vendor_dir.iterdir()) == ["1.pdf", "2.pdf"]
    assert (vendor_dir / "1.pdf").read_bytes() == b"a-source"
    assert (vendor_dir / "2.pdf").read_bytes() == b"z-source"


def test_copy_pdfs_for_vendor_skips_missing_sources(tmp_path: Path) -> None:
    vendor_dir = tmp_path / "vendor"
    vendor_dir.mkdir()
    copy_pdfs_for_vendor([_record("gone.pdf")], [], vendor_dir)
    assert list(vendor_dir.iterdir()) == []


def test_copy_pdfs_for_vendor_reports_a_failed_copy(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "a_1.pdf"
    _write_pdfs(source)
    report = _report_emitting(source)
    vendor_dir = tmp_path / "vendor"
    vendor_dir.mkdir()

    def boom(src: object, dst: object) -> None:
        raise OSError("disk on fire")

    monkeypatch.setattr(shutil, "copy2", boom)
    copy_pdfs_for_vendor([_record("a_1.pdf")], [report], vendor_dir)

    printed = _summary(capsys)
    assert "Warning: Could not copy" in printed
    assert "disk on fire" in printed
