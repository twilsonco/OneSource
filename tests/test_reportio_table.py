"""Tests for :mod:`vinyllabels.reportio.table`, the shared table engine."""

from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path

import pytest

from vinyllabels.reportio.table import (
    Column,
    TextColumn,
    render_rule_table,
    render_text_table,
    select_columns,
    sized_column,
    table_width,
    write_csv_table,
)


class Row:
    """A tiny row type with a name and a numeric amount."""

    def __init__(self, name: str, amount: float) -> None:
        self.name = name
        self.amount = amount


def rows() -> list[Row]:
    return [Row("alpha", 1.5), Row("beta", 2.5)]


def columns() -> list[Column[Row]]:
    return [
        Column("Name", lambda row: row.name),
        Column(
            "Amount",
            lambda row: f"{row.amount:.2f}",
            "amount",
            total=lambda items: f"{sum(i.amount for i in items):.2f}",
        ),
        Column(
            "Secret",
            lambda row: "internal-only",
            "secret",
            customer_only=True,
        ),
    ]


def headers(selected: Sequence[Column[Row]]) -> list[str]:
    return [column.header for column in selected]


def test_internal_view_drops_customer_only_columns() -> None:
    assert headers(select_columns(columns(), include_costs=True)) == ["Name", "Amount"]


def test_customer_view_defaults_to_the_declared_keys() -> None:
    selected = select_columns(
        columns(), include_costs=False, default_customer_keys=("amount",)
    )
    assert headers(selected) == ["Name", "Amount"]


def test_customer_view_without_defaults_keeps_only_always_present() -> None:
    selected = select_columns(columns(), include_costs=False)
    assert headers(selected) == ["Name"]


def test_customer_view_follows_the_config() -> None:
    selected = select_columns(
        columns(), include_costs=False, customer_config={"secret": True}
    )
    assert headers(selected) == ["Name", "Secret"]


def test_customer_config_false_hides_an_otherwise_default_column() -> None:
    selected = select_columns(
        columns(),
        include_costs=False,
        customer_config={"amount": False},
        default_customer_keys=("amount",),
    )
    assert headers(selected) == ["Name"]


def test_customer_order_reorders_and_appends_unlisted() -> None:
    selected = select_columns(
        columns(),
        include_costs=False,
        customer_config={"amount": True},
        customer_order=("Amount", "Name"),
    )
    assert headers(selected) == ["Amount", "Name"]


def test_customer_order_ignores_headers_that_werent_selected() -> None:
    selected = select_columns(
        columns(),
        include_costs=False,
        customer_order=("Secret", "Amount", "Name"),
    )
    assert headers(selected) == ["Name"]


def test_write_csv_internal_with_totals(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    write_csv_table(out, columns(), rows(), include_costs=True, total_label="TOTAL")
    table = list(csv.reader(out.read_text(encoding="utf-8").splitlines()))
    assert table == [
        ["Name", "Amount"],
        ["alpha", "1.50"],
        ["beta", "2.50"],
        ["TOTAL", "4.00"],
    ]


def test_write_csv_without_a_total_label_has_no_totals_row(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    write_csv_table(out, columns(), rows(), include_costs=True)
    table = list(csv.reader(out.read_text(encoding="utf-8").splitlines()))
    assert len(table) == 3


def test_write_csv_total_label_index_moves_the_label(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    write_csv_table(
        out,
        columns(),
        rows(),
        include_costs=True,
        total_label="TOTAL",
        total_label_index=1,
    )
    table = list(csv.reader(out.read_text(encoding="utf-8").splitlines()))
    assert table[-1] == ["", "TOTAL"]


def test_write_csv_leaves_unsummable_columns_blank(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    write_csv_table(
        out,
        [Column("A", lambda row: row.name), Column("B", lambda row: "x")],
        rows(),
        total_label="TOTAL",
    )
    table = list(csv.reader(out.read_text(encoding="utf-8").splitlines()))
    assert table[-1] == ["TOTAL", ""]


def test_write_csv_customer_view(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    write_csv_table(
        out,
        columns(),
        rows(),
        include_costs=False,
        customer_config={"amount": True},
        customer_order=("Amount", "Name"),
        total_label="TOTAL",
    )
    table = list(csv.reader(out.read_text(encoding="utf-8").splitlines()))
    assert table[0] == ["Amount", "Name"]
    assert table[1] == ["1.50", "alpha"]


def test_write_csv_uses_crlf_line_endings(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    write_csv_table(out, columns(), rows())
    assert b"\r\n" in out.read_bytes()


def test_write_csv_empty_rows_still_emits_a_total(tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    write_csv_table(out, columns(), [], include_costs=True, total_label="TOTAL")
    table = list(csv.reader(out.read_text(encoding="utf-8").splitlines()))
    assert table == [["Name", "Amount"], ["TOTAL", "0.00"]]


def text_columns() -> list[TextColumn[Row]]:
    return [
        TextColumn("Name", 6, lambda row: row.name, "left"),
        TextColumn(
            "Amount",
            6,
            lambda row: f"{row.amount:.2f}",
            total=lambda items: f"{sum(i.amount for i in items):.2f}",
        ),
    ]


def test_render_text_table_header_and_separator() -> None:
    lines = render_text_table(text_columns(), rows())
    assert lines == [
        "Name   | Amount",
        "-------+-------",
        "alpha  |   1.50",
        "beta   |   2.50",
    ]


def test_render_text_table_totals_row() -> None:
    lines = render_text_table(text_columns(), rows(), total_label="TOTAL")
    assert lines[4] == "=======+======="
    assert lines[5] == "TOTAL  |   4.00"


def test_render_text_table_total_label_index() -> None:
    lines = render_text_table(
        text_columns(), rows(), total_label="TOTAL", total_label_index=1
    )
    assert lines[5] == "       |  TOTAL"


def test_render_text_table_blank_cell_for_unsummable_column() -> None:
    columns: list[TextColumn[Row]] = [TextColumn("A", 3, lambda row: row.name, "left")]
    lines = render_text_table(columns, [Row("x", 0.0)], total_label="T")
    assert lines == ["A  ", "---", "x  ", "===", "T  "]


def test_render_text_table_no_rows() -> None:
    lines = render_text_table(text_columns(), [])
    assert len(lines) == 2


@pytest.mark.parametrize(
    "align",
    ["left", "right"],
)
def test_render_text_table_alignment(align: str) -> None:
    columns: list[TextColumn[Row]] = [
        TextColumn("H", 8, lambda row: row.name, align)  # type: ignore[arg-type]
    ]
    lines = render_text_table(columns, [Row("ab", 0.0)])
    body = lines[2]
    assert body == ("ab      " if align == "left" else "      ab")


def test_render_rule_table_wraps_in_the_given_rule() -> None:
    rule = "-" * 12
    columns: list[TextColumn[Row]] = [TextColumn("A", 4, lambda row: row.name, "left")]
    lines = render_rule_table(columns, [Row("ab", 0.0)], rule=rule)
    assert lines == [rule, "A   ", rule, "ab  ", rule]


def test_render_rule_table_no_rows() -> None:
    rule = "="
    lines = render_rule_table([TextColumn("A", 2, lambda row: "")], [], rule=rule)
    assert lines == [rule, " A", rule, rule]


def test_sized_column_fits_longest_value_over_header() -> None:
    column = sized_column("Name", lambda row: row.name, rows())
    assert column.width == 5  # "alpha" beats the 4-char header
    assert column.align == "left"


def test_sized_column_floors_at_header_width() -> None:
    column = sized_column("Header", lambda row: row.name, rows())
    assert column.width == 6
    empty: TextColumn[Row] = sized_column("Header", lambda row: row.name, [])
    assert empty.width == 6


def test_sized_column_carries_total_through_the_renderer() -> None:
    columns = [
        sized_column("N", lambda row: row.name, [Row("long-name", 0.0)]),
        sized_column(
            "V", lambda row: row.name, [Row("long-name", 0.0)], total=lambda rows: "T"
        ),
    ]
    lines = render_text_table(columns, [Row("long-name", 0.0)], total_label="TOTAL")
    # Both the separator and the totals row follow the grown widths, and the
    # sized column's own total renders in its cell.
    assert lines[1] == "-" * 9 + "-+-" + "-" * 9
    assert lines[4] == f"{'TOTAL':<9} | T{'':<8}"


def test_table_width_spans_columns_and_joiners() -> None:
    assert table_width(text_columns()) == 6 + 6 + 3
    assert table_width([TextColumn("A", 4, lambda row: "")]) == 4
    assert table_width([]) == 0


def test_column_defaults() -> None:
    column: Column[Row] = Column("H", lambda row: row.name)
    assert column.config_key is None
    assert column.total is None
    assert column.customer_only is False


def test_text_column_defaults_to_right_alignment() -> None:
    column: TextColumn[Row] = TextColumn("H", 4, lambda row: "x")
    assert column.align == "right"
    assert column.total is None


def test_generic_columns_are_usable_with_different_row_types() -> None:
    ints: list[Column[int]] = [Column("N", str, total=lambda items: str(sum(items)))]
    text: list[TextColumn[int]] = [
        TextColumn("N", 4, str, total=lambda items: str(sum(items)))
    ]
    lines = render_text_table(
        text,
        [1, 2, 3],
        total_label="T",
        total_label_index=1,
    )
    assert lines[2:5] == ["   1", "   2", "   3"]
    assert ints[0].header == "N"
    assert ints[0].total is not None
    assert ints[0].total([1, 2, 3]) == "6"
