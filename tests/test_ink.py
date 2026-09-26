"""Tests for :mod:`vinyllabels.ink`."""

from __future__ import annotations

from pathlib import Path

import pytest

from vinyllabels.ink import horizontal_scale, text_ink_area

_PT_PER_IN = 72.0


def test_ink_area_is_positive_for_real_glyphs(synthetic_font_path: Path) -> None:
    area = text_ink_area("A", str(synthetic_font_path), 100.0, 0.728)
    assert area > 0.0


def test_ink_area_scales_linearly_with_glyph_count(synthetic_font_path: Path) -> None:
    one = text_ink_area("A", str(synthetic_font_path), 100.0, 0.728)
    two = text_ink_area("AA", str(synthetic_font_path), 100.0, 0.728)
    assert two == pytest.approx(2.0 * one)


def test_ink_area_is_quadratic_in_font_size(synthetic_font_path: Path) -> None:
    small = text_ink_area("A", str(synthetic_font_path), 100.0, 0.728)
    large = text_ink_area("A", str(synthetic_font_path), 200.0, 0.728)
    assert large == pytest.approx(4.0 * small)


def test_ink_area_hand_computed_from_the_test_font(
    synthetic_font_path: Path,
) -> None:
    # The generated "A" is a 800x700 unit triangle in a 1000 upem font:
    # area = 0.5 * 800 * 700 = 280_000 font units squared.
    # At 72pt (1in) one font unit is 1/1000 in, so the ink is 0.28 sq in.
    area = text_ink_area("A", str(synthetic_font_path), _PT_PER_IN, 0.728)
    assert area == pytest.approx(0.28, rel=1e-6)


def test_ink_area_ignores_characters_missing_from_the_font(
    synthetic_font_path: Path,
) -> None:
    assert text_ink_area("\x00", str(synthetic_font_path), 100.0, 0.728) == 0.0
    mixed = text_ink_area("\x00A", str(synthetic_font_path), 100.0, 0.728)
    plain = text_ink_area("A", str(synthetic_font_path), 100.0, 0.728)
    assert mixed == pytest.approx(plain)


def test_ink_area_of_empty_text_is_zero(synthetic_font_path: Path) -> None:
    assert text_ink_area("", str(synthetic_font_path), 100.0, 0.728) == 0.0


def test_fallback_estimate_without_a_font_path() -> None:
    # cap = 72pt * 0.5 = 36pt; width = 2 chars * 36 * 0.60 = 43.2pt;
    # area = 43.2 * 36 * 0.45 = 699.84 sq pt = 699.84 / 72**2 sq in.
    area = text_ink_area("AB", None, _PT_PER_IN, 0.5)
    assert area == pytest.approx(699.84 / (_PT_PER_IN * _PT_PER_IN))


def test_horizontal_scale_never_enlarges(font_name: str) -> None:
    assert horizontal_scale("A", font_name, 100.0, 12.0) == 1.0


def test_horizontal_scale_compresses_wide_text(font_name: str) -> None:
    # Each glyph advances 1000/1000 em, so "AAAA" at 72pt is 4 inches wide.
    scale = horizontal_scale("AAAA", font_name, 1.0, _PT_PER_IN)
    assert scale == pytest.approx(0.25)


def test_horizontal_scale_of_empty_text_is_neutral(font_name: str) -> None:
    assert horizontal_scale("", font_name, 1.0, _PT_PER_IN) == 1.0
