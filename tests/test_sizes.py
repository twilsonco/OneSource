"""Tests for :mod:`vinyllabels.sizes`."""

from __future__ import annotations

from collections import Counter

from vinyllabels.sizes import (
    LabelSize,
    first_label_size,
    format_label_size,
    label_sizes_to_json,
)


def test_format_label_size_drops_trailing_zeros() -> None:
    assert format_label_size((8.0, 3.0)) == "8x3"
    assert format_label_size((8.5, 3.25)) == "8.5x3.25"
    assert format_label_size((12.0, 0.5)) == "12x0.5"


def test_label_sizes_to_json_is_sorted_by_size() -> None:
    sizes: Counter[LabelSize] = Counter({(8.0, 3.0): 4, (4.0, 2.0): 2})
    assert label_sizes_to_json(sizes) == [
        {"width_in": 4.0, "height_in": 2.0, "labels": 2},
        {"width_in": 8.0, "height_in": 3.0, "labels": 4},
    ]


def test_label_sizes_to_json_empty() -> None:
    assert label_sizes_to_json(Counter()) == []


def test_first_label_size_returns_the_smallest() -> None:
    sizes: Counter[LabelSize] = Counter({(8.0, 3.0): 1, (4.0, 6.0): 9})
    assert first_label_size(sizes) == (4.0, 6.0)


def test_first_label_size_empty_is_none() -> None:
    assert first_label_size(Counter()) is None
