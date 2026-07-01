"""Generic EC-SQL building blocks.

This package is intentionally independent from the original Oracle-specific
demo entrypoint.  It provides portable schema, dialect, dataset, and evaluation
utilities that can be reused by the production service, benchmark runners, and
server deployment scripts.
"""

from .dialects import (
    BigQueryDialect,
    Dialect,
    DuckDBDialect,
    GenericDialect,
    OracleDialect,
    PostgresDialect,
    SQLiteDialect,
    SnowflakeDialect,
    get_dialect,
)
from .dictionary import (
    ColumnDef,
    SchemaDictionary,
    TableDef,
    load_schema_dictionary,
)
from .connections import (
    DatabaseConfig,
    connect_database,
    default_config_from_env,
    fetch_table_columns,
    make_oracle_dsn,
)

__all__ = [
    "BigQueryDialect",
    "ColumnDef",
    "DatabaseConfig",
    "Dialect",
    "DuckDBDialect",
    "GenericDialect",
    "OracleDialect",
    "PostgresDialect",
    "SchemaDictionary",
    "SQLiteDialect",
    "SnowflakeDialect",
    "TableDef",
    "connect_database",
    "default_config_from_env",
    "fetch_table_columns",
    "get_dialect",
    "load_schema_dictionary",
    "make_oracle_dsn",
]
