# OneSource

Utility scripts for organizing label printing jobs and generating
print-ready PDFs for vinyl label printing (large industrial label printers,
e.g. 52in-wide rolls). The vinyl label pipeline lives in the
[`vinyllabels`](src/vinyllabels) package and runs as the `generate` and
`consolidate` tools; smaller one-offs live in [`scripts/`](scripts/). Both read
inputs from [`data/`](data/) and emit PDFs plus metrics reports.

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
the dev tooling (`ruff`, `mypy`, `pre-commit`, `types-reportlab`, `pypdf`), and
the `onesource` project itself in editable mode — which is what puts
`generate` and `consolidate` on the `uv run` PATH.

## Tools

| Tool | Purpose | Docs |
| --- | --- | --- |
| `uv run generate` | Lay out vinyl labels (default 8in × 3in) on a 52in-wide page and emit a print-ready PDF plus per-job metrics reports (text, JSON, CSV). | [`docs/vinyl_label_prep.md`](docs/vinyl_label_prep.md) |
| `uv run consolidate` | Consolidate the `*_report.json` metrics of many layout jobs into one combined material/ink report, plus every CSV/XLSX breakdown. | [`docs/vinyl_label_prep.md`](docs/vinyl_label_prep.md) |

Both also run as modules: `uv run python -m vinyllabels.generate` and
`uv run python -m vinyllabels.consolidate.cli`.

## Scripts

| Script | Purpose | Docs |
| --- | --- | --- |
| `scripts/count_line_lengths.py` | Histogram of per-line character counts across the `.txt` files in a directory (per-file and combined tables). | [`docs/count_line_lengths.md`](docs/count_line_lengths.md) |
| `scripts/split_lines_by_length.py` | Split the `.txt` files in a directory into per-length-range output files, optionally merging chosen ranges across all inputs. | [`docs/split_lines_by_length.md`](docs/split_lines_by_length.md) |

A typical vinyl label job flows: count line lengths → split label lists by length →
generate a PDF per bucket with `generate` → consolidate metrics with
`consolidate`. See [`docs/vinyl_label_prep.md`](docs/vinyl_label_prep.md) for
the full pipeline example.

## Project layout

```
.
├── AGENTS.md            # Conventions and common commands
├── README.md            # This file
├── docs/                # Per-tool and per-script documentation
├── pyproject.toml       # Metadata, dependencies, packaging, tool config
├── data/                # Input files for jobs (date-prefixed, descriptive)
├── scripts/             # One-off scripts, one per job
└── src/vinyllabels/     # The vinyl label package (layout, metrics, reports)
```

`src/vinyllabels/consolidate/` holds the multi-job half and
`src/vinyllabels/reportio/` the shared report-writing engine.
[`docs/refactor.md`](docs/refactor.md) records how the package relates to the
two scripts it replaced.

## Development

All commands run from the repository root.

```sh
# Lint, format, and type-check
uv run ruff check .
uv run ruff format .
uv run mypy .

# Run a tool
uv run generate -i data/input.txt
uv run consolidate data/json

# Run a script
uv run python scripts/<name>.py
```

See [`AGENTS.md`](AGENTS.md) for the full set of conventions (script style,
naming, data file handling, etc.).
