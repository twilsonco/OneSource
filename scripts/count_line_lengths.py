"""Count how many lines have each character length in a directory of text files.

Takes a directory path and loops over every ``*.txt`` file in it, counting the
character length of each line (leading/trailing whitespace stripped, inner
whitespace kept; blank lines skipped). Prints one ASCII histogram table per
file plus a final table with the combined counts across all files.

Usage:
    uv run python scripts/count_line_lengths.py <directory>
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path


def count_line_lengths(path: Path) -> Counter[int]:
    """Return a mapping of line length -> number of lines with that length.

    Each line is stripped of leading/trailing whitespace (including the
    newline) before measuring; whitespace between non-whitespace characters is
    kept. Lines that are empty after stripping are skipped.
    """
    lengths: Counter[int] = Counter()
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            stripped = raw_line.strip()
            if not stripped:
                continue
            lengths[len(stripped)] += 1
    return lengths


def render_table(title: str, lengths: Counter[int]) -> str:
    """Render ``lengths`` as a bordered ASCII table with ``# chars``/``count``."""
    headers = ("# chars", "line count")
    rows = [(str(chars), str(count)) for chars, count in sorted(lengths.items())]

    col1_width = max([len(headers[0]), *(len(row[0]) for row in rows)])
    col2_width = max([len(headers[1]), *(len(row[1]) for row in rows)])

    def border(left: str, mid: str, right: str) -> str:
        return f"+{'-' * (col1_width + 2)}{mid}{'-' * (col2_width + 2)}{right}"

    def row(cells: tuple[str, str]) -> str:
        c1, c2 = cells
        return f"| {c1:>{col1_width}} | {c2:>{col2_width}} |"

    lines = [
        title,
        border("+", "+", "+"),
        row(headers),
        border("+", "+", "+"),
    ]
    lines.extend(row(cell) for cell in rows)
    lines.append(border("+", "+", "+"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, process each text file, and print the histogram tables."""
    parser = argparse.ArgumentParser(
        description="Histogram of per-line character counts for text files "
        "in a directory (one table per file, plus a combined table)."
    )
    parser.add_argument(
        "directory",
        type=Path,
        help="Directory containing the .txt files to process.",
    )
    args = parser.parse_args(argv)

    directory: Path = args.directory
    if not directory.is_dir():
        print(f"error: not a directory: {directory}", file=sys.stderr)
        return 1

    files = sorted(directory.glob("*.txt"))
    if not files:
        print(f"error: no .txt files found in {directory}", file=sys.stderr)
        return 1

    combined: Counter[int] = Counter()
    tables: list[str] = []
    for path in files:
        lengths = count_line_lengths(path)
        combined.update(lengths)
        tables.append(render_table(path.name, lengths))

    tables.append(render_table("TOTAL (all files)", combined))
    print("\n\n".join(tables))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
