# OneSource

Utilities for preparing printing jobs for labels and tags (e.g. layouts for
large industrial label printers). Most work is delivered as one-off scripts in
[`scripts/`](scripts/) that read inputs from [`data/`](data/) and emit
print-ready output (PDF, PostScript, or similar).

## Requirements

- Python `>=3.13` (pinned via `.python-version`)
- [uv](https://docs.astral.sh/uv/) for dependency management

## Setup

From the repository root:

```sh
# Sync the virtualenv from pyproject.toml / uv.lock
uv sync
```

This installs the runtime dependencies (`reportlab`, `fonttools`) and the dev
tooling (`ruff`, `mypy`, `types-reportlab`, `pypdf`).

## Scripts

### `scripts/wic_label_layout.py`

Lays out WIC labels (8in × 3in) on a 52in-wide print page and emits a
print-ready PDF, alongside a text report covering material yield and ink
usage.

**Layout**

- Each label is 8in × 3in with 1/2in margins on all sides; hairline border.
- Text is 2in tall bold Arial, compressed horizontally if needed.
- Page is 52in wide with 1in margins on all four sides.
- Each label is printed twice (`2X`).
- Labels are organized into "sheets". By default a sheet spans the full page
  width (as many 8in columns as fit between the margins, with no horizontal
  gap) and is 8 rows down. Adjacent labels within a sheet share left/right and
  top/bottom edges.
- Sheets are placed in row-major order: sheet 0 top-left, sheet 1 top-right,
  sheet 2 below sheet 0, sheet 3 below sheet 1, and so on. How many sheets fit
  side-by-side is derived from the page width; the horizontal gap between them
  absorbs the leftover width.
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

**Input format**

A plain text file with one label code per line. Lines starting with `#` are
treated as comments. Example (`data/2027-7-2 WIC.txt`):

```text
# Labels: 8in X 3in with 1/2in margins all around; hairline border
# Text: 2in tall bold Arial, compressed horizontally as necessary
TUF-1A
TUF-1B
TUT-4A
```

**Usage**

```sh
# Use the default output path (<input>.pdf next to the input)
uv run python scripts/wic_label_layout.py -i "data/2027-7-2 WIC.txt"

# Write to a custom location; a sibling *_report.txt is also produced
uv run python scripts/wic_label_layout.py -i input.txt -o out/labels.pdf

# Override any layout option; defaults are the values described above
uv run python scripts/wic_label_layout.py -i input.txt \
    --label-height 4 --copies 3 --page-width 60 --no-border
```

**Options**

`-i/--input` is the only required flag. Every layout constant in the script is
also an optional flag defaulting to its defined value, e.g. `--page-width`,
`--page-left-margin` / `--page-right-margin` / `--page-top-margin` /
`--page-bottom-margin`, `--label-width`, `--label-height`,
`--label-h-margin`, `--label-v-margin`, `--labels-per-sheet-row` (0 = auto: fill the page width, the default),
`--labels-per-sheet-col` (0 = auto: one unbounded sheet), `--vertical-gap`,
`-c/--copies`, `--vertical-labels` (rotate labels 90° clockwise),
`--border` / `--no-border`, `--border-line-width`, `--text-height`,
`--cap-height-ratio`, plus `--font` to draw with a specific TTF instead of
probing for Arial Bold. Run with `-h` for full help.

**Output**

For each run the script writes two files:

1. **PDF** — the print-ready label layout, sized to the 52in-wide roll.
2. **`*_report.txt`** — a metrics report covering:
   - Total substrate required (sq in & linear feet of a 52in roll)
   - Total label area and material yield (%)
   - Total ink area and average ink coverage per label (%)
   - Total character count
   - Per-tag breakdown table (label code, chars, horizontal scale, ink area)

Ink area is computed via path integration (Green's theorem) over the TrueType
glyph outlines using `fontTools.pens.areaPen.AreaPen`. If Arial Bold is not
found on the system, the script falls back to ReportLab's built-in Helvetica
and estimates ink area from the natural text bounding box.

## Project layout

```
.
├── AGENTS.md            # Conventions and common commands
├── README.md            # This file
├── pyproject.toml       # Project metadata and dependencies
├── data/                # Input files for jobs (date-prefixed, descriptive)
└── scripts/             # One-off scripts, one per job
```

## Development

All commands run from the repository root.

```sh
# Lint, format, and type-check
uv run ruff check .
uv run ruff format .
uv run mypy .

# Run a script
uv run python scripts/<name>.py
```

See [`AGENTS.md`](AGENTS.md) for the full set of conventions (script style,
naming, data file handling, etc.).