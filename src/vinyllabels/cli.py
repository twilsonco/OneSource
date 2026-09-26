"""Command-line plumbing shared by the ``generate`` and ``consolidate`` tools.

Both tools accept the identical set of pricing overrides, so the argument group
and the mapping onto :class:`~vinyllabels.models.PricingConfig` live here once.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from vinyllabels.models import PricingConfig
from vinyllabels.pricing import find_pricing_config, resolve_pricing_config

__all__ = [
    "add_pricing_arguments",
    "pricing_config_path_from_args",
    "pricing_from_args",
    "pricing_overrides_from_args",
]

# (flag, help text, PricingConfig override key) for each numeric override.
_PRICING_FLAGS: tuple[tuple[str, str, str], ...] = (
    (
        "--ink-cost",
        "Ink cost per square foot (USD) (overrides config file).",
        "ink_cost",
    ),
    (
        "--substrate-cost",
        "Substrate cost per square foot (USD) (overrides config file).",
        "substrate_cost",
    ),
    (
        "--print-rate",
        "Print rate in hours per square foot (overrides config file).",
        "print_rate",
    ),
    (
        "--printer-cost",
        "Printer run cost per hour (USD) (overrides config file).",
        "printer_cost",
    ),
    (
        "--labor-rate",
        "Labor rate per hour (USD) (overrides config file).",
        "labor_rate",
    ),
    (
        "--labor-factor",
        "Labor time multiplier relative to printer hours (overrides config file).",
        "labor_factor",
    ),
    ("--markup", "Markup percentage on total cost (overrides config file).", "markup"),
)


def add_pricing_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the shared ``--pricing-config`` and per-metric override flags."""
    group = parser.add_argument_group("pricing", "cost configuration and pricing")
    group.add_argument(
        "--pricing-config",
        type=Path,
        default=None,
        help=(
            "Path to JSON pricing config file "
            "(default: pricing-config.json found by searching upward)."
        ),
    )
    for flag, help_text, _key in _PRICING_FLAGS:
        group.add_argument(flag, type=float, default=None, help=help_text)


def pricing_overrides_from_args(args: argparse.Namespace) -> dict[str, float]:
    """Collect the pricing flags the user actually passed, keyed for resolution."""
    overrides: dict[str, float] = {}
    for _flag, _help_text, key in _PRICING_FLAGS:
        value = getattr(args, key, None)
        if value is not None:
            overrides[key] = float(value)
    return overrides


def pricing_config_path_from_args(args: argparse.Namespace) -> Path | None:
    """Return the explicit ``--pricing-config`` path, else the discovered one."""
    explicit = getattr(args, "pricing_config", None)
    if explicit is not None:
        return Path(explicit)
    return find_pricing_config()


def pricing_from_args(args: argparse.Namespace) -> PricingConfig | None:
    """Resolve the effective pricing config from parsed CLI arguments."""
    return resolve_pricing_config(
        pricing_config_path_from_args(args), pricing_overrides_from_args(args)
    )
