"""SQL dialect adapters used by the generic EC-SQL pipeline.

The older code path mixed Oracle syntax, validation, and error parsing directly
inside generation logic.  This module separates those concerns so the same
retrieval and repair code can run on SQLite, PostgreSQL, Snowflake, BigQuery,
Oracle, or another adapter with a small surface area.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional


_DML_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


def _strip_terminal_semicolon(sql: str) -> str:
    return sql.strip().rstrip(";").strip()


@dataclass(frozen=True)
class Dialect:
    name: str
    identifier_quote: str = '"'
    supports_limit: bool = True
    uses_rownum: bool = False

    def quote_identifier(self, identifier: str) -> str:
        ident = identifier.strip()
        if not ident:
            return ident
        if ident.startswith(self.identifier_quote) and ident.endswith(
            self.identifier_quote
        ):
            return ident
        escaped = ident.replace(self.identifier_quote, self.identifier_quote * 2)
        return f"{self.identifier_quote}{escaped}{self.identifier_quote}"

    def limit_query(self, sql: str, n: int) -> str:
        base = _strip_terminal_semicolon(sql)
        if self.uses_rownum:
            return f"SELECT * FROM ({base}) WHERE ROWNUM <= {int(n)}"
        if re.search(r"\bLIMIT\s+\d+\b", base, flags=re.IGNORECASE):
            return base
        return f"{base} LIMIT {int(n)}"

    def date_literal(self, value: str) -> str:
        escaped = (value or "").replace("'", "''")
        return f"'{escaped}'"

    def date_range_condition(self, expression: str, start_date: str, end_date: str) -> str:
        return (
            f"{expression} >= {self.date_literal(start_date)} "
            f"AND {expression} <= {self.date_literal(end_date)}"
        )

    def validate_select_only(self, sql: str) -> Optional[str]:
        stripped = _strip_terminal_semicolon(sql)
        if not stripped:
            return "EMPTY_SQL"
        if not re.match(r"^\s*(WITH|SELECT)\b", stripped, flags=re.IGNORECASE):
            return "NOT_SELECT"
        if _DML_RE.search(stripped):
            return "DML_FORBIDDEN"
        return None

    def classify_error(self, message: str) -> str:
        text = (message or "").upper()
        if any(tok in text for tok in ("NO SUCH TABLE", "TABLE OR VIEW DOES NOT EXIST")):
            return "MISSING_TABLE"
        if any(tok in text for tok in ("NO SUCH COLUMN", "INVALID IDENTIFIER", "INVALID COLUMN")):
            return "INVALID_COL"
        if "GROUP BY" in text or "NOT A GROUP BY" in text:
            return "GROUPBY_ERR"
        if "ALIAS" in text or "AMBIGUOUS" in text:
            return "ALIAS_ERR"
        if "SYNTAX" in text or "PARSE" in text:
            return "SYNTAX_ERR"
        return "GENERIC"

    def error_prompt_rules(self) -> str:
        return (
            f"Use {self.name} SQL. Generate one read-only SELECT statement. "
            "Use only schema-provided identifiers."
        )


class GenericDialect(Dialect):
    def __init__(self) -> None:
        super().__init__(name="generic")


class SQLiteDialect(Dialect):
    def __init__(self) -> None:
        super().__init__(name="sqlite", identifier_quote='"', supports_limit=True)


class DuckDBDialect(Dialect):
    def __init__(self) -> None:
        super().__init__(name="duckdb", identifier_quote='"', supports_limit=True)


class PostgresDialect(Dialect):
    def __init__(self) -> None:
        super().__init__(name="postgres", identifier_quote='"', supports_limit=True)


class SnowflakeDialect(Dialect):
    def __init__(self) -> None:
        super().__init__(name="snowflake", identifier_quote='"', supports_limit=True)


class BigQueryDialect(Dialect):
    def __init__(self) -> None:
        super().__init__(name="bigquery", identifier_quote="`", supports_limit=True)


class OracleDialect(Dialect):
    def __init__(self) -> None:
        super().__init__(
            name="oracle",
            identifier_quote='"',
            supports_limit=False,
            uses_rownum=True,
        )

    def classify_error(self, message: str) -> str:
        text = (message or "").upper()
        if "ORA-00942" in text:
            return "MISSING_TABLE"
        if "ORA-00904" in text:
            return "INVALID_COL"
        if "ORA-00979" in text or "ORA-00937" in text:
            return "GROUPBY_ERR"
        if "ORA-00923" in text or "ORA-00918" in text:
            return "ALIAS_ERR"
        if text.startswith("ORA-") or "ORA-" in text:
            return "SYNTAX_ERR"
        return super().classify_error(message)

    def date_literal(self, value: str) -> str:
        escaped = (value or "").replace("'", "''")
        return f"TO_DATE('{escaped}','YYYY-MM-DD')"

    def error_prompt_rules(self) -> str:
        return (
            "Use Oracle-compatible SQL. Generate one read-only SELECT statement. "
            "Do not use LIMIT or FETCH FIRST; use ROWNUM through the validator "
            "when row limiting is required. Use only schema-provided identifiers."
        )


_DIALECTS = {
    "bigquery": BigQueryDialect,
    "bq": BigQueryDialect,
    "duckdb": DuckDBDialect,
    "generic": GenericDialect,
    "oracle": OracleDialect,
    "oracle11g": OracleDialect,
    "postgres": PostgresDialect,
    "postgresql": PostgresDialect,
    "sqlite": SQLiteDialect,
    "sqlite3": SQLiteDialect,
    "snowflake": SnowflakeDialect,
}


def get_dialect(name: str | None) -> Dialect:
    key = (name or "generic").strip().lower()
    cls = _DIALECTS.get(key, GenericDialect)
    return cls()


def dialect_names() -> Iterable[str]:
    return sorted(_DIALECTS)
