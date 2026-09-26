"""Tests for :mod:`vinyllabels.fonts`."""

from __future__ import annotations

from pathlib import Path

import pytest
from reportlab.pdfbase import pdfmetrics

from vinyllabels import fonts
from vinyllabels.fonts import (
    ARIAL_BOLD_CANDIDATES,
    ARIAL_BOLD_NAME,
    FALLBACK_FONT_NAME,
    register_bold_font,
)


def test_candidate_list_is_the_three_macos_locations() -> None:
    assert len(ARIAL_BOLD_CANDIDATES) == 3
    assert all(candidate.endswith(".ttf") for candidate in ARIAL_BOLD_CANDIDATES)


def test_probe_returns_a_usable_font_without_a_preferred_path() -> None:
    name, path = register_bold_font(None)
    assert name in (ARIAL_BOLD_NAME, FALLBACK_FONT_NAME)
    if path is None:
        assert name == FALLBACK_FONT_NAME
    else:
        assert name == ARIAL_BOLD_NAME
        assert Path(path).is_file()
    assert pdfmetrics.stringWidth("AB", name, 12.0) > 0.0


def test_preferred_path_is_used_exclusively(synthetic_font_path: Path) -> None:
    name, path = register_bold_font(synthetic_font_path)
    assert name == ARIAL_BOLD_NAME
    assert path == str(synthetic_font_path)
    assert pdfmetrics.stringWidth("AB", name, 12.0) > 0.0


def test_missing_preferred_path_exits(tmp_path: Path) -> None:
    missing = tmp_path / "nope.ttf"
    with pytest.raises(SystemExit) as excinfo:
        register_bold_font(missing)
    assert str(excinfo.value) == f"Font file not found: {missing}"


def test_unreadable_preferred_font_exits(tmp_path: Path) -> None:
    not_a_font = tmp_path / "fake.ttf"
    not_a_font.write_text("this is not a font", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        register_bold_font(not_a_font)
    assert str(excinfo.value).startswith(f"Could not register font {not_a_font}:")


def test_probe_skips_missing_candidates_and_uses_the_first_hit(
    monkeypatch: pytest.MonkeyPatch, synthetic_font_path: Path
) -> None:
    missing = synthetic_font_path.parent / "absent.ttf"
    monkeypatch.setattr(
        fonts, "ARIAL_BOLD_CANDIDATES", (str(missing), str(synthetic_font_path))
    )
    name, path = register_bold_font(None)
    assert (name, path) == (ARIAL_BOLD_NAME, str(synthetic_font_path))


def test_probe_falls_back_when_no_candidate_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        fonts,
        "ARIAL_BOLD_CANDIDATES",
        (str(tmp_path / "a.ttf"), str(tmp_path / "b.ttf")),
    )
    assert register_bold_font(None) == (FALLBACK_FONT_NAME, None)


def test_probe_falls_back_when_registration_fails(
    monkeypatch: pytest.MonkeyPatch, synthetic_font_path: Path
) -> None:
    class Boom:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("nope")

    monkeypatch.setattr(fonts, "RLTTFont", Boom)
    monkeypatch.setattr(fonts, "ARIAL_BOLD_CANDIDATES", (str(synthetic_font_path),))
    assert register_bold_font(None) == (FALLBACK_FONT_NAME, None)
