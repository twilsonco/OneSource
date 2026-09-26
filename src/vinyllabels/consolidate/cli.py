"""``consolidate`` — fold every job report in a directory into one run.

Reads the ``*_report.json`` files ``generate`` writes and emits the whole
consolidated output set: the human-readable report plus its JSON sibling, the
per-label text/XLSX listings, and every CSV breakdown in its internal, customer
and vendor variants. Finally it stages the emitted PDFs in the vendor drop
folder under simplified names.

Run with ``--help`` for the full option list.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from vinyllabels.cli import add_pricing_arguments, pricing_from_args
from vinyllabels.consolidate.csv_reports import (
    write_job_breakdown_csv,
    write_pdf_file_breakdown_csv,
    write_pdf_file_breakdown_vendor_csv,
    write_per_label_breakdown_csv,
    write_size_breakdown_csv,
    write_vendor_label_size_breakdown_csv,
)
from vinyllabels.consolidate.load import load_reports
from vinyllabels.consolidate.merge import (
    build_pdf_file_records,
    consolidate,
    consolidate_label_sizes,
    consolidate_labels,
    consolidate_labels_by_size,
    consolidate_size_areas,
    sort_pdf_records,
)
from vinyllabels.consolidate.model import JobReport, PdfFileRecord
from vinyllabels.consolidate.report import (
    write_consolidated_report,
    write_per_label_report,
    write_per_label_report_xlsx,
)
from vinyllabels.models import PricingConfig
from vinyllabels.output_paths import consolidated_output_paths

__all__ = ["build_argument_parser", "copy_pdfs_for_vendor", "main", "run"]

# Name every consolidated artifact is prefixed with.
BASENAME = "vinyl_labels_combined"


def build_argument_parser() -> argparse.ArgumentParser:
    """Construct the parser for a consolidated run."""
    parser = argparse.ArgumentParser(
        description=(
            "Consolidate the metrics of every *_report.json file in a directory "
            "into one report."
        ),
    )
    parser.add_argument(
        "directory",
        type=Path,
        help="Directory containing *_report.json files written by generate.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=(
            f"Path for the consolidated text report (default: "
            f"Multi-Job Report/{BASENAME}.txt, beside <directory>)."
        ),
    )
    add_pricing_arguments(parser)
    return parser


def copy_pdfs_for_vendor(
    pdf_records: list[PdfFileRecord], reports: list[JobReport], vendor_dir: Path
) -> None:
    """Copy each emitted PDF into ``vendor_dir`` as ``1.pdf``, ``2.pdf``, ...

    Numbering follows the vendor-friendly (size, filename) order so the drop
    folder matches the vendor breakdown CSVs. Missing sources are skipped.
    """
    sources: dict[str, Path] = {}
    for report in reports:
        for pdf_file in report.output_pdf_files or ():
            sources[pdf_file.name] = pdf_file

    for file_num, record in enumerate(sort_pdf_records(pdf_records), start=1):
        src_path = sources.get(record.filename)
        if src_path is None or not src_path.exists():
            continue
        dst_path = vendor_dir / f"{file_num}.pdf"
        try:
            shutil.copy2(src_path, dst_path)
        except OSError as exc:
            print(f"Warning: Could not copy {src_path} to {dst_path}: {exc}")


def _customer_config(
    pricing_config: PricingConfig | None,
) -> dict[str, bool] | None:
    return pricing_config.customer_report if pricing_config else None


def run(directory: Path, out_path: Path, pricing_config: PricingConfig | None) -> None:
    """Load every report in ``directory`` and write the full consolidated set."""
    organized = consolidated_output_paths(directory, BASENAME)
    # Never fold this tool's own output back into a later run.
    skip = frozenset({out_path.resolve(), organized["json"].resolve()})

    reports = load_reports(directory, skip)
    metrics = consolidate(reports, pricing_config)
    sizes = consolidate_label_sizes(reports)
    size_areas = consolidate_size_areas(reports, pricing_config)
    labels = consolidate_labels(reports, pricing_config)
    labels_by_size = consolidate_labels_by_size(reports, pricing_config)
    customer = _customer_config(pricing_config)

    json_report_path = write_consolidated_report(
        metrics,
        sizes,
        size_areas,
        labels,
        reports,
        out_path,
        directory,
        organized["json"],
    )

    write_size_breakdown_csv(
        sizes,
        size_areas,
        reports,
        organized["csv_size_breakdown"],
        include_costs=True,
    )
    write_size_breakdown_csv(
        sizes,
        size_areas,
        reports,
        organized["csv_size_breakdown_customer"],
        include_costs=False,
        customer_report_config=customer,
    )
    write_vendor_label_size_breakdown_csv(
        sizes, size_areas, reports, organized["csv_vendor_size_breakdown"]
    )

    write_job_breakdown_csv(
        reports, directory, organized["csv_job_breakdown"], include_costs=True
    )
    write_job_breakdown_csv(
        reports,
        directory,
        organized["csv_job_breakdown_customer"],
        include_costs=False,
        customer_report_config=customer,
    )

    write_per_label_report(
        reports, organized["txt_labels"], directory, include_costs=True
    )
    write_per_label_report(
        reports, organized["txt_labels_customer"], directory, include_costs=False
    )
    write_per_label_report_xlsx(
        reports, organized["txt_labels_xlsx"], directory, include_costs=True
    )
    write_per_label_report_xlsx(
        reports,
        organized["txt_labels_customer_xlsx"],
        directory,
        include_costs=False,
        customer_report_config=customer,
    )

    pdf_records = build_pdf_file_records(reports)
    write_pdf_file_breakdown_csv(
        pdf_records, organized["csv_file_breakdown"], include_costs=True
    )
    write_pdf_file_breakdown_csv(
        pdf_records,
        organized["csv_file_breakdown_customer"],
        include_costs=False,
        customer_report_config=customer,
    )
    write_pdf_file_breakdown_vendor_csv(
        pdf_records, organized["csv_file_breakdown_vendor"]
    )
    if pdf_records:
        copy_pdfs_for_vendor(pdf_records, reports, organized["pdf_files_vendor"])

    write_per_label_breakdown_csv(
        labels_by_size, organized["csv_label_breakdown"], include_costs=True
    )
    write_per_label_breakdown_csv(
        labels_by_size,
        organized["csv_label_breakdown_customer"],
        include_costs=False,
        customer_report_config=customer,
    )

    print(
        f"Consolidated {metrics.total_jobs} job report(s) "
        f"({metrics.total_output_labels} labels, "
        f"{metrics.linear_feet:.2f} linear ft, "
        f"{metrics.total_ink_area_sq_in:.2f} sq in ink)\n"
        f"Wrote consolidated report to {out_path}\n"
        f"Wrote consolidated JSON report to {json_report_path}"
    )


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and write the consolidated report set."""
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    directory: Path = args.directory
    if not directory.is_dir():
        parser.error(f"Not a directory: {directory}")

    organized = consolidated_output_paths(directory, BASENAME)
    out_path: Path = args.output if args.output is not None else organized["txt"]

    run(directory, out_path, pricing_from_args(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
