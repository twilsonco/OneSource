# `split_lines_by_length.py`

Split the text files in a directory into per-length-range files, based on each
line's character count.

This pairs with [`count_line_lengths.py`](count_line_lengths.md): once you know
how line lengths are distributed, this script groups them into separate files
so each length bucket can be printed with its own layout settings (e.g. one
font size per bucket in `vinyl_label_prep.py`).

## Usage

```sh
uv run python scripts/split_lines_by_length.py <directory> 6,8,11,14,21
uv run python scripts/split_lines_by_length.py <directory> 6,8,11,14,21 \
    --combined 14,21
```

- `<directory>` — directory containing the `.txt` files to process.
- `<ranges>` — comma-separated, **strictly increasing** upper bounds of the
  character-count ranges. The last range is open-ended.
- `--combined` / `-c` — optional comma-separated subset of the range bounds
  whose matching lines are merged into a single file across all input files.

## Ranges

The bounds define buckets between consecutive values. For example, `6,8,11,14,21`
yields:

| Range | Lines with |
| --- | --- |
| `1-6` | 1–6 characters |
| `7-8` | 7–8 characters |
| `9-11` | 9–11 characters |
| `12-14` | 12–14 characters |
| `15+` | 15 or more characters |

## Line reading rules

- Each line is stripped of leading/trailing whitespace; inner whitespace is kept.
- Blank lines (empty after stripping) are skipped; original order is preserved.
- Files are read as UTF-8 with undecodable bytes replaced.

## Output

All output files land in `<directory>/output` (created on demand):

- **Normal ranges** — one file per input file per range, named
  `<input_stem>_<upper>-chars.txt`.
- **`--combined` ranges** — a single file per range named `<upper>-chars.txt`
  containing the matching lines from *all* input files, grouped in sorted
  input-file order.
- Ranges with no matching lines produce **no file at all**.

The script prints a summary to stdout: how many files were split, and for each
range the total line count plus the per-output-file breakdown (or
`(no matching lines)`).

## Errors

Exits with status `1` and a message on stderr when:

- `<directory>` is not a directory or contains no `.txt` files;
- `<ranges>` is empty, contains non-integers/non-positive values, or is not
  strictly increasing;
- `--combined` contains duplicates or values that are not in `<ranges>`.

## Example

```sh
# Group label lists into length buckets, merging the two longest buckets
# across all input files:
uv run python scripts/split_lines_by_length.py \
    "data/input/full_label_lists" 6,8,11,14,21 --combined 14,21
```
