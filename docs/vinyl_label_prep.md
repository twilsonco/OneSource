# Vinyl label layout & job reports

Two scripts form the vinyl label printing pipeline:

- [`scripts/vinyl_label_prep.py`](../scripts/vinyl_label_prep.py) — lays out
  label codes on a wide print page, emits a print-ready PDF plus per-job
  metrics reports.
- [`scripts/vinyl_label_multi_job_report.py`](../scripts/vinyl_label_multi_job_report.py) —
  consolidates the metrics of many jobs into one combined report.

---

## `vinyl_label_prep.py`

Lays out vinyl labels (8in × 3in) on a 52in-wide print page and emits a
print-ready PDF, alongside a text report covering material yield and ink
usage.

### Layout

- Each label is 8in × 3in with 1/2in margins on all sides; hairline border
  (edges shared between adjacent labels are drawn once, never doubled).
- Text is 2in tall bold Arial, compressed horizontally if needed.
- Page is 52in wide with 1in margins on all four sides.
- Each label is printed twice (`2X`).
- Labels are organized into "sheets". By default a sheet spans the full page
  width (as many 8in columns as fit between the margins, with no horizontal
  gap) and is 8 rows down. Adjacent labels within a sheet share left/right and
  top/bottom edges.
- Sheets are placed in row-major order: sheet 0 top-left, sheet 1 top-right,
  sheet 2 below sheet 0, sheet 3 below sheet 1, and so on. How many sheets fit
  side-by-side is derived from the page width; the leftover width is split
  evenly between the horizontal gaps (so with 3+ sheets per row the last
  sheet's right edge lands on the right page margin), and sheet separators
  sit at each gap's midpoint.
- Sheet rows stack vertically, separated by `VERTICAL_GAP_IN`.
- A sheet size of `0` means "auto": `--labels-per-sheet-row 0` (the default)
  makes a single sheet filling the page width with no horizontal gap;
  `--labels-per-sheet-col 0` makes a single sheet of unbounded height with no
  vertical gap.
- `--vertical-labels` rotates each label's border and text 90° clockwise so the
  text reads top-to-bottom (turn your head clockwise to read it). The label
  keeps its internal design, so `--label-width`/`--label-height` still describe
  the *unrotated* label and the on-page footprint swaps width/height (8×3in
  labels become 3×8in footprints). When both sheet counts are fixed the grid
  transposes too (a 3×8 grid becomes 8×3); an auto (`0`) count instead stays on
  its own page axis, so vertical labels still fill the page width / flow
  unbounded with the default flags.

### Input format

A plain text file with one label code per line. Lines starting with `#` are
treated as comments. Example:

```text
# Labels: 8in X 3in with 1/2in margins all around; hairline border
# Text: 2in tall bold Arial, compressed horizontally as necessary
TUF-1A
TUF-1B
TUT-4A
```

### Usage

```sh
# Use the default output path (<input>.pdf next to the input)
uv run python scripts/vinyl_label_prep.py -i "data/input.txt"

# Write to a custom location; sibling *_report.txt / *_report.json are also produced
uv run python scripts/vinyl_label_prep.py -i input.txt -o out/labels.pdf

# Process every file matching a glob (quote it), e.g. one length bucket per file
uv run python scripts/vinyl_label_prep.py -i "data/output/*6-chars.txt"

# Override any layout option; defaults are the values described above
uv run python scripts/vinyl_label_prep.py -i input.txt \
    --label-height 4 --copies 3 --page-width 60 --no-border
```

`-i/--input` may be a glob pattern; every matching file is processed in sorted
order (`-o/--output` is only allowed when the input matches a single file).

### Options

`-i/--input` is the only required flag. Every layout constant in the script is
also an optional flag defaulting to its defined value, e.g. `--page-width`,
`--page-left-margin` / `--page-right-margin` / `--page-top-margin` /
`--page-bottom-margin`, `--label-width`, `--label-height`,
`--label-h-margin`, `--label-v-margin`, `--labels-per-sheet-row` (0 = auto:
fill the page width, the default), `--labels-per-sheet-col` (0 = auto: one
unbounded sheet), `--vertical-gap`, `-c/--copies`, `--vertical-labels` (rotate
labels 90° clockwise), `--border` / `--no-border`, `--border-line-width`,
`--text-height`, `--cap-height-ratio`, plus `--font` to draw with a specific
TTF instead of probing for Arial Bold. Run with `-h` for full help.

### Output

For each run the script writes three files:

1. **PDF** — the print-ready label layout, sized to the 52in-wide roll.
   (`--vertical-labels` appends `_vertical` to the PDF name.)
2. **`*_report.txt`** — a human-readable metrics report covering:
   - Total substrate required (sq in & linear feet of a 52in roll)
   - Total label area and material yield (%)
   - Total ink area and average ink coverage per label (%)
   - Total character count
   - Per-label breakdown table (label code, chars, horizontal scale, ink area)
3. **`*_report.json`** — the same data in machine-readable form, consumed by
   `vinyl_label_multi_job_report.py` below.

Ink area is computed via path integration (Green's theorem) over the TrueType
glyph outlines using `fontTools.pens.areaPen.AreaPen`. If Arial Bold is not
found on the system, the script falls back to ReportLab's built-in Helvetica
and estimates ink area from the natural text bounding box.

---

## `vinyl_label_multi_job_report.py`

Consolidates the metrics of every `*_report.json` file in a directory (the
machine-readable reports emitted by `vinyl_label_prep.py`) into one combined
report covering the total material yield and ink usage across all jobs, in the
same style as the per-job reports.

Percentages are recomputed from the summed areas (averaging the per-job
percentages would weight them wrongly), and the linear footage sums each job's
own roll length, so jobs with different page widths consolidate correctly.

### Usage

```sh
uv run python scripts/vinyl_label_multi_job_report.py <directory>
uv run python scripts/vinyl_label_multi_job_report.py <directory> -o out/combined.txt
```

- `<directory>` — directory containing `*_report.json` files from
  `vinyl_label_prep.py`.
- `-o/--output` — path for the consolidated text report (default:
  `<directory>/vinyl_labels_combined.txt`). A JSON sibling (same name, `.json`
  suffix) is always written next to it.

Consolidated reports carry a `report_type: "consolidated"` marker in their
`job` section and are skipped on later runs, so re-running the script over the
same directory is idempotent.

### Output

The human-readable report contains:

- **Global printing metrics** — total substrate (sq in / sq ft / linear feet
  per roll width), total label area, and material yield.
- **Ink usage metrics** — total ink area, average ink coverage, total
  character count, total output labels.
- **Label size breakdown** — per size (W×H): number of jobs, number of labels,
  plus a total row.
- **Job breakdown** — one row per contributing job: input file, roll width,
  labels, linear feet, and ink area.
- **Per-label breakdown** — one row per label code summed over every printed
  instance across all jobs: jobs appearing in, copies, characters, and ink area.

The JSON report mirrors the per-job schema (`job` metadata plus `global` and
`per_label` sections) and adds a `jobs` array carrying each contributing job's
own totals, so downstream tools can still attribute usage per job.

### Typical pipeline

```sh
# 1. See the length distribution, split into per-length files
uv run python scripts/count_line_lengths.py "data/input/full_label_lists"
uv run python scripts/split_lines_by_length.py "data/input/full_label_lists" 6,8,11,14,21

# 2. Generate a PDF + reports per length bucket
uv run python scripts/vinyl_label_prep.py -i "data/input/full_label_lists/output/*.txt"

# 3. Consolidate all jobs' metrics into one report
uv run python scripts/vinyl_label_multi_job_report.py "data/input/full_label_lists/output"
```
