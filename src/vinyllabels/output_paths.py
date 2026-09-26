"""Where generated artifacts land.

Both tools write into a fixed set of sibling folders next to their input, so a
job's PDFs, text reports, JSON and CSVs stay grouped. The two layouts differ
only in their root: a single job organises around its input *file*, while the
consolidated report organises around an input *directory*.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["consolidated_output_paths", "ensure_output_dirs", "job_output_paths"]

# Folder names, kept here so both tools (and the docs) agree on them.
PDF_DIR = "PDF Files"
JOB_REPORT_DIR = "Job Report"
MULTI_JOB_REPORT_DIR = "Multi-Job Report"
VENDOR_PDF_DIR = "PDF Files for Vendor"
JSON_DIR = "json"
CSV_DIR = "csv"


def ensure_output_dirs(*dirs: Path) -> None:
    """Create every directory in ``dirs`` (parents included) if missing."""
    for directory in dirs:
        directory.mkdir(parents=True, exist_ok=True)


def job_output_paths(base_path: Path, basename: str) -> dict[str, Path]:
    """Output paths for one layout job, organised beside its input file.

    ``base_path`` is the input file. Creates ``PDF Files/``, ``Job Report/``,
    ``json/`` and ``csv/`` next to it.
    """
    base_dir = base_path.parent
    pdf_dir = base_dir / PDF_DIR
    report_dir = base_dir / JOB_REPORT_DIR
    json_dir = base_dir / JSON_DIR
    csv_dir = base_dir / CSV_DIR
    ensure_output_dirs(pdf_dir, report_dir, json_dir, csv_dir)

    return {
        "pdf": pdf_dir / f"{basename}.pdf",
        "txt": report_dir / f"{basename}_report.txt",
        "json": json_dir / f"{basename}_report.json",
        "csv_report": csv_dir / f"{basename}_report.csv",
        "csv_report_customer": csv_dir / f"{basename}_report_customer.csv",
    }


def consolidated_output_paths(input_directory: Path, basename: str) -> dict[str, Path]:
    """Output paths for a consolidated run, organised beside the input directory.

    Builds ``Multi-Job Report/`` (text + ``csv/`` + ``json/``) as a sibling of
    ``input_directory``, plus the ``PDF Files for Vendor/`` drop folder.
    """
    output_root = input_directory.parent / MULTI_JOB_REPORT_DIR
    csv_dir = output_root / CSV_DIR
    json_dir = output_root / JSON_DIR
    pdf_vendor_dir = input_directory.parent / VENDOR_PDF_DIR
    ensure_output_dirs(output_root, csv_dir, json_dir, pdf_vendor_dir)

    return {
        "txt": output_root / f"{basename}.txt",
        "json": json_dir / f"{basename}.json",
        "txt_labels": output_root / f"{basename}_labels.txt",
        "txt_labels_customer": output_root / f"{basename}_labels_customer.txt",
        "txt_labels_xlsx": output_root / f"{basename}_labels.xlsx",
        "txt_labels_customer_xlsx": output_root / f"{basename}_labels_customer.xlsx",
        "csv_size_breakdown": csv_dir / f"{basename}_size_breakdown.csv",
        "csv_size_breakdown_customer": csv_dir
        / f"{basename}_size_breakdown_customer.csv",
        "csv_vendor_size_breakdown": csv_dir / f"{basename}_vendor_size_breakdown.csv",
        "csv_job_breakdown": csv_dir / f"{basename}_job_breakdown.csv",
        "csv_job_breakdown_customer": csv_dir
        / f"{basename}_job_breakdown_customer.csv",
        "csv_file_breakdown": csv_dir / f"{basename}_file_breakdown.csv",
        "csv_file_breakdown_customer": csv_dir
        / f"{basename}_file_breakdown_customer.csv",
        "csv_file_breakdown_vendor": csv_dir / f"{basename}_file_breakdown_vendor.csv",
        "csv_label_breakdown": csv_dir / f"{basename}_label_breakdown.csv",
        "csv_label_breakdown_customer": csv_dir
        / f"{basename}_label_breakdown_customer.csv",
        "pdf_files_vendor": pdf_vendor_dir,
    }
