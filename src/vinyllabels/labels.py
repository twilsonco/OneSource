"""Reading label-code input files.

A label list is a plain text file with one code per line; blank lines and lines
starting with ``#`` are ignored.
"""

from __future__ import annotations

import glob
from pathlib import Path

__all__ = ["expand_input_paths", "is_glob_pattern", "parse_labels"]


def is_glob_pattern(pattern: str) -> bool:
    """Whether ``pattern`` contains glob metacharacters."""
    return any(char in pattern for char in "*?[")


def expand_input_paths(pattern: Path) -> list[Path]:
    """Return the input files matched by ``pattern``, in sorted order.

    A plain path is returned as-is (even if it does not exist, so the caller
    can report a useful error). A glob pattern is expanded with ``**``
    supported for recursive matching, and directories are dropped.
    """
    raw = str(pattern)
    if not is_glob_pattern(raw):
        return [pattern]
    matches = (Path(p) for p in glob.glob(raw, recursive=True))
    return sorted((p for p in matches if p.is_file()), key=lambda p: str(p))


def parse_labels(path: Path) -> list[str]:
    """Return the non-empty, non-comment label codes from ``path``."""
    labels: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        labels.append(line)
    return labels
