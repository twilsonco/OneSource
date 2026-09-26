# Vinyl label layout & job reports

Two tools form the vinyl label printing pipeline:

- `generate` ([`src/vinyllabels/generate.py`](../src/vinyllabels/generate.py)) —
  lays out label codes on a wide print page, emits a print-ready PDF plus
  per-job metrics reports.
- `consolidate` ([`src/vinyllabels/consolidate/cli.py`](../src/vinyllabels/consolidate/cli.py)) —
  consolidates the metrics of many jobs into one combined report.

Both are console scripts installed by `uv sync`, and both also run as modules
(`uv run python -m vinyllabels.generate`,
`uv run python -m vinyllabels.consolidate.cli`).

---

## `generate`

Lays out vinyl labels (8in × 3in) on a 52in-wide print page and emits a
print-ready PDF, alongside a text report covering material yield and ink
usage.

### Layout

- Each label is 8in × 3in with 1/4in side and 1/2in top/bottom margins inside
  it (`--label-h-margin` / `--label-v-margin`); hairline border
  (edges shared between adjacent labels are drawn once, never doubled).
- Text is 2in tall bold Arial, compressed horizontally if needed.
- Page is 52in wide with 1in top/bottom margins and no left/right margins, so
  labels run edge-to-edge across the roll unless you ask for margins.
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
- Sheet rows stack vertically, separated by `--vertical-gap` (2in by default).
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
- A page is as tall as its content: sheets stack downwards until the next one
  would take the page past `--soft-page-height` (80in by default), then the
  remaining labels continue on the next PDF. The limit is soft — a page that
  already exceeds it still gets its next full sheet-row, and a single
  sheet-row taller than the limit is printed as-is — and a page is only closed
  when the *whole* remainder stops fitting, so a job never splits into two
  short pages that could have been one. Every page is then trimmed to the rows
  it actually uses, so a partial trailing sheet leaves no blank space.

### Input format

A plain text file with one label code per line. Lines starting with `#` are
treated as comments. Example:

```text
# Labels: 8in X 3in with 1/2in top/bottom margins; hairline border
# Text: 2in tall bold Arial, compressed horizontally as necessary
TUF-1A
TUF-1B
TUT-4A
```

### Usage

```sh
# Use the default output path (PDF Files/<input>.pdf next to the input)
uv run generate -i "data/input.txt"

# Write the PDF somewhere else; the reports are still written beside the input,
# in Job Report/, json/ and csv/
uv run generate -i input.txt -o out/labels.pdf

# Process every file matching a glob (quote it), e.g. one length bucket per file
uv run generate -i "data/output/*6-chars.txt"

# Override any layout option; defaults are the values described above
uv run generate -i input.txt \
    --label-height 4 --copies 3 --page-width 60 --no-border
```

`-i/--input` may be a glob pattern; every matching file is processed in sorted
order (`-o/--output` is only allowed when the input matches a single file).

### Options

`-i/--input` is the only required flag. Every layout constant in the tool is
also an optional flag defaulting to its defined value, grouped in `--help` as:

- **page** — `--page-width`, `--page-left-margin` / `--page-right-margin` /
  `--page-top-margin` / `--page-bottom-margin`.
- **label** — `--label-width`, `--label-height`, `--label-h-margin`,
  `--label-v-margin`, `--vertical-labels` / `--no-vertical-labels`.
- **sheet** — `--labels-per-sheet-row` (0 = auto: fill the page width, the
  default), `--labels-per-sheet-col` (0 = auto: one unbounded sheet),
  `--vertical-gap`, `--soft-page-height`.
- **output** — `-c/--copies`, `--border` / `--no-border`,
  `--border-line-width`, `--border-color`, `--sheet-separators` /
  `--no-sheet-separators`, `--sheet-separator-color`.
- **text** — `--text-height`, `--cap-height-ratio`, `--text-color`.
- **pricing** — see the Pricing section of the
  [README](../README.md#pricing).

Colours accept the presets `black`/`k`, `blue`/`b`, `green`/`g`, `red`/`r`,
`orange`/`o`, `yellow`/`y`, `violet`/`v`, `magenta`/`m`, an RGB triple
(`12,60,200`), or 6 hex digits (`003cc8`). `--font` draws with a specific TTF
instead of probing for Arial Bold. Run with `-h` for full help, which also
prints every default.

### Output

For each run the tool writes:

1. **PDF** — the print-ready label layout, sized to the 52in-wide roll, in
   `PDF Files/` next to the input. (`--vertical-labels` appends `_vertical`
   to the name.) The name embeds the label size and count so printed sheets
   are identifiable without opening them — `OGA9_6-chars_10x4_72-labels.pdf`
   — and a job split across pages numbers them in print order:
   `OGA9_6-chars_10x4_part-001_72-labels.pdf`, `…_part-002_48-labels.pdf`.
   `generate` never deletes previous output, so clear `PDF Files/` before
   re-running a job with different layout flags.
2. **`Job Report/*_report.txt`** — a human-readable metrics report covering:
   - Total substrate required (sq in & linear feet of a 52in roll)
   - Total label area and material yield (%)
   - Total ink area and average ink coverage per label (%)
   - Total character count
   - Per-label breakdown table (label code, chars, horizontal scale, ink area)
3. **`json/*_report.json`** — the same data in machine-readable form, consumed
   by `consolidate` below.
4. **`csv/*_report.csv` / `csv/*_report_customer.csv`** — the metrics table as
   CSV, in the internal and the customer column subset.

Ink area is computed via path integration (Green's theorem) over the TrueType
glyph outlines using `fontTools.pens.areaPen.AreaPen`. If Arial Bold is not
found on the system, the tool falls back to ReportLab's built-in Helvetica
and estimates ink area from the natural text bounding box.

---

## `consolidate`

Consolidates the metrics of every `*_report.json` file in a directory (the
machine-readable reports emitted by `generate`) into one combined report
covering the total material yield and ink usage across all jobs, in the same
style as the per-job reports.

Percentages are recomputed from the summed areas (averaging the per-job
percentages would weight them wrongly), and the linear footage sums each job's
own roll length, so jobs with different page widths consolidate correctly.

### Usage

```sh
uv run consolidate <directory>
uv run consolidate <directory> -o out/combined.txt
```

- `<directory>` — directory containing `*_report.json` files from `generate`
  (usually the input job's `json/` folder).
- `-o/--output` — path for the consolidated text report (default:
  `Multi-Job Report/vinyl_labels_combined.txt`, a sibling of `<directory>`).
  Every other artifact keeps its default location regardless; only the JSON
  sibling (same name, `.json` suffix) follows `-o`.

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

Alongside the text report, every run writes the breakdown set into
`Multi-Job Report/` next to the input directory (its `csv/` and `json/`
siblings hold the machine-readable halves) — the per-label listings as text
and XLSX, and CSV breakdowns by size, job, PDF file, and label code. Each
comes in an internal version, a customer version (columns gated by the
`customer_report` switches in `pricing-config.json`), and, where a vendor would
see it, a cost-free vendor version. Emitted PDFs are also copied into
`PDF Files for Vendor/` as `1.pdf`, `2.pdf`, … in the same size-then-name order
the vendor CSVs use.

### Typical pipeline

```sh
# 1. See the length distribution, split into per-length files
uv run python scripts/count_line_lengths.py "data/input/full_label_lists"
uv run python scripts/split_lines_by_length.py "data/input/full_label_lists" 6,8,11,14,21

# 2. Generate a PDF + reports per length bucket (one layout per bucket)
uv run generate -i "data/input/full_label_lists/output/*6-chars.txt" \
    --page-width 50 --label-width 10 --label-height 4 --text-height 3 \
    --vertical-labels --labels-per-sheet-row 3 --labels-per-sheet-col 6
uv run generate -i "data/input/full_label_lists/output/*8-chars.txt" \
    --page-width 50 --label-width 12 --label-height 4 --text-height 3 \
    --vertical-labels --labels-per-sheet-row 3 --labels-per-sheet-col 6

# 3. Consolidate all jobs' metrics into one report
uv run consolidate "data/input/full_label_lists/output"
```
