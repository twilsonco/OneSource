"""Tests for :mod:`vinyllabels.colors`."""

from __future__ import annotations

import pytest
from reportlab.lib.colors import Color, black, blue, green, magenta, orange, red, yellow

from vinyllabels.colors import PRESET_COLOR_NAMES, parse_color


def hex_of(color: Color) -> str:
    """Normalise ReportLab's ``0xrrggbb`` spelling to ``#RRGGBB`` for comparisons."""
    return f"#{color.hexval().split('x')[-1].upper()}"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("black", black),
        ("k", black),
        ("blue", blue),
        ("b", blue),
        ("green", green),
        ("g", green),
        ("red", red),
        ("r", red),
        ("orange", orange),
        ("o", orange),
        ("yellow", yellow),
        ("y", yellow),
        ("magenta", magenta),
        ("m", magenta),
    ],
)
def test_preset_names_resolve_to_reportlab_colors(name: str, expected: object) -> None:
    assert parse_color(name) is expected


@pytest.mark.parametrize("name", ["violet", "v"])
def test_violet_preset_is_the_documented_hex(name: str) -> None:
    assert hex_of(parse_color(name)) == "#8B00FF"


def test_preset_help_text_lists_the_long_names() -> None:
    for name in (
        "black",
        "blue",
        "green",
        "red",
        "orange",
        "yellow",
        "violet",
        "magenta",
    ):
        assert name in PRESET_COLOR_NAMES


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("255,0,0", "#FF0000"),
        ("0, 128, 255", "#0080FF"),
        ("(0,0,0)", "#000000"),
        ("(255, 255, 255)", "#FFFFFF"),
        ("0,0,0", "#000000"),
    ],
)
def test_rgb_spellings(spec: str, expected: str) -> None:
    assert hex_of(parse_color(spec)) == expected


@pytest.mark.parametrize(
    ("spec", "expected"),
    [("00ff00", "#00FF00"), ("ABCDEF", "#ABCDEF"), ("000000", "#000000")],
)
def test_hex_spellings(spec: str, expected: str) -> None:
    assert hex_of(parse_color(spec)) == expected


@pytest.mark.parametrize(
    ("spec", "fragment"),
    [
        ("1,2", "RGB color must have 3 components, got 2: 1,2"),
        ("1,2,3,4", "RGB color must have 3 components, got 4: 1,2,3,4"),
        ("256,0,0", "RGB values must be in [0, 255], got r=256, g=0, b=0"),
        ("0,-1,0", "RGB values must be in [0, 255], got r=0, g=-1, b=0"),
        ("a,b,c", "Invalid RGB color format a,b,c"),
        ("(1,2)", "Invalid RGB color format (1,2)"),
    ],
)
def test_invalid_rgb_raises_with_context(spec: str, fragment: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        parse_color(spec)
    assert fragment in str(excinfo.value)


@pytest.mark.parametrize(
    ("spec", "fragment"),
    [
        ("abc", "Hex color must be 6 digits (got 3): abc"),
        ("abcdef0", "Hex color must be 6 digits (got 7): abcdef0"),
        ("zzzzzz", "Invalid hex color format zzzzzz"),
        ("purple", "Invalid hex color format purple"),
    ],
)
def test_invalid_hex_raises_with_context(spec: str, fragment: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        parse_color(spec)
    assert fragment in str(excinfo.value)
