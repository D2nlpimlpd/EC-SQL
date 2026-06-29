import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from boyuesql_generic.dialects import get_dialect
from boyuesql_generic.connections import (
    DatabaseConfig,
    connect_database,
    default_config_from_env,
    fetch_table_columns,
)


class DialectAdapterTests(unittest.TestCase):
    def test_limit_query_uses_rownum_for_oracle(self) -> None:
        dialect = get_dialect("oracle")
        self.assertEqual(
            dialect.limit_query("SELECT * FROM USERS;", 3),
            "SELECT * FROM (SELECT * FROM USERS) WHERE ROWNUM <= 3",
        )

    def test_limit_query_uses_limit_for_sqlite(self) -> None:
        dialect = get_dialect("sqlite")
        self.assertEqual(
            dialect.limit_query("SELECT * FROM users;", 5),
            "SELECT * FROM users LIMIT 5",
        )

    def test_limit_query_does_not_duplicate_existing_limit(self) -> None:
        dialect = get_dialect("duckdb")
        self.assertEqual(
            dialect.limit_query("SELECT * FROM users LIMIT 7", 5),
            "SELECT * FROM users LIMIT 7",
        )

    def test_date_range_condition_is_dialect_specific(self) -> None:
        oracle = get_dialect("oracle")
        sqlite = get_dialect("sqlite")
        self.assertEqual(
            oracle.date_range_condition("CREATED_AT", "2021-01-01", "2021-02-01"),
            "CREATED_AT >= TO_DATE('2021-01-01','YYYY-MM-DD') "
            "AND CREATED_AT <= TO_DATE('2021-02-01','YYYY-MM-DD')",
        )
        self.assertEqual(
            sqlite.date_range_condition("created_at", "2021-01-01", "2021-02-01"),
            "created_at >= '2021-01-01' AND created_at <= '2021-02-01'",
        )

    def test_error_classification_is_dialect_specific(self) -> None:
        self.assertEqual(
            get_dialect("oracle").classify_error("ORA-00942: table or view does not exist"),
            "MISSING_TABLE",
        )
        self.assertEqual(
            get_dialect("sqlite").classify_error("no such column: foo"),
            "INVALID_COL",
        )

    def test_bigquery_identifier_quote(self) -> None:
        self.assertEqual(get_dialect("bigquery").quote_identifier("project.dataset.table"), "`project.dataset.table`")

    def test_sqlite_connection_and_catalog_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "sample.sqlite")
            cfg = DatabaseConfig(dialect="sqlite", path=db_path)
            conn = connect_database(cfg)
            try:
                cur = conn.cursor()
                cur.execute("CREATE TABLE users (id INTEGER, name TEXT)")
                conn.commit()
            finally:
                conn.close()

            self.assertEqual(fetch_table_columns(cfg, "users"), {"ID", "NAME"})

    def test_generic_file_config_infers_sqlite_connector(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "generic.sqlite")
            cfg = DatabaseConfig(dialect="generic", path=db_path)
            conn = connect_database(cfg)
            try:
                cur = conn.cursor()
                cur.execute("CREATE TABLE inferred (id INTEGER)")
                conn.commit()
            finally:
                conn.close()
            self.assertEqual(fetch_table_columns(cfg, "inferred"), {"ID"})

    def test_config_accepts_boyuesql_dialect_alias(self) -> None:
        cfg = DatabaseConfig.from_mapping({"BOYUESQL_DIALECT": "sqlite", "DB_PATH": ":memory:"})
        self.assertEqual(cfg.dialect, "sqlite")
        self.assertEqual(cfg.path, ":memory:")

    def test_default_config_is_generic_without_oracle_opt_in(self) -> None:
        with patch.dict("os.environ", {"BOYUESQL_DIALECT": "", "DB_DIALECT": ""}, clear=False):
            cfg = default_config_from_env()
        self.assertEqual(cfg.dialect, "generic")

    def test_oracle_config_requires_explicit_dialect(self) -> None:
        cfg = DatabaseConfig.from_mapping({"BOYUESQL_DIALECT": "oracle"})
        self.assertEqual(cfg.dialect, "oracle")


if __name__ == "__main__":
    unittest.main()
