"""Table engine shared by every report writer.

The per-job and consolidated reports render the same shape over and over: an
ordered set of columns, a choice of "internal" (all columns) or "customer"
(a configured subset) view, per-row values, and a trailing totals row. Without
abstraction that shape was re-implemented in every writer, which is where the
two report modules had grown to thousands of lines.

Define the columns once and let :func:`write_csv_table` /
:func:`render_text_table` handle selection, alignment, separators, and totals.
"""

from __future__ import annotations

import csv
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Literal, TypeVar

__all__ = [
    "Column",
    "TextColumn",
    "render_rule_table",
    "render_text_table",
    "select_columns",
    "write_csv_table",
]

T = TypeVar("T")

Alignment = Literal["left", "right"]


@dataclass(frozen=True)
class Column(Generic[T]):
    """One CSV column: how to label it, format a row, and total it.

    ``config_key`` names the entry in ``customer_report`` that reveals this
    column in customer-facing output; ``None`` means the column is always
    present. ``total`` returns the totals-row cell; ``None`` means the column is
    not summable and renders blank. ``customer_only`` hides the column from
    internal output, which is how the per-job report keeps its narrower
    internal column set.
    """

    header: str
    value: Callable[[T], str]
    config_key: str | None = None
    total: Callable[[Sequence[T]], str] | None = None
    customer_only: bool = False


def select_columns(
    columns: Sequence[Column[T]],
    *,
    include_costs: bool,
    customer_config: dict[str, bool] | None = None,
    default_customer_keys: Sequence[str] = (),
    customer_order: Sequence[str] = (),
) -> list[Column[T]]:
    """Return the columns to emit for the requested audience.

    Internal output gets every column except the customer-only ones, in the
    declared order. Customer output gets the always-present columns plus those
    enabled in ``customer_config``; when no config is supplied,
    ``default_customer_keys`` decides, so a run without a pricing file still
    produces a sensible customer file.

    ``customer_order`` lists customer headers in the order the customer file
    should emit them. The two audiences historically arrange a few shared columns
    differently (the per-job totals lead with a measurement column, the customer
    view with its counters), so the declared order drives internal output and
    this list drives customer output. Headers left out keep their declared
    position at the end.
    """
    if include_costs:
        return [column for column in columns if not column.customer_only]
    selected = [
        column
        for column in columns
        if column.config_key is None
        or (
            customer_config.get(column.config_key, False)
            if customer_config is not None
            else column.config_key in default_customer_keys
        )
    ]
    if not customer_order:
        return selected
    rank = {header: index for index, header in enumerate(customer_order)}
    ordered = sorted(
        enumerate(selected),
        key=lambda pair: (rank.get(pair[1].header, len(rank)), pair[0]),
    )
    return [column for _, column in ordered]


def write_csv_table(
    output_path: Path,
    columns: Sequence[Column[T]],
    rows: Sequence[T],
    *,
    include_costs: bool = True,
    customer_config: dict[str, bool] | None = None,
    default_customer_keys: Sequence[str] = (),
    customer_order: Sequence[str] = (),
    total_label: str | None = None,
    total_label_index: int = 0,
) -> None:
    """Write ``rows`` to ``output_path`` as CSV, honouring the audience filter.

    When ``total_label`` is given, a final row is appended whose remaining cells
    come from each column's ``total``. ``total_label_index`` says which column
    carries the label, since some tables lead with a row number and label the
    name column instead.
    """
    selected = select_columns(
        columns,
        include_costs=include_costs,
        customer_config=customer_config,
        default_customer_keys=default_customer_keys,
        customer_order=customer_order,
    )

    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([column.header for column in selected])
        for row in rows:
            writer.writerow([column.value(row) for column in selected])

        if total_label is None:
            return
        total_row: list[str] = []
        for index, column in enumerate(selected):
            if index == total_label_index:
                total_row.append(total_label)
            elif column.total is not None:
                total_row.append(column.total(rows))
            else:
                total_row.append("")
        writer.writerow(total_row)


@dataclass(frozen=True)
class TextColumn(Generic[T]):
    """One fixed-width column of an ASCII report table."""

    header: str
    width: int
    value: Callable[[T], str]
    align: Alignment = "right"
    total: Callable[[Sequence[T]], str] | None = None


def _fit(text: str, width: int, align: Alignment) -> str:
    return f"{text:<{width}}" if align == "left" else f"{text:>{width}}"


def render_text_table(
    columns: Sequence[TextColumn[T]],
    rows: Sequence[T],
    *,
    total_label: str | None = None,
    total_label_index: int = 0,
) -> list[str]:
    """Render ``rows`` as a bordered ASCII table, returning its lines.

    Uses the report house style: ``-``/``-+-`` under the header, and a
    ``=``/``=+=`` rule above the optional totals row. ``total_label_index``
    chooses which column carries the totals label.
    """

    def header_line() -> str:
        return " | ".join(_fit(c.header, c.width, c.align) for c in columns)

    def separator(char: str, joiner: str) -> str:
        return joiner.join(char * c.width for c in columns)

    lines = [
        header_line(),
        separator("-", "-+-"),
    ]
    for row in rows:
        lines.append(" | ".join(_fit(c.value(row), c.width, c.align) for c in columns))

    if total_label is None:
        return lines

    lines.append(separator("=", "=+="))
    total_cells: list[str] = []
    for index, column in enumerate(columns):
        if index == total_label_index:
            total_cells.append(_fit(total_label, column.width, column.align))
        elif column.total is not None:
            total_cells.append(_fit(column.total(rows), column.width, column.align))
        else:
            total_cells.append(_fit("", column.width, column.align))
    lines.append(" | ".join(total_cells))
    return lines


def render_rule_table(
    columns: Sequence[TextColumn[T]], rows: Sequence[T], *, rule: str
) -> list[str]:
    """Render ``rows`` with ``rule`` above and below the table, and no totals.

    The per-label listings use a plain full-width rule instead of the per-column
    ``-+-`` style, so they get their own renderer rather than a flag on the
    house-style one.
    """
    header = " | ".join(_fit(c.header, c.width, c.align) for c in columns)
    body = [
        " | ".join(_fit(c.value(row), c.width, c.align) for c in columns)
        for row in rows
    ]
    return [rule, header, rule, *body, rule]
