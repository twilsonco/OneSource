# AGENTS.md

Guidance for AI coding agents and human contributors working in this repository.

## Project Overview

This workspace contains utilities for **preparing printing jobs for labels** (e.g. layouts for large industrial label printers).

The vinyl label pipeline is a real package: **`src/vinyllabels/`**, installed
editable by `uv sync` and exposed as the **`generate`** and **`consolidate`**
console tools (`uv run generate`, `uv run consolidate`). It reads inputs from
`data/` and emits print-ready output (PDF, PostScript, or similar).

Everything else is delivered as **one-off scripts** in `scripts/`. Shared or
reused logic belongs in `src/vinyllabels/`; a genuinely one-shot job stays a
standalone script.

## Environment

- **Python**: pinned via `.python-version` (currently `>=3.13`). Use `uv` for everything — never call `pip` directly.
- **Package manager**: [uv](https://docs.astral.sh/uv/)
- **Linter / formatter**: [ruff](https://docs.astral.sh/ruff/)
- **Type checker**: [mypy](https://mypy.readthedocs.io/)
- **Pre-commit hooks**: [pre-commit](https://pre-commit.com/) runs ruff format, ruff check, and mypy on every `git commit` (config: `.pre-commit-config.yaml`).

## Common Commands

All commands run from the repository root.

```sh
# Sync the virtualenv from pyproject.toml / uv.lock (also installs the package)
uv sync

# Run the vinyl label tools (console scripts from pyproject.toml)
uv run generate -i data/input.txt
uv run consolidate data/json
uv run python -m vinyllabels.generate        # module form, same thing

# Run a one-off script
uv run python scripts/<name>.py

# Add a dependency (prefer this over hand-editing pyproject.toml)
uv add <package>
uv add --dev <package>              # dev-only (ruff, mypy, etc. live here)

# Lint, format, and type-check
uv run ruff check .                 # lint
uv run ruff format .                # format
uv run mypy .                       # type-check

# Pre-commit hooks (ruff format + ruff check + mypy)
uv run pre-commit install           # one-time setup per clone
uv run pre-commit run --all-files   # run all hooks on every file
uv run pre-commit run               # run all hooks on staged files
```

If `ruff` / `mypy` are not yet declared as dev dependencies, add them:

```sh
uv add --dev ruff mypy
```

## Conventions for Scripts

- **One-off scripts** belong in `scripts/`. Give each a descriptive filename (e.g. `vinyl_label_prep.py`, not `script1.py`).
- **Shared logic** belongs in `src/vinyllabels/`. When logic gets reused, move it into the package instead of copy-pasting it; new modules go under the subpackage that owns the concern (`reportio/` for report writing, `consolidate/` for multi-job work).
- **Inputs** live in `data/` (e.g. `2027-7-2 labels.txt`). Read them with `pathlib.Path`, never hard-code absolute paths.
- **Output** (PDF, PS, etc.) should be written to a predictable location — usually a sibling of the input, or a dedicated `out/` folder created on demand.
- Keep scripts **standalone and re-runnable**: parse CLI args with `argparse` or `sys.argv`, and accept input/output paths as flags rather than baking them in.
- Prefer **stdlib** (`csv`, `pathlib`, `argparse`, `dataclasses`) for one-offs. Add a real dependency only when it pays for itself (e.g. `reportlab`, `pillow`, `pypdf`).
- Prefer **`fpdf2`** or **`reportlab`** for PDF generation when a layout is non-trivial. For raw PostScript, generate text and pipe to `enscript`/`a2ps` or write PS directly when needed.
- Label layout work (e.g. the `2027-7-2 labels.txt` job) should be expressed in **inches with named constants**, not magic numbers:
  ```python
  LABEL_W_IN = 8.0
  LABEL_H_IN = 3.0
  MARGIN_IN = 0.5
  PAGE_W_IN = 52.0
  ```
- Keep measurements in one unit per script and convert once at the boundary (PDF libraries typically want millimeters or points).

## Code Style

- **Formatter / linter**: ruff with default rules. Enforced automatically by the pre-commit hooks (ruff format, then ruff check with `--fix`).
- **Type hints**: required on all new code. `mypy` runs in strict mode — no `Any` unless justified with a comment.
- **Naming**: `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_SNAKE` for constants and unit suffixes (`LABEL_W_IN`).
- **Docstrings**: short module-level docstring stating *what* the script produces and *what* input it expects. Function docstrings only when the name doesn't say it all.

## Data Files

- Files in `data/` are **inputs to jobs**, not source code. Do not edit them as part of a code change unless the task is explicitly about that data.
- When adding a new job, drop a copy of the input in `data/` with a date-prefixed, descriptive name.

## Testing

This is a scripts-first repo — there is no `tests/` directory by design. Keep logic in pure functions so it is easy to spot-check from a REPL, and give every script a `def main()` (plus an `if __name__ == "__main__"` block) that can be exercised manually:

```sh
uv run python -c "from vinyllabels.layout import JobConfig; print(JobConfig(input_path=__import__('pathlib').Path('.')))"
```

For anything that changes report output, verify by **differential testing**
rather than by eye: copy `data/` to two temp directories, run the pre-change
code against one and your change against the other with the same
`--pricing-config`, then `diff -r`. Ignore timestamp lines, echoed input paths,
and PDF creation dates; everything else should match byte-for-byte.

## Workflow: run pre-commit hooks after each change

After each change is **confirmed working** (script runs, output spot-checked), run the pre-commit hooks before moving on or committing:

```sh
uv run pre-commit run            # staged files (what `git commit` will run)
# or, when files aren't staged yet:
uv run pre-commit run --all-files
```

The hooks run ruff format, ruff check (with `--fix`), and mypy. If a hook modifies files (e.g. reformatting), re-verify the change still works, then re-run the hooks until they pass. Do not leave the repo in a state where `git commit` would fail the hooks.

## Git

- Keep changes scoped. One script per commit when possible.
- Commit messages: short imperative summary, e.g. `scripts: add vinyl label layout prep`.
- Do not commit `.venv/`, `__pycache__/`, or generated PDFs/PS files.