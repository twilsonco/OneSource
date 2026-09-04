"""Split text files into per-length-range files based on line character counts.

Takes a directory path plus a comma-separated list of upper bounds defining
character-count ranges. For example, ``6,8,11,14,21`` yields the ranges 1-6,
7-8, 9-11, 12-14, and 15+ (the last range is open-ended: lines longer than
the largest bound land there). Each input ``*.txt`` file is read with
leading/trailing whitespace stripped from every line (blank lines skipped,
original order preserved), and each range gets its own output files under
``<directory>/output``.

By default every range produces one output file per input file, named
``<input_stem>_<upper>-chars.txt``. Ranges listed in ``--combined`` (whose
values must appear in the range list) instead produce a single file named
``<upper>-chars.txt`` containing the matching lines from all input files,
grouped in sorted input-file order. Ranges with no matching lines produce no
file at all.

Usage:
    uv run python scripts/split_lines_by_length.py <directory> 6,8,11,14,21
    uv run python scripts/split_lines_by_length.py <directory> 6,8,11,14,21 \
        --combined 14,21
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path


def parse_int_list(spec: str, flag: str) -> list[int]:
    """Parse a comma-separated list of positive integers from ``spec``.

    ``flag`` names the argument in error messages. Raises ``ValueError`` on
    empty parts, non-integers, or non-positive values.
    """
    values: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"{flag}: empty value in {spec!r}")
        try:
            value = int(part)
        except ValueError:
            raise ValueError(f"{flag}: not an integer: {part!r}") from None
        if value <= 0:
            raise ValueError(f"{flag}: values must be positive, got {value}")
        values.append(value)
    return values


def parse_ranges(spec: str) -> list[int]:
    """Parse the range upper bounds, validating they are strictly increasing."""
    uppers = parse_int_list(spec, "ranges")
    if not uppers:
        raise ValueError("ranges: no values given")
    for previous, upper in zip(uppers, uppers[1:]):
        if upper <= previous:
            raise ValueError(
                f"ranges: must be strictly increasing, got {previous} then {upper}"
            )
    return uppers


def parse_combined(spec: str | None, uppers: Sequence[int]) -> set[int]:
    """Parse the ``--combined`` upper bounds, validating each is in ``uppers``."""
    if spec is None or not spec.strip():
        return set()
    values = parse_int_list(spec, "--combined")
    if len(set(values)) != len(values):
        raise ValueError(f"--combined: duplicate values in {spec!r}")
    unknown = [str(value) for value in values if value not in uppers]
    if unknown:
        raise ValueError(f"--combined: values not in ranges: {', '.join(unknown)}")
    return set(values)


def bucket_index(length: int, uppers: Sequence[int]) -> int:
    """Return the index of the bucket a line of ``length`` characters lands in.

    The last bucket is open-ended, so lengths above the largest bound land
    there.
    """
    for index, upper in enumerate(uppers):
        if length <= upper:
            return index
    return len(uppers) - 1


def range_label(index: int, uppers: Sequence[int]) -> str:
    """Return a human-readable label like ``7-8`` or ``15+`` for a bucket."""
    lower = 1 if index == 0 else uppers[index - 1] + 1
    if index == len(uppers) - 1:
        return f"{lower}+"
    return f"{lower}-{uppers[index]}"


def read_stripped_lines(path: Path) -> list[str]:
    """Return the non-blank lines of ``path``, stripped, in original order.

    Each line is stripped of leading/trailing whitespace (including the
    newline); whitespace between non-whitespace characters is kept. Lines
    that are empty after stripping are skipped.
    """
    lines: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            stripped = raw_line.strip()
            if stripped:
                lines.append(stripped)
    return lines


def write_lines(path: Path, lines: Sequence[str]) -> None:
    """Write ``lines`` to ``path``, one per line, with a trailing newline."""
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, split each text file into per-range output files."""
    parser = argparse.ArgumentParser(
        description="Split the .txt files in a directory into per-range files "
        "by line character count (stripped length). Ranges are given as "
        "comma-separated upper bounds; the last range is open-ended."
    )
    parser.add_argument(
        "directory",
        type=Path,
        help="Directory containing the .txt files to process.",
    )
    parser.add_argument(
        "ranges",
        help="Comma-separated strictly increasing upper bounds of the "
        "character-count ranges, e.g. '6,8,11,14,21'.",
    )
    parser.add_argument(
        "--combined",
        "-c",
        default=None,
        help="Comma-separated subset of the range bounds whose matching lines "
        "are merged into a single file across all input files, e.g. '14,21'. "
        "Omit to write per-input-file outputs for every range.",
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

    try:
        uppers = parse_ranges(args.ranges)
        combined = parse_combined(args.combined, uppers)
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1

    upper_to_index = {upper: index for index, upper in enumerate(uppers)}
    combined_indices = {upper_to_index[upper] for upper in combined}

    # Buffers keyed by (input file name, bucket index); created on first hit
    # so empty buckets never produce a file. Combined buckets accumulate lines
    # from every input file in sorted-file order.
    per_file: dict[tuple[str, int], list[str]] = {}
    combined_buffers: dict[int, list[str]] = {index: [] for index in combined_indices}

    for path in files:
        for line in read_stripped_lines(path):
            index = bucket_index(len(line), uppers)
            if index in combined_indices:
                combined_buffers[index].append(line)
            else:
                per_file.setdefault((path.name, index), []).append(line)

    output_dir = directory / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Per-bucket report rows of (output file name, lines written), filled as
    # each output file is written.
    rows_per_bucket: dict[int, list[tuple[str, int]]] = {}
    for (name, index), lines in per_file.items():
        out_path = output_dir / f"{Path(name).stem}_{uppers[index]}-chars.txt"
        write_lines(out_path, lines)
        rows_per_bucket.setdefault(index, []).append((out_path.name, len(lines)))
    for index, lines in combined_buffers.items():
        if not lines:
            continue
        out_path = output_dir / f"{uppers[index]}-chars.txt"
        write_lines(out_path, lines)
        rows_per_bucket.setdefault(index, []).append((out_path.name, len(lines)))

    print(f"split {len(files)} files from {directory} into {output_dir}")
    for index in range(len(uppers)):
        rows = sorted(rows_per_bucket.get(index, []))
        total = sum(count for _, count in rows)
        print(f"  {range_label(index, uppers)} chars ({total} lines):")
        if rows:
            name_width = max(len(name) for name, _ in rows)
            for name, count in rows:
                print(f"    {name:<{name_width}}  {count}")
        else:
            print("    (no matching lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
