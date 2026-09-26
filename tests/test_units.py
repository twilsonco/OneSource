"""Tests for :mod:`vinyllabels.units`."""

from __future__ import annotations

from vinyllabels.units import SQ_IN_PER_SQ_FT, sq_ft, with_sq_ft


def test_sq_ft_divides_by_144() -> None:
    assert sq_ft(144.0) == 1.0
    assert sq_ft(0.0) == 0.0
    assert sq_ft(36.0) == 0.25
    assert sq_ft(-144.0) == -1.0


def test_constant_is_square_inches_per_square_foot() -> None:
    assert SQ_IN_PER_SQ_FT == 144.0


def test_with_sq_ft_adds_sibling_for_every_sq_in_number() -> None:
    extended = with_sq_ft({"total_ink_area_sq_in": 288.0, "count_sq_in": 144})
    assert extended["total_ink_area_sq_in"] == 288.0
    assert extended["total_ink_area_sq_ft"] == 2.0
    assert extended["count_sq_ft"] == 1.0


def test_with_sq_ft_leaves_other_keys_untouched() -> None:
    extended = with_sq_ft(
        {"linear_feet": 3.0, "note_sq_in": "144", "nested_sq_in": None}
    )
    assert extended == {"linear_feet": 3.0, "note_sq_in": "144", "nested_sq_in": None}


def test_with_sq_ft_does_not_mutate_the_input() -> None:
    values: dict[str, object] = {"a_sq_in": 144.0}
    result = with_sq_ft(values)
    assert values == {"a_sq_in": 144.0}
    assert result is not values
