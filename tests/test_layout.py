"""Tests for :mod:`vinyllabels.layout` -- every piece of pure page geometry."""

from __future__ import annotations

from collections.abc import Callable
import math
from pathlib import Path

import pytest

from vinyllabels.layout import (
    CAP_HEIGHT_RATIO,
    COPIES_PER_LABEL,
    LABEL_H_IN,
    LABEL_H_MARGIN_IN,
    LABEL_V_MARGIN_IN,
    LABEL_W_IN,
    LABELS_PER_SHEET_COL,
    LABELS_PER_SHEET_ROW,
    PAGE_BOTTOM_MARGIN_IN,
    PAGE_LEFT_MARGIN_IN,
    PAGE_RIGHT_MARGIN_IN,
    PAGE_TOP_MARGIN_IN,
    PAGE_W_IN,
    SOFT_PAGE_HEIGHT_IN,
    TEXT_HEIGHT_IN,
    VERTICAL_GAP_IN,
    JobConfig,
    grid_page_height,
    label_position_in,
    minimum_page_height,
    page_breaks,
    page_height_in,
    resolve_sheet_cols,
    sheet_origin_in,
)

JobConfigFactory = Callable[..., JobConfig]


@pytest.fixture
def grid_config(make_job_config: JobConfigFactory) -> JobConfig:
    """Two columns by two rows per sheet, three sheets across a 52in page."""
    return make_job_config(labels_per_sheet_row=2, labels_per_sheet_col=2)


def test_config_defaults_match_the_module_constants(
    make_job_config: JobConfigFactory,
) -> None:
    config = make_job_config()
    assert config.page_w_in == PAGE_W_IN
    assert (
        config.page_left_margin_in,
        config.page_right_margin_in,
    ) == (PAGE_LEFT_MARGIN_IN, PAGE_RIGHT_MARGIN_IN)
    assert (
        config.page_top_margin_in,
        config.page_bottom_margin_in,
    ) == (PAGE_TOP_MARGIN_IN, PAGE_BOTTOM_MARGIN_IN)
    assert (config.label_w_in, config.label_h_in) == (LABEL_W_IN, LABEL_H_IN)
    assert (config.label_h_margin_in, config.label_v_margin_in) == (
        LABEL_H_MARGIN_IN,
        LABEL_V_MARGIN_IN,
    )
    assert (config.labels_per_sheet_row, config.labels_per_sheet_col) == (
        LABELS_PER_SHEET_ROW,
        LABELS_PER_SHEET_COL,
    )
    assert config.vertical_gap_in == VERTICAL_GAP_IN
    assert config.copies_per_label == COPIES_PER_LABEL
    assert config.text_height_in == TEXT_HEIGHT_IN
    assert config.cap_height_ratio == CAP_HEIGHT_RATIO
    assert config.soft_page_height_in == SOFT_PAGE_HEIGHT_IN
    assert config.vertical_labels is False
    assert config.output_path is None
    assert config.flat_label_price is None


def test_default_derived_geometry(make_job_config: JobConfigFactory) -> None:
    config = make_job_config()
    assert config.usable_page_w_in == 52.0
    assert (config.foot_w_in, config.foot_h_in) == (8.0, 3.0)
    assert (config.eff_per_sheet_row, config.eff_per_sheet_col) == (0, 8)
    assert config.sheet_cols == 6
    assert config.sheet_w_in == 48.0
    assert config.sheet_h_in == 24.0
    assert config.labels_per_sheet == 48
    assert config.sheets_per_row == 1
    assert config.horizontal_gap_in == 0.0
    assert config.label_area_sq_in == 24.0
    assert config.label_size == (8.0, 3.0)
    assert config.text_area_w_in == 7.5
    assert config.font_size_pt == pytest.approx(2.0 * 72.0 / 0.728)


def test_usable_width_subtracts_both_page_margins(
    make_job_config: JobConfigFactory,
) -> None:
    config = make_job_config(
        page_w_in=50.0, page_left_margin_in=1.0, page_right_margin_in=2.0
    )
    assert config.usable_page_w_in == 47.0


def test_resolve_sheet_cols_expands_auto() -> None:
    assert resolve_sheet_cols(0, 52.0, 8.0) == 6
    assert resolve_sheet_cols(3, 52.0, 8.0) == 3
    assert resolve_sheet_cols(0, 7.0, 8.0) == 0


def test_labels_wider_than_the_page_yield_no_columns(
    make_job_config: JobConfigFactory,
) -> None:
    config = make_job_config(label_w_in=60.0)
    assert config.sheet_cols == 0


def test_exactly_fitting_page(make_job_config: JobConfigFactory) -> None:
    config = make_job_config(page_w_in=16.0)
    assert config.sheet_cols == 2
    assert config.sheet_w_in == 16.0


def test_fixed_grid_geometry(make_job_config: JobConfigFactory) -> None:
    config = make_job_config(labels_per_sheet_row=2, labels_per_sheet_col=4)
    assert config.sheet_cols == 2
    assert config.sheet_w_in == 16.0
    assert config.sheet_h_in == 12.0
    assert config.labels_per_sheet == 8
    assert config.sheets_per_row == 3
    assert config.horizontal_gap_in == pytest.approx((52.0 - 48.0) / 2)


def test_single_sheet_per_row_has_no_gap(make_job_config: JobConfigFactory) -> None:
    config = make_job_config(labels_per_sheet_row=6, labels_per_sheet_col=4)
    assert config.sheets_per_row == 1
    assert config.horizontal_gap_in == 0.0


def test_vertical_labels_swap_the_footprint(make_job_config: JobConfigFactory) -> None:
    config = make_job_config(vertical_labels=True)
    assert (config.foot_w_in, config.foot_h_in) == (3.0, 8.0)
    # An auto (0) column count stays on its own page axis: no transposition.
    assert (config.eff_per_sheet_row, config.eff_per_sheet_col) == (0, 8)
    assert config.sheet_cols == 52 // 3


def test_vertical_labels_transpose_a_fixed_grid(
    make_job_config: JobConfigFactory,
) -> None:
    config = make_job_config(
        vertical_labels=True, labels_per_sheet_row=2, labels_per_sheet_col=4
    )
    assert (config.eff_per_sheet_row, config.eff_per_sheet_col) == (4, 2)
    assert config.sheet_cols == 4
    assert config.sheet_w_in == 12.0
    assert config.sheet_h_in == 16.0
    assert config.labels_per_sheet == 8
    assert config.sheets_per_row == 4
    assert config.horizontal_gap_in == pytest.approx((52.0 - 48.0) / 3)


def test_sheet_origin_walks_the_grid(grid_config: JobConfig) -> None:
    assert sheet_origin_in(grid_config, 0, 26.0) == (0.0, 25.0)
    assert sheet_origin_in(grid_config, 1, 26.0) == (18.0, 25.0)
    assert sheet_origin_in(grid_config, 3, 26.0) == (0.0, 17.0)


def test_page_height_for_auto_rows(make_job_config: JobConfigFactory) -> None:
    config = make_job_config(labels_per_sheet_col=0)
    assert config.sheet_h_in == 0
    assert config.labels_per_sheet == 0
    assert grid_page_height(config, 1) == 5.0
    assert grid_page_height(config, 12) == 8.0
    assert minimum_page_height(config, 12) == 8.0
    assert minimum_page_height(config, 0) == 2.0


def test_page_height_for_fixed_sheets(make_job_config: JobConfigFactory) -> None:
    config = make_job_config()
    assert grid_page_height(config, 48) == 26.0
    assert grid_page_height(config, 49) == 52.0
    assert page_height_in is grid_page_height


def test_minimum_page_height_trims_a_partial_final_sheet(
    make_job_config: JobConfigFactory,
) -> None:
    config = make_job_config()
    assert minimum_page_height(config, 0) == 2.0
    # A single label trims the 8-row sheet down to the one row it uses.
    assert minimum_page_height(config, 1) == 5.0
    assert minimum_page_height(config, 7) == 8.0
    # A full sheet is not trimmed.
    assert minimum_page_height(config, 48) == 26.0


def test_minimum_page_height_with_multiple_sheet_rows(
    make_job_config: JobConfigFactory,
) -> None:
    config = make_job_config(
        page_w_in=16.0, labels_per_sheet_row=2, labels_per_sheet_col=2
    )
    assert config.sheets_per_row == 1
    # 5 labels = 2 sheets stacked; the second sheet uses one of its two rows.
    assert minimum_page_height(config, 5) == 13.0
    assert grid_page_height(config, 5) == 16.0


def test_minimum_page_height_keeps_a_full_final_sheet(
    make_job_config: JobConfigFactory,
) -> None:
    # One sheet-row holding a single sheet that is itself full (4 labels in a
    # 2x2 sheet), so there is no partial row to trim away.
    config = make_job_config(
        page_w_in=16.0, labels_per_sheet_row=2, labels_per_sheet_col=2
    )
    assert minimum_page_height(config, 4) == 8.0
    assert minimum_page_height(config, 4) == grid_page_height(config, 4)


def test_minimum_page_height_only_trims_a_lone_final_sheet(
    make_job_config: JobConfigFactory,
) -> None:
    # Two sheets share the last sheet-row here, so its height stays standard
    # even though the second sheet is only half full.
    config = make_job_config(
        page_w_in=32.0, labels_per_sheet_row=2, labels_per_sheet_col=2
    )
    assert config.sheets_per_row == 2
    assert minimum_page_height(config, 5) == 8.0
    assert minimum_page_height(config, 5) == grid_page_height(config, 5)


def test_label_position_with_auto_rows(make_job_config: JobConfigFactory) -> None:
    config = make_job_config(labels_per_sheet_col=0)
    assert label_position_in(config, 0, 8.0) == (0.0, 4.0)
    assert label_position_in(config, 6, 8.0) == (0.0, 1.0)
    assert label_position_in(config, 7, 8.0) == (8.0, 1.0)


def test_label_position_with_fixed_sheets(grid_config: JobConfig) -> None:
    # 2x2 sheet of 8x3 labels: sheet height 6, page height 26 for one sheet.
    assert label_position_in(grid_config, 0, 26.0) == (0.0, 22.0)
    assert label_position_in(grid_config, 1, 26.0) == (8.0, 22.0)
    assert label_position_in(grid_config, 2, 26.0) == (0.0, 19.0)
    assert label_position_in(grid_config, 4, 26.0) == (18.0, 22.0)


def test_label_position_with_vertical_labels(
    make_job_config: JobConfigFactory,
) -> None:
    config = make_job_config(vertical_labels=True, labels_per_sheet_col=0)
    x_in, y_in = label_position_in(config, 0, 10.0)
    assert x_in == 0.0
    assert y_in == pytest.approx(10.0 - 1.0 - 8.0)


def test_page_breaks_without_instances(make_job_config: JobConfigFactory) -> None:
    assert page_breaks(make_job_config(), 0) == []


def test_page_breaks_single_page(grid_config: JobConfig) -> None:
    assert page_breaks(grid_config, 10, 1000.0) == [(0, 9)]


def test_page_breaks_splits_at_the_last_fitting_row(grid_config: JobConfig) -> None:
    # stride = 4 labels/sheet * 3 sheets/row = 12; one sheet-row is 8in tall.
    assert page_breaks(grid_config, 36, 9.0) == [(0, 11), (12, 23), (24, 35)]


def test_page_breaks_always_emits_one_row_per_page(grid_config: JobConfig) -> None:
    assert page_breaks(grid_config, 36, 5.0) == [(0, 11), (12, 23), (24, 35)]


def test_page_breaks_use_the_config_limit_by_default(
    make_job_config: JobConfigFactory,
) -> None:
    config = make_job_config(labels_per_sheet_col=0, soft_page_height_in=8.0)
    assert page_breaks(config, 18) == [(0, 11), (12, 17)]


def test_page_breaks_keep_a_final_row_on_the_previous_page(
    grid_config: JobConfig,
) -> None:
    # Two sheet-rows are 16in, so both fit under a 17in limit. 24 is an exact
    # multiple of the 12-label sheet-row, which leaves the end of the job as the
    # only candidate after the first row -- it still has to be considered.
    assert page_breaks(grid_config, 24, 17.0) == [(0, 23)]


def test_page_breaks_merge_short_trailing_pages(grid_config: JobConfig) -> None:
    # 42 instances under a 17in limit: a 24-label page, then an 18-label tail.
    # The tail crosses a sheet-row at 36, but all 18 fit together, so the job
    # stays in two PDFs instead of splitting 24 + 12 + 6.
    assert page_breaks(grid_config, 42, 17.0) == [(0, 23), (24, 41)]


def test_page_breaks_for_the_vertical_print_jobs(
    make_job_config: JobConfigFactory,
) -> None:
    """Regressions for the vertical jobs the print shell scripts run.

    Each geometry is a real ``generate`` invocation on a 50in page with a 4in
    design height, and each count is a real label file's lines doubled by
    ``--copies 2``. Every job must use the fewest pages the 80in default allows.
    """

    def job(label_w_in: float, row: int, col: int) -> JobConfig:
        return make_job_config(
            page_w_in=50.0,
            label_w_in=label_w_in,
            label_h_in=4.0,
            vertical_labels=True,
            labels_per_sheet_row=row,
            labels_per_sheet_col=col,
        )

    narrow = job(10.0, 3, 6)
    wide = job(12.0, 3, 6)
    extra_wide = job(32.0, 1, 4)

    # 12 codes: two sheet-rows of 12, 68in together.
    assert page_breaks(extra_wide, 24) == [(0, 23)]
    # 60 codes: a 72-label page, then the 48-label remainder, which fits as one.
    assert page_breaks(narrow, 120) == [(0, 71), (72, 119)]
    # 416 codes: eleven 72-label pages plus a 40-label tail, not twelve plus two.
    assert len(page_breaks(wide, 832)) == 12

    # No two adjacent pages could have been printed as one taller page.
    for config, count in ((narrow, 120), (wide, 832)):
        ranges = page_breaks(config, count)
        for (first_start, _), (_, last_end) in zip(ranges, ranges[1:], strict=False):
            merged = grid_page_height(config, last_end - first_start + 1)
            assert merged > config.soft_page_height_in


def test_page_break_ranges_cover_every_instance(grid_config: JobConfig) -> None:
    ranges = page_breaks(grid_config, 37, 9.0)
    covered = [idx for start, end in ranges for idx in range(start, end + 1)]
    assert covered == list(range(37))
    assert all(start <= end for start, end in ranges)


def test_page_breaks_never_exceed_the_limit_unnecessarily(
    grid_config: JobConfig,
) -> None:
    for start, end in page_breaks(grid_config, 60, 9.0):
        height = grid_page_height(grid_config, end - start + 1)
        assert height <= 9.0 or (end - start + 1) == 12


def test_font_size_round_trips_to_cap_height(make_job_config: JobConfigFactory) -> None:
    config = make_job_config(text_height_in=1.5, cap_height_ratio=0.5)
    assert config.font_size_pt == 216.0
    assert math.isclose(
        config.font_size_pt * config.cap_height_ratio / 72.0, config.text_height_in
    )


def test_input_path_is_kept(tmp_path: Path) -> None:
    assert JobConfig(input_path=tmp_path).input_path == tmp_path
