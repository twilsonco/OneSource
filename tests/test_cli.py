"""Tests for :mod:`vinyllabels.cli` (the pricing flags shared by both tools)."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from vinyllabels.cli import (
    add_pricing_arguments,
    pricing_config_path_from_args,
    pricing_from_args,
    pricing_overrides_from_args,
)
from vinyllabels.models import PricingConfig
from vinyllabels.pricing import PRICING_CONFIG_FILENAME


def build_parser() -> argparse.ArgumentParser:
    """A bare parser carrying only the shared pricing options."""
    parser = argparse.ArgumentParser()
    add_pricing_arguments(parser)
    return parser


def test_every_pricing_flag_defaults_to_none() -> None:
    args = build_parser().parse_args([])
    assert args.pricing_config is None
    assert pricing_overrides_from_args(args) == {}
    assert pricing_from_args(args) is None


def test_numeric_overrides_are_collected() -> None:
    args = build_parser().parse_args(
        ["--ink-cost", "1.5", "--markup", "20", "--labor-factor", "0.25"]
    )
    assert pricing_overrides_from_args(args) == {
        "ink_cost": 1.5,
        "markup": 20.0,
        "labor_factor": 0.25,
    }


def test_all_override_flags_map_to_distinct_keys() -> None:
    args = build_parser().parse_args(
        [
            "--ink-cost",
            "1",
            "--substrate-cost",
            "2",
            "--print-rate",
            "3",
            "--printer-cost",
            "4",
            "--labor-rate",
            "5",
            "--labor-factor",
            "6",
            "--markup",
            "7",
        ]
    )
    assert pricing_overrides_from_args(args) == {
        "ink_cost": 1.0,
        "substrate_cost": 2.0,
        "print_rate": 3.0,
        "printer_cost": 4.0,
        "labor_rate": 5.0,
        "labor_factor": 6.0,
        "markup": 7.0,
    }


def test_explicit_config_path_wins(tmp_path: Path) -> None:
    explicit = tmp_path / "custom.json"
    args = build_parser().parse_args(["--pricing-config", str(explicit)])
    assert pricing_config_path_from_args(args) == explicit


def test_config_path_is_discovered_when_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / PRICING_CONFIG_FILENAME
    config.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args([])
    assert pricing_config_path_from_args(args) == config


def test_pricing_from_args_merges_file_and_flags(
    tmp_path: Path, make_pricing_config: Callable[..., PricingConfig]
) -> None:
    payload = {
        "ink_cost_usd_per_sqft": 1.0,
        "substrate_cost_usd_per_sqft": 2.0,
        "print_rate_hours_per_sqft": 0.5,
        "printer_run_cost_usd_per_hour": 10.0,
        "labor_rate_usd_per_hour": 20.0,
        "labor_time_factor": 0.5,
        "markup_percent": 10.0,
        "customer_report": {"price": True},
    }
    config_path = tmp_path / PRICING_CONFIG_FILENAME
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    args = build_parser().parse_args(
        ["--pricing-config", str(config_path), "--markup", "50"]
    )
    config = pricing_from_args(args)
    assert config is not None
    assert config.markup_percent == 50.0
    assert config.ink_cost_usd_per_sqft == 1.0
    assert config.customer_report == {"price": True}
    assert config != make_pricing_config()
