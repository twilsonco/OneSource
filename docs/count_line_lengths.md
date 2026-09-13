# `count_line_lengths.py`

Histogram of per-line character counts across the text files in a directory.

Given a directory, the script loops over every `*.txt` file in it and counts
how many lines have each character length. It prints one ASCII histogram table
per file, plus a final `TOTAL (all files)` table with the combined counts.

This is typically used to size label text before generating PDFs — for example
to see how a label list's codes are distributed by length before picking font
sizes or splitting jobs (see [`split_lines_by_length.md`](split_lines_by_length.md)).

## Usage

```sh
uv run python scripts/count_line_lengths.py <directory>
```

- `<directory>` — directory containing the `.txt` files to process.

The script exits with an error (status `1`) if `<directory>` is not a
directory or contains no `.txt` files.

## Line counting rules

- Each line is stripped of leading/trailing whitespace (including the newline)
  before measuring; whitespace *between* non-whitespace characters is kept.
- Lines that are empty after stripping are skipped entirely.
- Files are read as UTF-8 with undecodable bytes replaced, so binary-ish input
  never crashes the count.

## Output

To stdout, one bordered ASCII table per file (titled with the file name)
followed by a combined table, e.g.:

```text
tags.txt
+---------+------------+
| # chars | line count |
+---------+------------+
|       6 |         12 |
|       7 |          4 |
|      11 |          9 |
+---------+------------+
```

Nothing is written to disk.

## Example

```sh
uv run python scripts/count_line_lengths.py "data/input/full_label_lists"
```
