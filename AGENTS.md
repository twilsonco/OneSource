# AGENTS.md

Guidance for AI coding agents and human contributors working in this repository.

## Project Overview

This workspace contains utilities for **preparing printing jobs for labels and tags** (e.g. layouts for large industrial label printers). Most work is delivered as **one-off scripts** in `scripts/` that read inputs from `data/` and emit print-ready output (PDF, PostScript, or similar).

## Environment

- **Python**: pinned via `.python-version` (currently `>=3.13`). Use `uv` for everything — never call `pip` directly.
- **Package manager**: [uv](https://docs.astral.sh/uv/)
- **Linter / formatter**: [ruff](https://docs.astral.sh/ruff/)
- **Type checker**: [mypy](https://mypy.readthedocs.io/)

## Common Commands

All commands run from the repository root.

```sh
# Sync the virtualenv from pyproject.toml / uv.lock
uv sync

# Run a one-off script
uv run python scripts/<name>.py
uv run python -m scripts.<name>     # if the script has a module wrapper

# Add a dependency (prefer this over hand-editing pyproject.toml)
uv add <package>
uv add --dev <package>              # dev-only (ruff, mypy, etc. live here)

# Lint, format, and type-check
uv run ruff check .                 # lint
uv run ruff format .                # format
uv run mypy .                       # type-check
```

If `ruff` / `mypy` are not yet declared as dev dependencies, add them:

```sh
uv add --dev ruff mypy
```

## Conventions for Scripts

- **One-off scripts** belong in `scripts/`. Give each a descriptive filename (e.g. `wic_8x3_layout.py`, not `script1.py`).
- **Inputs** live in `data/` (e.g. `2027-7-2 WIC.txt`). Read them with `pathlib.Path`, never hard-code absolute paths.
- **Output** (PDF, PS, etc.) should be written to a predictable location — usually a sibling of the input, or a dedicated `out/` folder created on demand.
- Keep scripts **standalone and re-runnable**: parse CLI args with `argparse` or `sys.argv`, and accept input/output paths as flags rather than baking them in.
- Prefer **stdlib** (`csv`, `pathlib`, `argparse`, `dataclasses`) for one-offs. Add a real dependency only when it pays for itself (e.g. `reportlab`, `pillow`, `pypdf`).
- Prefer **`fpdf2`** or **`reportlab`** for PDF generation when a layout is non-trivial. For raw PostScript, generate text and pipe to `enscript`/`a2ps` or write PS directly when needed.
- Label layout work (e.g. the `2027-7-2 WIC.txt` job) should be expressed in **inches or points with named constants**, not magic numbers:
  ```python
  LABEL_W_IN = 8.0
  LABEL_H_IN = 3.0
  MARGIN_IN = 0.5
  PAGE_W_IN = 52.0
  ```
- Keep measurements in one unit per script and convert once at the boundary (PDF libraries typically want millimeters or points).

## Code Style

- **Formatter / linter**: ruff with default rules. Run `uv run ruff format .` before committing.
- **Type hints**: required on all new code. `mypy` runs in strict mode — no `Any` unless justified with a comment.
- **Naming**: `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_SNAKE` for constants and unit suffixes (`LABEL_W_IN`).
- **Docstrings**: short module-level docstring stating *what* the script produces and *what* input it expects. Function docstrings only when the name doesn't say it all.

## Data Files

- Files in `data/` are **inputs to jobs**, not source code. Do not edit them as part of a code change unless the task is explicitly about that data.
- When adding a new job, drop a copy of the input in `data/` with a date-prefixed, descriptive name.

## Testing

This is a scripts-first repo — there is no `tests/` directory by design. For non-trivial scripts, add a small `if __name__ == "__main__"` block or a `def main()` that can be exercised manually, and keep the logic in pure functions so it's easy to spot-check from a REPL:

```sh
uv run python -c "from scripts.wic_layout import build_page; print(build_page(...))"
```

## Git

- Keep changes scoped. One script per commit when possible.
- Commit messages: short imperative summary, e.g. `scripts: add 8x3 WIC label layout`.
- Do not commit `.venv/`, `__pycache__/`, or generated PDFs/PS files.