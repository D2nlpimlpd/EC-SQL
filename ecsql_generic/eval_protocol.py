"""Execution and semantic-evidence metrics shared by benchmark runners."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class SemanticEvidence:
    executed: bool
    result_exact_match: bool
    table_coverage: bool
    column_coverage: bool
    no_null_placeholder: bool

    @property
    def semantic_pass(self) -> bool:
        return all(
            [
                self.executed,
                self.result_exact_match,
                self.table_coverage,
                self.column_coverage,
                self.no_null_placeholder,
            ]
        )


def normalize_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        return round(value, 8)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="microseconds").removesuffix(".000000")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, bool)):
        return value
    text = str(value).strip()
    text = re.sub(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.0+$", r"\1", text)
    return text


def result_signature(columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> tuple:
    normalized_cols = tuple(str(col).upper() for col in columns)
    normalized_rows = sorted(
        (tuple(normalize_cell(v) for v in row) for row in rows),
        key=lambda row: tuple(repr(value) for value in row),
    )
    return normalized_cols, tuple(normalized_rows)


def contains_null_placeholder(sql: str) -> bool:
    return "NULL AS" in (sql or "").upper()
