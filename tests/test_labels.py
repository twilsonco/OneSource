"""Tests for :mod:`vinyllabels.labels`."""

from __future__ import annotations

from pathlib import Path

from vinyllabels.labels import expand_input_paths, is_glob_pattern, parse_labels


def test_is_glob_pattern_detects_metacharacters() -> None:
    assert is_glob_pattern("*.txt")
    assert is_glob_pattern("job?.txt")
    assert is_glob_pattern("[ab]c.txt")
    assert not is_glob_pattern("data/labels.txt")
    assert not is_glob_pattern("")


def test_parse_labels_skips_blanks_and_comments(tmp_path: Path) -> None:
    path = tmp_path / "labels.txt"
    path.write_text(
        "# header comment\n\nALPHA\n   BETA   \n   # indented comment\n   \nGAMMA\n",
        encoding="utf-8",
    )
    assert parse_labels(path) == ["ALPHA", "BETA", "GAMMA"]


def test_parse_labels_keeps_duplicates_in_order(tmp_path: Path) -> None:
    path = tmp_path / "labels.txt"
    path.write_text("A\nB\nA\n", encoding="utf-8")
    assert parse_labels(path) == ["A", "B", "A"]


def test_parse_labels_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "labels.txt"
    path.write_text("", encoding="utf-8")
    assert parse_labels(path) == []


def test_parse_labels_handles_unicode(tmp_path: Path) -> None:
    path = tmp_path / "labels.txt"
    path.write_text("CAFÉ-99\n", encoding="utf-8")
    assert parse_labels(path) == ["CAFÉ-99"]


def test_expand_input_paths_returns_plain_path_even_if_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope.txt"
    assert expand_input_paths(missing) == [missing]


def test_expand_input_paths_sorts_glob_matches_and_drops_dirs(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("B\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("A\n", encoding="utf-8")
    (tmp_path / "skip.txt").mkdir()
    (tmp_path / "ignored.md").write_text("x\n", encoding="utf-8")
    assert expand_input_paths(tmp_path / "*.txt") == [
        tmp_path / "a.txt",
        tmp_path / "b.txt",
    ]


def test_expand_input_paths_recursive_glob(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "deeper"
    nested.mkdir(parents=True)
    (nested / "z.txt").write_text("Z\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("A\n", encoding="utf-8")
    assert expand_input_paths(tmp_path / "**" / "*.txt") == [
        tmp_path / "a.txt",
        nested / "z.txt",
    ]


def test_expand_input_paths_no_matches(tmp_path: Path) -> None:
    assert expand_input_paths(tmp_path / "*.txt") == []
