# OneSource

Utility scripts for organizing label/tag printing jobs and generating
print-ready PDFs for vinyl label printing (large industrial label printers,
e.g. 52in-wide rolls). Scripts live in [`scripts/`](scripts/), read inputs
from [`data/`](data/), and emit PDFs plus metrics reports.

Detailed per-script documentation lives in [`docs/`](docs/).

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

| Script | Purpose | Docs |
| --- | --- | --- |
| `scripts/count_line_lengths.py` | Histogram of per-line character counts across the `.txt` files in a directory (per-file and combined tables). | [`docs/count_line_lengths.md`](docs/count_line_lengths.md) |
| `scripts/split_lines_by_length.py` | Split the `.txt` files in a directory into per-length-range output files, optionally merging chosen ranges across all inputs. | [`docs/split_lines_by_length.md`](docs/split_lines_by_length.md) |
| `scripts/vinyl_label_prep.py` | Lay out vinyl labels (default 8in × 3in) on a 52in-wide page and emit a print-ready PDF plus per-job metrics reports (text + JSON). | [`docs/vinyl_label_prep.md`](docs/vinyl_label_prep.md) |
| `scripts/vinyl_label_multi_job_report.py` | Consolidate the `*_report.json` metrics of many layout jobs into one combined material/ink report. | [`docs/vinyl_label_prep.md`](docs/vinyl_label_prep.md) |

A typical vinyl label job flows: count line lengths → split tag lists by length →
generate a PDF per bucket with `vinyl_label_prep.py` → consolidate metrics
with `vinyl_label_multi_job_report.py`. See
[`docs/vinyl_label_prep.md`](docs/vinyl_label_prep.md) for the full pipeline
example.

## Project layout

```
.
├── AGENTS.md            # Conventions and common commands
├── README.md            # This file
├── docs/                # Per-script documentation
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
