"""Tests for :mod:`vinyllabels.reportio.loaders`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vinyllabels.models import CostBreakdown
from vinyllabels.reportio.loaders import (
    ReportLoadError,
    as_entry_list,
    as_float,
    as_int,
    as_list,
    as_section,
    as_str,
    cost_breakdown_from_json,
    field,
    load_json_object,
)

COST_KEYS = (
    "ink_cost",
    "substrate_cost",
    "printer_hours",
    "printer_cost",
    "labor_hours",
    "labor_cost",
    "total_cost",
    "unit_price",
)


def write_json(tmp_path: Path, payload: object, name: str = "r.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_json_object(tmp_path: Path) -> None:
    path = write_json(tmp_path, {"a": 1})
    assert load_json_object(path) == {"a": 1}


def test_load_json_object_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ReportLoadError, match="Could not read"):
        load_json_object(tmp_path / "absent.json")


def test_load_json_object_malformed(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(ReportLoadError, match="Could not read"):
        load_json_object(path)


def test_load_json_object_rejects_a_non_object(tmp_path: Path) -> None:
    path = write_json(tmp_path, [1, 2])
    with pytest.raises(ReportLoadError, match="top level is not a JSON object"):
        load_json_object(path)


def test_field_returns_the_value(tmp_path: Path) -> None:
    assert field({"a": 1}, "a", tmp_path) == 1


def test_field_missing_key_names_the_report(tmp_path: Path) -> None:
    with pytest.raises(ReportLoadError, match="missing 'b'"):
        field({}, "b", tmp_path)


def test_as_float_accepts_ints(tmp_path: Path) -> None:
    assert as_float({"a": 3}, "a", tmp_path) == 3.0


def test_as_float_rejects_bools_and_strings(tmp_path: Path) -> None:
    with pytest.raises(ReportLoadError, match="is not a number"):
        as_float({"a": True}, "a", tmp_path)
    with pytest.raises(ReportLoadError, match="is not a number"):
        as_float({"a": "3"}, "a", tmp_path)


def test_as_int_accepts_ints_only(tmp_path: Path) -> None:
    assert as_int({"a": 3}, "a", tmp_path) == 3
    with pytest.raises(ReportLoadError, match="is not an integer"):
        as_int({"a": 3.5}, "a", tmp_path)
    with pytest.raises(ReportLoadError, match="is not an integer"):
        as_int({"a": True}, "a", tmp_path)


def test_as_str(tmp_path: Path) -> None:
    assert as_str({"a": "x"}, "a", tmp_path) == "x"
    with pytest.raises(ReportLoadError, match="is not a string"):
        as_str({"a": 1}, "a", tmp_path)


def test_as_section(tmp_path: Path) -> None:
    assert as_section({"a": {"b": 1}}, "a", tmp_path) == {"b": 1}
    with pytest.raises(ReportLoadError, match="is not a JSON object"):
        as_section({"a": []}, "a", tmp_path)


def test_as_list(tmp_path: Path) -> None:
    assert as_list({"a": [1]}, "a", tmp_path) == [1]
    with pytest.raises(ReportLoadError, match="is not a JSON array"):
        as_list({"a": {}}, "a", tmp_path)


def test_as_entry_list(tmp_path: Path) -> None:
    assert as_entry_list([{"a": 1}], "items", tmp_path) == [{"a": 1}]
    with pytest.raises(ReportLoadError, match=r"'items\[1\]' is not a JSON object"):
        as_entry_list([{"a": 1}, 2], "items", tmp_path)


def _cost_values() -> dict[str, object]:
    return {key: index + 0.5 for index, key in enumerate(COST_KEYS)}


def test_cost_breakdown_from_a_nested_object() -> None:
    breakdown = cost_breakdown_from_json({"cost_breakdown": _cost_values()})
    assert breakdown == CostBreakdown(
        ink_cost=0.5,
        substrate_cost=1.5,
        printer_hours=2.5,
        printer_cost=3.5,
        labor_hours=4.5,
        labor_cost=5.5,
        total_cost=6.5,
        unit_price=7.5,
    )


def test_cost_breakdown_from_flattened_fields() -> None:
    breakdown = cost_breakdown_from_json(_cost_values())
    assert breakdown is not None
    assert breakdown.total_cost == 6.5


def test_cost_breakdown_absent_is_none() -> None:
    assert cost_breakdown_from_json({}) is None
    assert cost_breakdown_from_json({"ink_cost": 1.0}) is None


def test_cost_breakdown_non_numeric_is_none() -> None:
    values: dict[str, object] = _cost_values()
    values["ink_cost"] = "free"
    assert cost_breakdown_from_json(values) is None
    values["ink_cost"] = True
    assert cost_breakdown_from_json(values) is None


def test_cost_breakdown_ignores_a_non_dict_nested_section() -> None:
    values: dict[str, object] = _cost_values()
    values["cost_breakdown"] = "nope"
    assert cost_breakdown_from_json(values) is not None
