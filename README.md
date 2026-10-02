# OneSource

Utility scripts for organizing label printing jobs and generating
print-ready PDFs for vinyl label printing (large industrial label printers,
e.g. 52in-wide rolls). The vinyl label pipeline lives in the
[`vinyllabels`](src/vinyllabels) package and runs as the `generate` and
`consolidate` tools; smaller one-offs live in [`scripts/`](scripts/). Both read
inputs from [`data/`](data/) and emit PDFs plus metrics reports.

Artifacts are written **beside the input file**, not into the working
directory: a job's `PDF Files/`, `Job Report/`, `json/` and `csv/` land next to
the `.txt` it was generated from, and `consolidate` writes its `Multi-Job
Report/` next to the `json/` directory it read. `-o` moves only the PDF
(`generate`) or the text report and its JSON sibling (`consolidate`) — every
other artifact keeps its folder.

A job taller than `--soft-page-height` (80in by default) is split into several
numbered PDFs, so changing the layout flags and re-running can leave earlier
sheets behind in `PDF Files/` — clear that folder before printing a re-run.

Detailed per-tool documentation lives in [`docs/`](docs/).

## Requirements

- Python `>=3.13` (pinned via `.python-version`)
- [uv](https://docs.astral.sh/uv/) for dependency management

## Setup

From the repository root:

```sh
# Sync the virtualenv from pyproject.toml / uv.lock
uv sync
```

This installs the runtime dependencies (`reportlab`, `fonttools`, `openpyxl`),
the dev tooling (`ruff`, `mypy`, `pytest`, `pytest-cov`, `pre-commit`, `pypdf`,
`types-openpyxl`, `types-reportlab`), and the `onesource` project itself in
editable mode — which is what puts `generate` and `consolidate` on the
`uv run` PATH.

Then install the git hooks, once per clone:

```sh
uv run pre-commit install
```

The hooks run ruff format, ruff check, mypy and pytest on every commit.

## Tools

| Tool | Purpose | Docs |
| --- | --- | --- |
| `uv run generate` | Lay out vinyl labels (default 8in × 3in) on a 52in-wide page and emit a print-ready PDF plus per-job metrics reports (text, JSON, CSV). | [`docs/vinyl_label_prep.md`](docs/vinyl_label_prep.md) |
| `uv run consolidate` | Consolidate the `*_report.json` metrics of many layout jobs into one combined material/ink report, plus every CSV/XLSX breakdown. | [`docs/vinyl_label_prep.md`](docs/vinyl_label_prep.md) |

Both also run as modules: `uv run python -m vinyllabels.generate` and
`uv run python -m vinyllabels.consolidate.cli`.

### Cut-Ready PDFs for Plotter Integration

The `generate` tool produces **cut-ready PDFs by default**, with special spot color separations recognized by Roland VersaWorks and other RIP (Raster Image Processor) software for automated plotter cutting.

#### Spot Colors

Two spot color separations route cut paths directly to the plotter blade instead of the printer's ink nozzles:

- **CutContour** — For kiss-cuts (label borders). Drawn with Magenta visual fallback (0, 1, 0, 0 CMYK).
- **PerfCutContour** — For perforated or through-cuts (sheet separators). Drawn with Yellow visual fallback (0, 0, 1, 0 CMYK).

When Roland VersaWorks recognizes these spot colors, it displays animated "marching ants" around the cut paths to confirm plotter routing. If the RIP doesn't recognize the names, it renders the visual fallback colors (magenta borders, yellow separators) so the job still prints and cuts with visible guides.

#### Overprint

Both cut line types have **overprint enabled** (`/OPM 1` in PDF). This prevents the RIP from knocking out (erasing) the printable design under the cut lines, avoiding white halos where cuts meet printed artwork.

#### Usage

```bash
# Default: generate cut-ready PDFs (CutContour + PerfCutContour with overprint)
uv run generate -i labels.txt

# Disable cut-ready mode (revert to regular colors, no spot separations)
uv run generate -i labels.txt --no-cut-ready-borders --no-cut-ready-separators

# Mix: only CutContour for borders, regular color for separators
uv run generate -i labels.txt --no-cut-ready-separators

# Mix: only PerfCutContour for separators, regular color for borders
uv run generate -i labels.txt --no-cut-ready-borders
```

#### Verification

Use the inspection script to confirm cut-ready features in a generated PDF:

```bash
# Single file
python scripts/inspect_cut_contours.py PDF_Files/out_8x3_24-labels.pdf

# Entire directory
python scripts/inspect_cut_contours.py PDF_Files/

# Example output:
# out_8x3_24-labels.pdf: ✓ CutContour (kiss-cuts) | ✓ PerfCutContour (perf-cuts) | ✓ Overprint enabled
```

You can also verify in **Adobe Acrobat Pro**: Tools > Print Production > Output Preview. The spot colors should appear as independent separation plates (CMYK + CutContour + PerfCutContour).

### Pricing

Cost lines come from `pricing-config.json` in the repository root. The tools
find it by walking upward from the working directory, so they work from any
subdirectory; copy [`pricing-config.example.json`](pricing-config.example.json)
to get started (the real file is gitignored). Use `--pricing-config` to point
elsewhere, or override single figures with `--ink-cost`, `--substrate-cost`,
`--print-rate`, `--printer-cost`, `--labor-rate`, `--labor-factor`, `--markup`
and, for `generate`, `--flat-label-price`. With no config and no overrides the
tools still lay out and measure — they just report no costs.

## Scripts

| Script | Purpose | Docs |
| --- | --- | --- |
| `scripts/count_line_lengths.py` | Histogram of per-line character counts across the `.txt` files in a directory (per-file and combined tables). | [`docs/count_line_lengths.md`](docs/count_line_lengths.md) |
| `scripts/split_lines_by_length.py` | Split the `.txt` files in a directory into per-length-range output files, optionally merging chosen ranges across all inputs. | [`docs/split_lines_by_length.md`](docs/split_lines_by_length.md) |
| `scripts/inspect_cut_contours.py` | Inspect PDF files for CutContour and PerfCutContour spot colors and overprint settings. Useful for verifying cut-ready PDFs before sending to plotter. | |

A typical vinyl label job flows: count line lengths → split label lists by length →
generate a PDF per bucket with `generate` → consolidate metrics with
`consolidate`. See [`docs/vinyl_label_prep.md`](docs/vinyl_label_prep.md) for
the full pipeline example.

## Project layout

```
.
├── AGENTS.md                    # Conventions and common commands
├── README.md                    # This file
├── docs/                        # Per-tool and per-script documentation
├── pyproject.toml               # Metadata, dependencies, packaging, tool config
├── uv.lock                      # Resolved dependency versions
├── .pre-commit-config.yaml      # ruff format, ruff check, mypy, pytest
├── pricing-config.example.json  # Cost model template
├── data/                        # Input files for jobs (date-prefixed, descriptive)
├── scripts/                     # One-off scripts, one per job
├── src/vinyllabels/             # The vinyl label package (layout, metrics, reports)
└── tests/                       # pytest suite, mirrors src/ module for module
```

`src/vinyllabels/consolidate/` holds the multi-job half and
`src/vinyllabels/reportio/` the shared report-writing engine.

## Development

All commands run from the repository root.

```sh
# Lint, format, and type-check
uv run ruff check .
uv run ruff format .
uv run mypy .

# Tests (hermetic, under a second)
uv run pytest
uv run pytest tests/test_layout.py
uv run pytest --cov=vinyllabels --cov-report=term-missing

# Pre-commit hooks
uv run pre-commit run --all-files   # every hook, every file
uv run pre-commit run               # every hook, staged files only

# Run a tool
uv run generate -i data/input.txt
uv run consolidate data/json

# Run a script
uv run python scripts/<name>.py
```

The suite covers every line and branch of `src/vinyllabels/`, so a coverage
drop is a signal that new code shipped untested. Tests never touch the network
or the repository's `data/`.

See [`AGENTS.md`](AGENTS.md) for the full set of conventions (script style,
naming, data file handling, etc.).
