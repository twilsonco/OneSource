"""Tests for :mod:`vinyllabels.output_paths`."""

from __future__ import annotations

from pathlib import Path

from vinyllabels.output_paths import (
    CSV_DIR,
    JOB_REPORT_DIR,
    JSON_DIR,
    MULTI_JOB_REPORT_DIR,
    PDF_DIR,
    VENDOR_PDF_DIR,
    consolidated_output_paths,
    ensure_output_dirs,
    job_output_paths,
)


def test_folder_names_are_the_documented_ones() -> None:
    assert (PDF_DIR, JOB_REPORT_DIR, MULTI_JOB_REPORT_DIR, VENDOR_PDF_DIR) == (
        "PDF Files",
        "Job Report",
        "Multi-Job Report",
        "PDF Files for Vendor",
    )
    assert (JSON_DIR, CSV_DIR) == ("json", "csv")


def test_ensure_output_dirs_creates_and_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b"
    ensure_output_dirs(target, tmp_path / "c")
    assert target.is_dir()
    ensure_output_dirs(target, tmp_path / "c")
    assert target.is_dir()


def test_job_output_paths_layout(tmp_path: Path) -> None:
    input_file = tmp_path / "job.txt"
    input_file.write_text("A\n", encoding="utf-8")

    paths = job_output_paths(input_file, "job")

    assert paths["pdf"] == tmp_path / "PDF Files" / "job.pdf"
    assert paths["txt"] == tmp_path / "Job Report" / "job_report.txt"
    assert paths["json"] == tmp_path / "json" / "job_report.json"
    assert paths["csv_report"] == tmp_path / "csv" / "job_report.csv"
    assert paths["csv_report_customer"] == (
        tmp_path / "csv" / "job_report_customer.csv"
    )
    for name in (PDF_DIR, JOB_REPORT_DIR, JSON_DIR, CSV_DIR):
        assert (tmp_path / name).is_dir()


def test_consolidated_output_paths_layout(tmp_path: Path) -> None:
    input_dir = tmp_path / "json"
    input_dir.mkdir()

    paths = consolidated_output_paths(input_dir, "combined")
    root = tmp_path / MULTI_JOB_REPORT_DIR

    assert paths["txt"] == root / "combined.txt"
    assert paths["json"] == root / "json" / "combined.json"
    assert paths["txt_labels"] == root / "combined_labels.txt"
    assert paths["txt_labels_customer"] == root / "combined_labels_customer.txt"
    assert paths["txt_labels_xlsx"] == root / "combined_labels.xlsx"
    assert paths["txt_labels_customer_xlsx"] == (root / "combined_labels_customer.xlsx")
    assert paths["csv_size_breakdown"] == (root / "csv" / "combined_size_breakdown.csv")
    assert paths["csv_size_breakdown_customer"] == (
        root / "csv" / "combined_size_breakdown_customer.csv"
    )
    assert paths["csv_vendor_size_breakdown"] == (
        root / "csv" / "combined_vendor_size_breakdown.csv"
    )
    assert paths["csv_job_breakdown"] == root / "csv" / "combined_job_breakdown.csv"
    assert paths["csv_job_breakdown_customer"] == (
        root / "csv" / "combined_job_breakdown_customer.csv"
    )
    assert paths["csv_file_breakdown"] == root / "csv" / "combined_file_breakdown.csv"
    assert paths["csv_file_breakdown_customer"] == (
        root / "csv" / "combined_file_breakdown_customer.csv"
    )
    assert paths["csv_file_breakdown_vendor"] == (
        root / "csv" / "combined_file_breakdown_vendor.csv"
    )
    assert paths["csv_label_breakdown"] == root / "csv" / "combined_label_breakdown.csv"
    assert paths["csv_label_breakdown_customer"] == (
        root / "csv" / "combined_label_breakdown_customer.csv"
    )
    assert paths["pdf_files_vendor"] == tmp_path / VENDOR_PDF_DIR

    assert (root / CSV_DIR).is_dir()
    assert (root / JSON_DIR).is_dir()
    assert (tmp_path / VENDOR_PDF_DIR).is_dir()
