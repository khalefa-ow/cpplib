"""Comparing a generated engine's query output against gold results.

The comparison is the reward signal for the whole codegen loop, so it is built
to produce a *readable* verdict rather than a boolean. A model told only "wrong"
rewrites at random; told "row 4, column revenue: expected 1234.50, got 1234.00"
it fixes the accumulator.

Two policies are deliberate:

- **Full comparison, never a checksum.** A hash tells the model nothing it can
  act on, and computing one is no cheaper at these sizes.
- **Tolerance for floating point, exactness for everything else.** A DECIMAL
  sum accumulated in a different order differs in the last digit on a correct
  implementation, so exact equality would reject working code; an integer count
  that is off by one is a real bug, so a blanket tolerance would accept broken
  code.
"""

from __future__ import annotations

import csv
import io
import math
from pathlib import Path
from typing import Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

# Spellings a CSV writer may emit for SQL NULL. Normalized so a gold file
# written by DuckDB and output written by generated C++ agree about emptiness.
_NULL_FORMS = {"", "null", "NULL", "\\N", "None", "nan", "NaN"}

# Which side's first row is a header rather than data. See compare_tables.
_HEADER_MODES = ("none", "gold", "actual", "both")


class ResultTable(BaseModel):
    """A query result as rows of cell strings."""

    model_config = ConfigDict(extra="forbid")

    rows: list[list[str]] = Field(default_factory=list)
    source: str = ""

    @property
    def shape(self) -> tuple[int, int]:
        return len(self.rows), max((len(row) for row in self.rows), default=0)

    def preview(self, limit: int = 5) -> str:
        """First few rows, for a fix prompt."""
        head = [" | ".join(row) for row in self.rows[:limit]]
        if len(self.rows) > limit:
            head.append(f"… {len(self.rows) - limit} more row(s)")
        return "\n".join(head) or "(no rows)"


class Comparison(BaseModel):
    """The verdict on one query's output."""

    model_config = ConfigDict(extra="forbid")

    matched: bool
    reason: str = ""
    row: Optional[int] = None
    column: Optional[int] = None
    column_name: str = ""
    expected: Optional[str] = None
    actual: Optional[str] = None
    expected_shape: tuple[int, int] = (0, 0)
    actual_shape: tuple[int, int] = (0, 0)

    def brief(self) -> str:
        """A rendering aimed at the model that has to fix the mismatch."""
        if self.matched:
            return f"MATCH ({self.expected_shape[0]} row(s), {self.expected_shape[1]} column(s))"
        lines = [f"MISMATCH: {self.reason}"]
        if self.row is not None:
            where = f"row {self.row}"
            if self.column is not None:
                where += f", column {self.column}"
                if self.column_name:
                    where += f" ({self.column_name})"
            lines.append(f"  first difference at {where}")
            lines.append(f"    expected: {self.expected!r}")
            lines.append(f"    actual:   {self.actual!r}")
        lines.append(
            f"  expected shape {self.expected_shape[0]}x{self.expected_shape[1]}, "
            f"actual {self.actual_shape[0]}x{self.actual_shape[1]}"
        )
        return "\n".join(lines)


def parse_table(text: str, delimiter: str = ",", source: str = "") -> ResultTable:
    """Parse delimited text into a table.

    Uses :mod:`csv` rather than ``str.split`` so a quoted value containing the
    delimiter survives — customer names with commas in them are exactly the case
    that makes a hand-rolled splitter report a phantom mismatch.
    """
    cleaned = text.replace("\r\n", "\n").strip("\n")
    if not cleaned.strip():
        return ResultTable(rows=[], source=source)
    reader = csv.reader(io.StringIO(cleaned), delimiter=delimiter)
    rows = [[cell.strip() for cell in row] for row in reader if any(cell.strip() for cell in row)]
    return ResultTable(rows=rows, source=source)


def load_table(path: str | Path, delimiter: str = ",") -> ResultTable:
    """Read a result file from disk."""
    file = Path(path)
    return parse_table(file.read_text(encoding="utf-8", errors="replace"), delimiter, str(file))


def compare_tables(
    expected: ResultTable,
    actual: ResultTable,
    float_tolerance: float = 1e-6,
    header_mode: str = "none",
    sort_rows: bool = False,
    header: Sequence[str] = (),
) -> Comparison:
    """Compare two result tables and locate the first difference.

    Args:
        float_tolerance: Absolute tolerance for cells that parse as numbers on
            both sides.
        header_mode: Which side carries a header row that is not data. Spelled
            out rather than guessed: DuckDB writes gold with a header while a
            generated engine is told to print bare rows, so ``"gold"`` is the
            common case, and inferring it from row counts would let a genuine
            off-by-one-row bug be silently absorbed.

            - ``"none"``   - neither side has one; compare every row.
            - ``"gold"``   - drop the expected side's first row.
            - ``"actual"`` - drop the actual side's first row.
            - ``"both"``   - drop both.
        sort_rows: Compare as multisets. Only correct for a query with no
            ``ORDER BY``: for an ordered query, row order *is* part of the
            answer and sorting would hide a real bug.
        header: Column names, used to make the mismatch message readable. Taken
            from the dropped header row when one is available.
    """
    mode = (header_mode or "none").lower()
    if mode not in _HEADER_MODES:
        raise ValueError(
            f"header_mode must be one of {', '.join(sorted(_HEADER_MODES))}, got {header_mode!r}."
        )

    left = list(expected.rows)
    right = list(actual.rows)
    names = list(header)
    if mode in ("gold", "both") and left:
        if not names:
            names = left[0]
        left = left[1:]
    if mode in ("actual", "both") and right:
        if not names:
            names = right[0]
        right = right[1:]

    if sort_rows:
        left = sorted(left, key=_row_key)
        right = sorted(right, key=_row_key)

    expected_shape = (len(left), max((len(row) for row in left), default=0))
    actual_shape = (len(right), max((len(row) for row in right), default=0))

    if len(left) != len(right):
        # Reported before the cell scan: a row-count difference explains every
        # cell difference after it, so showing a cell diff first would mislead.
        row = _first_row_difference(left, right)
        return Comparison(
            matched=False,
            reason=f"row count differs: expected {len(left)}, got {len(right)}",
            expected_shape=expected_shape,
            actual_shape=actual_shape,
            row=row,
            expected=_join(left[row - 1]) if row is not None and row <= len(left) else None,
            actual=_join(right[row - 1]) if row is not None and row <= len(right) else None,
        )

    for row_index, (expected_row, actual_row) in enumerate(zip(left, right), start=1):
        if len(expected_row) != len(actual_row):
            return Comparison(
                matched=False,
                reason=(
                    f"column count differs on row {row_index}: "
                    f"expected {len(expected_row)}, got {len(actual_row)}"
                ),
                row=row_index,
                expected=_join(expected_row),
                actual=_join(actual_row),
                expected_shape=expected_shape,
                actual_shape=actual_shape,
            )
        for column_index, (expected_cell, actual_cell) in enumerate(
            zip(expected_row, actual_row), start=1
        ):
            if cells_equal(expected_cell, actual_cell, float_tolerance):
                continue
            return Comparison(
                matched=False,
                reason="cell value differs",
                row=row_index,
                column=column_index,
                column_name=names[column_index - 1] if column_index <= len(names) else "",
                expected=expected_cell,
                actual=actual_cell,
                expected_shape=expected_shape,
                actual_shape=actual_shape,
            )

    return Comparison(matched=True, expected_shape=expected_shape, actual_shape=actual_shape)


def cells_equal(expected: str, actual: str, float_tolerance: float = 1e-6) -> bool:
    """Whether two cells agree, numerically when both are numbers."""
    left = expected.strip()
    right = actual.strip()
    if left in _NULL_FORMS and right in _NULL_FORMS:
        return True
    if left == right:
        return True
    left_number = _as_float(left)
    right_number = _as_float(right)
    if left_number is None or right_number is None:
        # Not both numeric: fall back to case- and whitespace-insensitive text,
        # which forgives 'BUILDING ' padded by a CHAR(10) column.
        return left.casefold() == right.casefold()
    if math.isnan(left_number) and math.isnan(right_number):
        return True
    return math.isclose(left_number, right_number, rel_tol=0.0, abs_tol=float_tolerance)


def _as_float(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_key(row: Sequence[str]) -> tuple[str, ...]:
    return tuple(cell.strip().casefold() for cell in row)


def _join(row: Sequence[str]) -> str:
    return " | ".join(row)


def _first_row_difference(left: list[list[str]], right: list[list[str]]) -> Optional[int]:
    """1-indexed first differing row, or None when one side is a prefix."""
    for index, (expected_row, actual_row) in enumerate(zip(left, right), start=1):
        if _row_key(expected_row) != _row_key(actual_row):
            return index
    return None
