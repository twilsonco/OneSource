"""Pricing configuration loading, CLI-override merging, and cost computation.

The cost model is deliberately simple and linear: ink and substrate are priced
per square foot, printer time per hour (driven off square feet), and labor is a
factor of printer time. Price is either cost plus markup, or a flat per-label
price when one is supplied.
"""

from __future__ import annotations

import json
from pathlib import Path

from vinyllabels.models import CostBreakdown, PricingConfig

__all__ = [
    "PRICING_CONFIG_FILENAME",
    "compute_cost_breakdown",
    "find_pricing_config",
    "load_pricing_config",
    "resolve_pricing_config",
]

#: Name looked up when walking up the directory tree for pricing settings.
PRICING_CONFIG_FILENAME = "pricing-config.json"

# Maps the CLI override keys (shared by both CLIs) onto PricingConfig fields.
_CLI_OVERRIDE_FIELDS: dict[str, str] = {
    "ink_cost": "ink_cost_usd_per_sqft",
    "substrate_cost": "substrate_cost_usd_per_sqft",
    "print_rate": "print_rate_hours_per_sqft",
    "printer_cost": "printer_run_cost_usd_per_hour",
    "labor_rate": "labor_rate_usd_per_hour",
    "labor_factor": "labor_time_factor",
    "markup": "markup_percent",
}


def load_pricing_config(path: Path) -> PricingConfig | None:
    """Load :class:`PricingConfig` from JSON, returning ``None`` if unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        customer_report = data.get("customer_report")
        return PricingConfig(
            ink_cost_usd_per_sqft=float(data["ink_cost_usd_per_sqft"]),
            substrate_cost_usd_per_sqft=float(data["substrate_cost_usd_per_sqft"]),
            print_rate_hours_per_sqft=float(data["print_rate_hours_per_sqft"]),
            printer_run_cost_usd_per_hour=float(data["printer_run_cost_usd_per_hour"]),
            labor_rate_usd_per_hour=float(data["labor_rate_usd_per_hour"]),
            labor_time_factor=float(data["labor_time_factor"]),
            markup_percent=float(data["markup_percent"]),
            customer_report=(
                {str(k): bool(v) for k, v in customer_report.items()}
                if isinstance(customer_report, dict)
                else None
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def find_pricing_config(
    start: Path | None = None, *, filename: str = PRICING_CONFIG_FILENAME
) -> Path | None:
    """Return the nearest ``filename`` at or above ``start``, searching upward.

    Replaces the old ``Path(__file__).parent.parent`` guess, which only worked
    because the scripts lived one directory below the repo root. Walking up from
    the working directory keeps "drop pricing-config.json in the project root"
    working while also working from an installed package.
    """
    directory = (start if start is not None else Path.cwd()).resolve()
    for candidate_dir in (directory, *directory.parents):
        candidate = candidate_dir / filename
        if candidate.is_file():
            return candidate
    return None


def resolve_pricing_config(
    config_path: Path | None, cli_overrides: dict[str, float]
) -> PricingConfig | None:
    """Merge file config with CLI overrides, returning ``None`` if all absent."""
    config = load_pricing_config(config_path) if config_path is not None else None

    if config is None and not cli_overrides:
        return None

    # With no file config, start from neutral defaults so CLI flags alone work.
    if config is None:
        config = PricingConfig(
            ink_cost_usd_per_sqft=0.0,
            substrate_cost_usd_per_sqft=0.0,
            print_rate_hours_per_sqft=0.0,
            printer_run_cost_usd_per_hour=0.0,
            labor_rate_usd_per_hour=0.0,
            labor_time_factor=1.0,
            markup_percent=0.0,
        )

    overrides = {
        field: cli_overrides[key]
        for key, field in _CLI_OVERRIDE_FIELDS.items()
        if key in cli_overrides
    }
    if overrides:
        config = _with_overrides(config, overrides)

    return config


def _with_overrides(
    config: PricingConfig, overrides: dict[str, float]
) -> PricingConfig:
    """Return ``config`` with the numeric ``overrides`` applied.

    Every overridable field is a float, so rebuilding the config from a merged
    field mapping keeps the pricing dataclass immutable and the types honest.
    """
    values: dict[str, float] = {
        "ink_cost_usd_per_sqft": config.ink_cost_usd_per_sqft,
        "substrate_cost_usd_per_sqft": config.substrate_cost_usd_per_sqft,
        "print_rate_hours_per_sqft": config.print_rate_hours_per_sqft,
        "printer_run_cost_usd_per_hour": config.printer_run_cost_usd_per_hour,
        "labor_rate_usd_per_hour": config.labor_rate_usd_per_hour,
        "labor_time_factor": config.labor_time_factor,
        "markup_percent": config.markup_percent,
    }
    values.update(overrides)
    return PricingConfig(customer_report=config.customer_report, **values)


def compute_cost_breakdown(
    sqft_ink: float,
    sqft_substrate: float,
    pricing_config: PricingConfig | None,
    flat_label_price: float | None,
) -> CostBreakdown | None:
    """Compute a cost breakdown from areas and pricing config.

    Returns ``None`` if ``pricing_config`` is ``None``. If ``flat_label_price``
    is provided, ``unit_price`` uses it; otherwise it is computed from markup.
    """
    if pricing_config is None:
        return None

    ink_cost = sqft_ink * pricing_config.ink_cost_usd_per_sqft
    substrate_cost = sqft_substrate * pricing_config.substrate_cost_usd_per_sqft
    printer_hours = sqft_substrate * pricing_config.print_rate_hours_per_sqft
    printer_cost = printer_hours * pricing_config.printer_run_cost_usd_per_hour
    labor_hours = printer_hours * pricing_config.labor_time_factor
    labor_cost = labor_hours * pricing_config.labor_rate_usd_per_hour
    total_cost = ink_cost + substrate_cost + printer_cost + labor_cost

    if flat_label_price is not None:
        unit_price = flat_label_price
    else:
        unit_price = total_cost * (1.0 + pricing_config.markup_percent / 100.0)

    return CostBreakdown(
        ink_cost=ink_cost,
        substrate_cost=substrate_cost,
        printer_hours=printer_hours,
        printer_cost=printer_cost,
        labor_hours=labor_hours,
        labor_cost=labor_cost,
        total_cost=total_cost,
        unit_price=unit_price,
    )
