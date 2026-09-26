"""Typed accessors for reading ``*_report.json`` files.

Job reports are written by this project but consumed long after, possibly by a
different version, so loading validates every field it depends on and reports
exactly which file and key is wrong. Errors raise :class:`ReportLoadError`,
which the CLIs turn into a clean exit message.
"""

from __future__ import annotations

import json
from pathlib import Path

from vinyllabels.models import CostBreakdown

__all__ = [
    "ReportLoadError",
    "as_entry_list",
    "as_float",
    "as_int",
    "as_list",
    "as_section",
    "as_str",
    "cost_breakdown_from_json",
    "field",
    "load_json_object",
]


class ReportLoadError(Exception):
    """A report file is missing, malformed, or missing a required field."""


def load_json_object(path: Path) -> dict[str, object]:
    """Read ``path`` as JSON and return its top-level object."""
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportLoadError(f"Could not read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ReportLoadError(f"{path}: top level is not a JSON object")
    return {str(k): v for k, v in raw.items()}


def field(mapping: dict[str, object], key: str, path: Path) -> object:
    """Return ``mapping[key]`` or raise naming the offending report."""
    if key not in mapping:
        raise ReportLoadError(f"{path}: missing '{key}'")
    return mapping[key]


def as_float(mapping: dict[str, object], key: str, path: Path) -> float:
    """Return ``mapping[key]`` as a float or raise."""
    value = field(mapping, key, path)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ReportLoadError(f"{path}: '{key}' is not a number (got {value!r})")
    return float(value)


def as_int(mapping: dict[str, object], key: str, path: Path) -> int:
    """Return ``mapping[key]`` as an int or raise."""
    value = field(mapping, key, path)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReportLoadError(f"{path}: '{key}' is not an integer (got {value!r})")
    return int(value)


def as_str(mapping: dict[str, object], key: str, path: Path) -> str:
    """Return ``mapping[key]`` as a string or raise."""
    value = field(mapping, key, path)
    if not isinstance(value, str):
        raise ReportLoadError(f"{path}: '{key}' is not a string (got {value!r})")
    return value


def as_section(report: dict[str, object], key: str, path: Path) -> dict[str, object]:
    """Return ``report[key]`` as an object or raise."""
    value = field(report, key, path)
    if not isinstance(value, dict):
        raise ReportLoadError(f"{path}: '{key}' is not a JSON object")
    return {str(k): v for k, v in value.items()}


def as_list(report: dict[str, object], key: str, path: Path) -> list[object]:
    """Return ``report[key]`` as an array or raise."""
    value = field(report, key, path)
    if not isinstance(value, list):
        raise ReportLoadError(f"{path}: '{key}' is not a JSON array")
    return value


def as_entry_list(
    entries: list[object], key: str, path: Path
) -> list[dict[str, object]]:
    """Return ``entries`` as a list of objects, raising on the first non-object."""
    items: list[dict[str, object]] = []
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ReportLoadError(f"{path}: '{key}[{idx}]' is not a JSON object")
        items.append({str(k): v for k, v in entry.items()})
    return items


_COST_KEYS = (
    "ink_cost",
    "substrate_cost",
    "printer_hours",
    "printer_cost",
    "labor_hours",
    "labor_cost",
    "total_cost",
    "unit_price",
)


def cost_breakdown_from_json(
    section: dict[str, object],
) -> CostBreakdown | None:
    """Read a cost breakdown out of a report section.

    Accepts both shapes the writers have produced: a nested ``cost_breakdown``
    object, or the eight numbers flattened into ``section``. Returns ``None``
    rather than raising when they are absent or non-numeric, since "this report
    was written without pricing" is a normal state, not a malformed file.
    """
    source = section.get("cost_breakdown")
    target = source if isinstance(source, dict) else section
    data: dict[str, object] = {str(k): v for k, v in target.items()}

    values: dict[str, float] = {}
    for key in _COST_KEYS:
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        values[key] = float(value)

    return CostBreakdown(
        ink_cost=values["ink_cost"],
        substrate_cost=values["substrate_cost"],
        printer_hours=values["printer_hours"],
        printer_cost=values["printer_cost"],
        labor_hours=values["labor_hours"],
        labor_cost=values["labor_cost"],
        total_cost=values["total_cost"],
        unit_price=values["unit_price"],
    )
