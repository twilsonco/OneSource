"""Tests for :mod:`vinyllabels.pricing`."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest

from vinyllabels.models import PricingConfig
from vinyllabels.pricing import (
    PRICING_CONFIG_FILENAME,
    compute_cost_breakdown,
    find_pricing_config,
    load_pricing_config,
    resolve_pricing_config,
)

VALID_CONFIG: Final[dict[str, object]] = {
    "ink_cost_usd_per_sqft": 1.0,
    "substrate_cost_usd_per_sqft": 2.0,
    "print_rate_hours_per_sqft": 0.5,
    "printer_run_cost_usd_per_hour": 10.0,
    "labor_rate_usd_per_hour": 20.0,
    "labor_time_factor": 0.5,
    "markup_percent": 10.0,
}


def write_config(directory: Path, **overrides: object) -> Path:
    """Write a pricing config file into ``directory`` and return its path."""
    payload: dict[str, object] = dict(VALID_CONFIG)
    payload.update(overrides)
    path = directory / PRICING_CONFIG_FILENAME
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --- load_pricing_config -------------------------------------------------------


def test_load_valid_config(tmp_path: Path) -> None:
    config = load_pricing_config(write_config(tmp_path))
    assert config == PricingConfig(
        ink_cost_usd_per_sqft=1.0,
        substrate_cost_usd_per_sqft=2.0,
        print_rate_hours_per_sqft=0.5,
        printer_run_cost_usd_per_hour=10.0,
        labor_rate_usd_per_hour=20.0,
        labor_time_factor=0.5,
        markup_percent=10.0,
        customer_report=None,
    )


def test_load_coerces_customer_report_flags(tmp_path: Path) -> None:
    path = write_config(tmp_path, customer_report={"ink_cost": 1, "labor_cost": 0})
    config = load_pricing_config(path)
    assert config is not None
    assert config.customer_report == {"ink_cost": True, "labor_cost": False}


def test_load_ignores_non_dict_customer_report(tmp_path: Path) -> None:
    path = write_config(tmp_path, customer_report="all")
    config = load_pricing_config(path)
    assert config is not None
    assert config.customer_report is None
    assert config.markup_percent == 10.0


def test_load_accepts_integer_values(tmp_path: Path) -> None:
    path = write_config(tmp_path, markup_percent=15)
    config = load_pricing_config(path)
    assert config is not None
    assert config.markup_percent == 15.0


def test_load_missing_file_is_none(tmp_path: Path) -> None:
    assert load_pricing_config(tmp_path / "absent.json") is None


def test_load_malformed_json_is_none(tmp_path: Path) -> None:
    path = tmp_path / PRICING_CONFIG_FILENAME
    path.write_text("{not json", encoding="utf-8")
    assert load_pricing_config(path) is None


def test_load_non_object_json_is_none(tmp_path: Path) -> None:
    path = tmp_path / PRICING_CONFIG_FILENAME
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_pricing_config(path) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"ink_cost_usd_per_sqft": None},
        {"ink_cost_usd_per_sqft": "abc"},
        {"markup_percent": []},
    ],
)
def test_load_bad_field_types_is_none(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    assert load_pricing_config(write_config(tmp_path, **overrides)) is None


def test_load_missing_key_is_none(tmp_path: Path) -> None:
    payload = dict(VALID_CONFIG)
    del payload["markup_percent"]
    path = tmp_path / PRICING_CONFIG_FILENAME
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_pricing_config(path) is None


# --- find_pricing_config -------------------------------------------------------


def test_find_walks_upward(tmp_path: Path) -> None:
    expected = write_config(tmp_path)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_pricing_config(nested) == expected


def test_find_uses_the_working_directory_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = write_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert find_pricing_config() == expected


def test_find_honours_a_custom_filename(tmp_path: Path) -> None:
    path = tmp_path / "other.json"
    path.write_text("{}", encoding="utf-8")
    assert find_pricing_config(tmp_path, filename="other.json") == path


def test_find_returns_none_when_absent(tmp_path: Path) -> None:
    assert find_pricing_config(tmp_path, filename="definitely-not-here.json") is None


# --- resolve_pricing_config ----------------------------------------------------


def test_resolve_without_file_or_overrides_is_none() -> None:
    assert resolve_pricing_config(None, {}) is None


def test_resolve_with_unreadable_file_and_no_overrides_is_none(
    tmp_path: Path,
) -> None:
    assert resolve_pricing_config(tmp_path / "absent.json", {}) is None


def test_resolve_overrides_only_uses_neutral_defaults() -> None:
    config = resolve_pricing_config(None, {"ink_cost": 3.0, "markup": 25.0})
    assert config == PricingConfig(
        ink_cost_usd_per_sqft=3.0,
        substrate_cost_usd_per_sqft=0.0,
        print_rate_hours_per_sqft=0.0,
        printer_run_cost_usd_per_hour=0.0,
        labor_rate_usd_per_hour=0.0,
        labor_time_factor=1.0,
        markup_percent=25.0,
        customer_report=None,
    )


def test_resolve_from_file(tmp_path: Path) -> None:
    config = resolve_pricing_config(write_config(tmp_path), {})
    assert config is not None
    assert config.substrate_cost_usd_per_sqft == 2.0


def test_resolve_merges_cli_overrides_and_keeps_customer_report(
    tmp_path: Path,
) -> None:
    path = write_config(tmp_path, customer_report={"price": True})
    config = resolve_pricing_config(
        path,
        {
            "ink_cost": 9.0,
            "substrate_cost": 8.0,
            "print_rate": 7.0,
            "printer_cost": 6.0,
            "labor_rate": 5.0,
            "labor_factor": 4.0,
            "markup": 3.0,
        },
    )
    assert config is not None
    assert config.ink_cost_usd_per_sqft == 9.0
    assert config.substrate_cost_usd_per_sqft == 8.0
    assert config.print_rate_hours_per_sqft == 7.0
    assert config.printer_run_cost_usd_per_hour == 6.0
    assert config.labor_rate_usd_per_hour == 5.0
    assert config.labor_time_factor == 4.0
    assert config.markup_percent == 3.0
    assert config.customer_report == {"price": True}


def test_resolve_ignores_unknown_override_keys(tmp_path: Path) -> None:
    config = resolve_pricing_config(write_config(tmp_path), {"nonsense": 1.0})
    assert config is not None
    assert config.ink_cost_usd_per_sqft == 1.0


# --- compute_cost_breakdown ----------------------------------------------------


def test_compute_without_config_is_none() -> None:
    assert compute_cost_breakdown(1.0, 1.0, None, None) is None


def test_compute_hand_checked_breakdown(
    make_pricing_config: Callable[..., PricingConfig],
) -> None:
    breakdown = compute_cost_breakdown(2.0, 3.0, make_pricing_config(), None)
    assert breakdown is not None
    # ink 2*1=2, substrate 3*2=6, hours 3*0.5=1.5, printer 1.5*10=15,
    # labor hours 1.5*0.5=0.75, labor 0.75*20=15 -> total 38, +10% = 41.8
    assert breakdown.ink_cost == pytest.approx(2.0)
    assert breakdown.substrate_cost == pytest.approx(6.0)
    assert breakdown.printer_hours == pytest.approx(1.5)
    assert breakdown.printer_cost == pytest.approx(15.0)
    assert breakdown.labor_hours == pytest.approx(0.75)
    assert breakdown.labor_cost == pytest.approx(15.0)
    assert breakdown.total_cost == pytest.approx(38.0)
    assert breakdown.unit_price == pytest.approx(41.8)


def test_compute_flat_price_replaces_markup(
    make_pricing_config: Callable[..., PricingConfig],
) -> None:
    breakdown = compute_cost_breakdown(2.0, 3.0, make_pricing_config(), 7.5)
    assert breakdown is not None
    assert breakdown.unit_price == 7.5
    assert breakdown.total_cost == pytest.approx(38.0)


def test_compute_with_zero_areas(
    make_pricing_config: Callable[..., PricingConfig],
) -> None:
    breakdown = compute_cost_breakdown(0.0, 0.0, make_pricing_config(), None)
    assert breakdown is not None
    assert breakdown.total_cost == 0.0
    assert breakdown.unit_price == 0.0
