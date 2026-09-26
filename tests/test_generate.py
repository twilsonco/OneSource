"""Tests for the ``generate`` command line entry point.

``generate`` is what the print scripts drive, so these check the three things a
shell script depends on: every flag lands on the :class:`JobConfig`, impossible
geometry fails with a usable message, and a real run writes the whole artifact
set beside its input.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader
from reportlab.lib.colors import Color

from vinyllabels.generate import build_argument_parser, main, parse_args
from vinyllabels.layout import JobConfig
from vinyllabels.output_paths import job_output_paths
from vinyllabels.pricing import PRICING_CONFIG_FILENAME

from helpers import LabelFileFactory

# A JSON report payload. The documents are untyped by nature, so ``Any`` keeps
# the nested assertions readable instead of casting every lookup.
JsonDict = dict[str, Any]

# A small but non-trivial job: two codes, two copies each, one 3x2 sheet grid.
BASE_ARGS = [
    "--page-width",
    "24",
    "--label-width",
    "8",
    "--label-height",
    "3",
    "--labels-per-sheet-row",
    "3",
    "--labels-per-sheet-col",
    "2",
    "--copies",
    "2",
    "--text-height",
    "2",
]


def _hex(color: Color) -> str:
    """``#RRGGBB`` for a reportlab colour, for readable colour assertions."""
    return f"#{color.hexval().split('x')[-1].upper()}"


def _error(capsys: pytest.CaptureFixture[str], args: list[str]) -> str:
    """Parse ``args`` expecting argparse to reject them; return stderr."""
    with pytest.raises(SystemExit) as excinfo:
        parse_args(args)
    assert excinfo.value.code == 2
    return capsys.readouterr().err


def _job_args(label_file: Path, font: Path, *extra: str) -> list[str]:
    return ["-i", str(label_file), "--font", str(font), *BASE_ARGS, *extra]


def test_defaults_are_the_job_config_defaults(tmp_path: Path) -> None:
    input_path = tmp_path / "labels.txt"
    config = parse_args(["-i", str(input_path)])
    reference = JobConfig(input_path=input_path)

    assert config.output_path is None
    assert config.font_path is None
    assert (config.page_w_in, config.page_top_margin_in) == (52.0, 1.0)
    assert (config.label_w_in, config.label_h_in) == (reference.label_w_in, 3.0)
    assert (config.labels_per_sheet_row, config.labels_per_sheet_col) == (0, 8)
    assert (config.copies_per_label, config.text_height_in) == (2, 2.0)
    assert config.vertical_labels is False
    assert config.draw_border is True and config.draw_sheet_separators is True
    assert config.soft_page_height_in == 80.0
    assert config.flat_label_price is None
    # Colour flags default to the documented names, parsed into colours.
    assert _hex(config.border_color) == "#FF00FF"
    assert _hex(config.sheet_separator_color) == "#FFFF00"
    assert _hex(config.text_color) == "#000000"


def test_help_documents_every_flag() -> None:
    text = build_argument_parser().format_help()
    for flag in (
        "--page-width",
        "--vertical-labels",
        "--labels-per-sheet-row",
        "--soft-page-height",
        "--flat-label-price",
        "--pricing-config",
    ):
        assert flag in text


def test_every_layout_flag_lands_on_the_config(tmp_path: Path) -> None:
    # The flag set the WIC print script uses, checked end to end through the
    # parser rather than field by field by hand.
    config = parse_args(
        [
            "-i",
            str(tmp_path / "labels.txt"),
            "--page-width",
            "52",
            "--label-width",
            "10",
            "--label-height",
            "4",
            "--text-height",
            "3",
            "--copies",
            "2",
            "--vertical-labels",
            "--labels-per-sheet-row",
            "3",
            "--labels-per-sheet-col",
            "6",
            "--page-left-margin",
            "1",
            "--page-right-margin",
            "1",
        ]
    )

    assert (config.page_w_in, config.label_w_in, config.label_h_in) == (52.0, 10.0, 4.0)
    assert (config.page_left_margin_in, config.page_right_margin_in) == (1.0, 1.0)
    assert config.usable_page_w_in == 50.0
    assert (config.labels_per_sheet_row, config.labels_per_sheet_col) == (3, 6)
    assert config.copies_per_label == 2 and config.text_height_in == 3.0
    assert config.vertical_labels is True
    # Vertical labels keep the design size but swap the on-page footprint.
    assert (config.foot_w_in, config.foot_h_in) == (4.0, 10.0)


def test_vertical_labels_can_be_switched_back_off(tmp_path: Path) -> None:
    path = str(tmp_path / "labels.txt")
    assert parse_args(["-i", path, "--vertical-labels"]).vertical_labels is True
    assert parse_args(["-i", path, "--no-vertical-labels"]).vertical_labels is False


@pytest.mark.parametrize(
    ("flag", "value", "expected"),
    [
        ("--page-width", "0", r"--page-width must be greater than 0"),
        ("--label-width", "-1", r"--label-width must be greater than 0"),
        ("--label-height", "0", r"--label-height must be greater than 0"),
        ("--text-height", "0", r"--text-height must be greater than 0"),
        ("--page-left-margin", "-0.5", r"--page-left-margin must not be negative"),
        ("--vertical-gap", "-1", r"--vertical-gap must not be negative"),
        ("--border-line-width", "-1", r"--border-line-width must not be negative"),
        ("--label-h-margin", "4", r"--label-h-margin is too large"),
        ("--labels-per-sheet-row", "-1", r"must be >= 0"),
        ("--copies", "0", r"--copies must be >= 1"),
        ("--cap-height-ratio", "0", "--cap-height-ratio must be in (0, 1]"),
        ("--label-width", "60", r"no 60in label footprint fits in a 52in page"),
        ("--labels-per-sheet-row", "7", r"no 56in sheet fits in a 52in page"),
    ],
)
def test_impossible_geometry_is_rejected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    flag: str,
    value: str,
    expected: str,
) -> None:
    message = _error(capsys, ["-i", str(tmp_path / "labels.txt"), flag, value])
    assert expected in message


@pytest.mark.parametrize(
    ("flag", "value", "expected"),
    [
        # Only the eight preset names are recognised; anything else is read as a
        # colour literal, so a mistyped name reports the literal's error.
        ("--text-color", "chartreuse", "Invalid hex color format chartreuse"),
        ("--border-color", "1,2", "RGB color must have 3 components"),
        ("--border-color", "1,2,999", "RGB values must be in [0, 255]"),
        ("--sheet-separator-color", "zzzzzz", "Invalid hex color format zzzzzz"),
    ],
)
def test_bad_colours_are_rejected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    flag: str,
    value: str,
    expected: str,
) -> None:
    message = _error(capsys, ["-i", str(tmp_path / "labels.txt"), flag, value])
    assert expected in message


def test_a_run_writes_the_full_artifact_set(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    label_file: LabelFileFactory,
    synthetic_font_path: Path,
) -> None:
    labels = label_file("AAA", "BB", header="# two codes")
    assert main(_job_args(labels, synthetic_font_path)) == 0

    printed = capsys.readouterr().out
    assert "Wrote 4 label instances (2 unique x 2)" in printed

    organized = job_output_paths(labels, labels.stem)
    # The PDF is named after its label size and count, so it is found by prefix.
    pdfs = list((tmp_path / "PDF Files").glob("labels_8x3_*-labels.pdf"))
    assert [path.name for path in pdfs] == ["labels_8x3_4-labels.pdf"]
    for key, path in organized.items():
        if key != "pdf":
            assert path.is_file(), path

    reader = PdfReader(str(pdfs[0]))
    assert len(reader.pages) == 1
    assert reader.pages[0].extract_text().count("AAA") == 2

    payload: JsonDict = json.loads(organized["json"].read_text(encoding="utf-8"))
    assert payload["job"]["input_file"] == str(labels)
    assert payload["job"]["label_width_in"] == 8.0
    assert payload["global"]["total_output_labels"] == 4
    assert payload["global"]["total_characters"] == 10
    assert [entry["text"] for entry in payload["per_label"]] == ["AAA", "BB"]
    assert payload["label_sizes"] == [{"width_in": 8.0, "height_in": 3.0, "labels": 4}]

    report = organized["txt"].read_text(encoding="utf-8")
    assert "LABEL PRINTING REPORT" in report
    assert f"Input File:       {labels}" in report
    assert "Total Character Count:             10" in report
    assert "8x3" in report
    # Without a pricing config the header still advertises the money columns,
    # but every one of their cells is empty.
    internal_rows = [
        line.split(",")
        for line in organized["csv_report"].read_text(encoding="utf-8").splitlines()
    ]
    money = [
        index for index, header in enumerate(internal_rows[0]) if header.endswith("($)")
    ]
    assert len(money) == 6
    for row in internal_rows[1:]:
        assert all(row[index] == "" for index in money)
    assert organized["csv_report_customer"].is_file()


def test_vertical_runs_suffix_every_output(
    tmp_path: Path, synthetic_font_path: Path
) -> None:
    labels = tmp_path / "codes.txt"
    labels.write_text("AAA\n", encoding="utf-8")
    main(_job_args(labels, synthetic_font_path, "--vertical-labels"))

    organized = job_output_paths(labels, f"{labels.stem}_vertical")
    assert list((tmp_path / "PDF Files").glob("codes_vertical_8x3_*-labels.pdf"))
    assert organized["json"].read_text(encoding="utf-8").count("_vertical") >= 1

    # An explicit -o gains the same suffix, so vertical and flat runs never
    # overwrite each other's PDF.
    out = tmp_path / "given.pdf"
    main(_job_args(labels, synthetic_font_path, "--vertical-labels", "-o", str(out)))
    assert [p.name for p in tmp_path.glob("given*.pdf")] == [
        "given_vertical_8x3_2-labels.pdf"
    ]


def test_output_path_is_used_verbatim_when_not_vertical(
    tmp_path: Path, synthetic_font_path: Path
) -> None:
    labels = tmp_path / "codes.txt"
    labels.write_text("AAA\n", encoding="utf-8")
    out = tmp_path / "elsewhere.pdf"
    main(_job_args(labels, synthetic_font_path, "-o", str(out)))
    assert [p.name for p in tmp_path.glob("elsewhere*.pdf")] == [
        "elsewhere_8x3_2-labels.pdf"
    ]


def test_pricing_config_and_flat_price(
    tmp_path: Path,
    label_file: LabelFileFactory,
    synthetic_font_path: Path,
) -> None:
    config_path = tmp_path / PRICING_CONFIG_FILENAME
    config_path.write_text(
        json.dumps(
            {
                "ink_cost_usd_per_sqft": 1.0,
                "substrate_cost_usd_per_sqft": 2.0,
                "print_rate_hours_per_sqft": 0.5,
                "printer_run_cost_usd_per_hour": 10.0,
                "labor_rate_usd_per_hour": 20.0,
                "labor_time_factor": 0.5,
                "markup_percent": 10.0,
            }
        ),
        encoding="utf-8",
    )
    labels = label_file("AAA")
    main(_job_args(labels, synthetic_font_path, "--pricing-config", str(config_path)))
    priced: JsonDict = json.loads(
        job_output_paths(labels, labels.stem)["json"].read_text(encoding="utf-8")
    )
    breakdown = priced["global"]["cost_breakdown"]
    # The headline price is the summed cost with the config's 10% markup.
    assert breakdown["unit_price"] == pytest.approx(breakdown["total_cost"] * 1.1)

    # A flat per-label price overrides the markup maths entirely: each printed
    # label is worth 3.25, so the job total is 3.25 x its 2 printed instances.
    flat = label_file("AAA", name="flat.txt")
    main(
        _job_args(
            flat,
            synthetic_font_path,
            "--pricing-config",
            str(config_path),
            "--flat-label-price",
            "3.25",
        )
    )
    flat_payload: JsonDict = json.loads(
        job_output_paths(flat, flat.stem)["json"].read_text(encoding="utf-8")
    )
    assert flat_payload["global"]["cost_breakdown"]["unit_price"] == 6.5
    assert [
        entry["cost_breakdown"]["unit_price"] for entry in flat_payload["per_label"]
    ] == [3.25]


def test_a_glob_input_processes_every_match(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    synthetic_font_path: Path,
) -> None:
    for name in ("one", "two"):
        (tmp_path / f"{name}-6-chars.txt").write_text("AAA\n", encoding="utf-8")

    assert main(_job_args(tmp_path / "*-6-chars.txt", synthetic_font_path)) == 0

    printed = capsys.readouterr().out
    assert "[1/2] " in printed and "[2/2] " in printed
    assert (tmp_path / "PDF Files" / "one-6-chars_8x3_2-labels.pdf").is_file()
    assert (tmp_path / "PDF Files" / "two-6-chars_8x3_2-labels.pdf").is_file()


def test_output_flag_is_rejected_for_a_glob(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    synthetic_font_path: Path,
) -> None:
    (tmp_path / "one.txt").write_text("AAA\n", encoding="utf-8")
    (tmp_path / "two.txt").write_text("BBB\n", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        main(_job_args(tmp_path / "*.txt", synthetic_font_path, "-o", "combined.pdf"))
    assert "cannot be used when --input matches multiple files" in str(excinfo.value)
    # Nothing was written, because the conflict is caught before any output.
    assert not (tmp_path / "PDF Files").exists()


def test_unmatched_input_exits(tmp_path: Path, synthetic_font_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(_job_args(tmp_path / "*.missing.txt", synthetic_font_path))
    assert "No input files matched" in str(excinfo.value)


def test_label_file_without_labels_exits(
    tmp_path: Path, synthetic_font_path: Path
) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing but a comment\n\n", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        main(_job_args(empty, synthetic_font_path))
    assert f"No labels found in {empty}" in str(excinfo.value)


def test_missing_font_fails_before_any_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labels = tmp_path / "codes.txt"
    labels.write_text("AAA\n", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        main(["-i", str(labels), "--font", str(tmp_path / "nope.ttf"), *BASE_ARGS])
    assert "Font file not found" in str(excinfo.value)
    assert not (tmp_path / "PDF Files").exists()


def test_soft_page_height_splits_into_multiple_pdfs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    synthetic_font_path: Path,
) -> None:
    labels = tmp_path / "codes.txt"
    labels.write_text("AAA\nBBB\nCCC\n", encoding="utf-8")
    # One label per sheet, one sheet per page row, and a 4in soft limit that one
    # sheet-row already exceeds: each sheet-row becomes its own PDF.
    main(
        _job_args(
            labels,
            synthetic_font_path,
            "--page-width",
            "8",
            "--labels-per-sheet-row",
            "1",
            "--labels-per-sheet-col",
            "1",
            "--copies",
            "1",
            "--soft-page-height",
            "4",
        )
    )

    printed = capsys.readouterr().out
    assert "to 3 PDFs:" in printed
    pages = sorted((tmp_path / "PDF Files").glob("codes_8x3_part-*.pdf"))
    assert [path.name for path in pages] == [
        "codes_8x3_part-001_1-labels.pdf",
        "codes_8x3_part-002_1-labels.pdf",
        "codes_8x3_part-003_1-labels.pdf",
    ]
    assert [len(PdfReader(str(p)).pages) for p in pages] == [1, 1, 1]

    organized = job_output_paths(labels, labels.stem)
    payload: JsonDict = json.loads(organized["json"].read_text(encoding="utf-8"))
    assert len(payload["per_pdf"]) == 3
    # Each code is attributed to the PDF that actually carries it.
    rows = [
        line.split(",") for line in organized["csv_report"].read_text().splitlines()[1:]
    ]
    assert {row[1]: row[0] for row in rows} == {
        "AAA": pages[0].name,
        "BBB": pages[1].name,
        "CCC": pages[2].name,
    }


def test_soft_page_height_keeps_an_exact_fit_in_one_pdf(
    tmp_path: Path,
    synthetic_font_path: Path,
) -> None:
    labels = tmp_path / "codes.txt"
    labels.write_text("AAA\nBBB\nCCC\n", encoding="utf-8")
    # 3 codes x 2 copies = 6 labels in 3-label sheet-rows, so the job ends
    # exactly on a sheet-row boundary. Both rows together are 10in, which the
    # 10in limit allows; the end of the job has to be a split candidate for the
    # greedy pass to see that.
    main(
        _job_args(
            labels,
            synthetic_font_path,
            "--labels-per-sheet-col",
            "1",
            "--soft-page-height",
            "10",
        )
    )

    assert [p.name for p in (tmp_path / "PDF Files").glob("codes_8x3_*.pdf")] == [
        "codes_8x3_6-labels.pdf"
    ]
    payload: JsonDict = json.loads(
        job_output_paths(labels, labels.stem)["json"].read_text(encoding="utf-8")
    )
    assert len(payload["per_pdf"]) == 1
