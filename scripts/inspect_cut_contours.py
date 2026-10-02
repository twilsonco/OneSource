"""Inspect PDF files for CutContour and PerfCutContour spot colors and overprint.

This script analyzes PDF files to detect:
- CutContour spot colors (for kiss-cuts in label borders)
- PerfCutContour spot colors (for perforated/through-cuts in sheet separators)
- Overprint settings (which prevent knockout artifacts)

Roland VersaWorks and other RIP software use these spot color separations to
route cut paths directly to the plotter blade instead of the printer's ink nozzles.

Usage:
    python scripts/inspect_cut_contours.py <file_or_directory>
    python scripts/inspect_cut_contours.py data/PDF_Files/
    python scripts/inspect_cut_contours.py out_8x3_24-labels.pdf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:
    print("Error: pypdf is required. Install it with: uv add pypdf", file=sys.stderr)
    sys.exit(1)


def find_spot_colors(pdf_path: Path) -> tuple[set[str], bool]:
    """Extract spot color names and overprint status from a PDF.

    Returns a tuple of (spot_color_names, has_overprint).
    """
    spot_colors: set[str] = set()
    has_overprint = False

    try:
        reader = PdfReader(str(pdf_path))
    except Exception as e:
        print(f"Error reading {pdf_path}: {e}", file=sys.stderr)
        return spot_colors, has_overprint

    for page_num, page in enumerate(reader.pages):
        # Inspect the page's resource dictionary for color spaces
        if "/Resources" in page:
            resources = page["/Resources"]
            if isinstance(resources, dict):
                # Look for Separation color spaces
                if "/ColorSpace" in resources:
                    color_space = resources["/ColorSpace"]
                    if isinstance(color_space, dict):
                        for cs_name, cs_obj in color_space.items():
                            # cs_name is a NameObject like "/CutContour"
                            # Handle both direct arrays and indirect references
                            try:
                                if hasattr(cs_obj, "get_object"):
                                    cs_obj = cs_obj.get_object()
                            except Exception:
                                pass

                            if isinstance(cs_obj, (list, tuple)) and len(cs_obj) > 0:
                                # Separation entry: [/Separation /SpotName ...]
                                cs_type = str(cs_obj[0]).lstrip("/")
                                if cs_type == "Separation" and len(cs_obj) > 1:
                                    spot_name = str(cs_obj[1]).lstrip("/")
                                    spot_colors.add(spot_name)

                # Look for graphics state overprint settings
                if "/ExtGState" in resources:
                    ext_g_state = resources["/ExtGState"]
                    if isinstance(ext_g_state, dict):
                        for gs_name, gs_obj in ext_g_state.items():
                            try:
                                gs_dict = (
                                    gs_obj.get_object()
                                    if hasattr(gs_obj, "get_object")
                                    else gs_obj
                                )
                                if isinstance(gs_dict, dict):
                                    # Check for overprint flags
                                    # /OP (overprint stroke), /op (overprint fill), /OPM (overprintMode)
                                    if "/OP" in gs_dict and gs_dict["/OP"]:
                                        has_overprint = True
                                    if "/op" in gs_dict and gs_dict["/op"]:
                                        has_overprint = True
                                    if "/OPM" in gs_dict and gs_dict["/OPM"] == 1:
                                        has_overprint = True
                            except Exception:
                                # Skip problematic graphics state entries
                                pass

    return spot_colors, has_overprint


def inspect_pdf(pdf_path: Path) -> None:
    """Print inspection results for a single PDF file."""
    spot_colors, has_overprint = find_spot_colors(pdf_path)

    has_cutcontour = "CutContour" in spot_colors
    has_perfcutcontour = "PerfCutContour" in spot_colors

    # Format the output
    status_parts = []
    if has_cutcontour:
        status_parts.append("✓ CutContour (kiss-cuts)")
    if has_perfcutcontour:
        status_parts.append("✓ PerfCutContour (perf-cuts)")
    if has_overprint:
        status_parts.append("✓ Overprint enabled")

    if status_parts:
        status = " | ".join(status_parts)
    else:
        status = "No cut contours or overprint detected"

    print(f"{pdf_path.name}: {status}")


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Path to a PDF file or directory containing PDF files.",
    )

    args = parser.parse_args(argv)
    path = args.path

    if not path.exists():
        print(f"Error: {path} does not exist", file=sys.stderr)
        return 1

    pdf_files: list[Path] = []

    if path.is_file():
        if path.suffix.lower() == ".pdf":
            pdf_files.append(path)
        else:
            print(f"Error: {path} is not a PDF file", file=sys.stderr)
            return 1
    elif path.is_dir():
        # Recursively find all PDF files
        pdf_files = sorted(path.rglob("*.pdf"))
        if not pdf_files:
            print(f"Warning: No PDF files found in {path}", file=sys.stderr)
            return 1
    else:
        print(f"Error: {path} is neither a file nor directory", file=sys.stderr)
        return 1

    # Inspect each PDF
    for pdf_path in pdf_files:
        inspect_pdf(pdf_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
