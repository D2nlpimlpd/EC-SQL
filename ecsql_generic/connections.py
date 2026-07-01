"""Database connection and catalog helpers for EC-SQL.

The production entrypoint historically imported Oracle drivers at module import
time and queried Oracle catalog views directly.  This adapter keeps those
details behind a small DB-API-like surface so benchmark and server runs can use
SQLite/DuckDB without Oracle client libraries.
"""

from __future__ import annotations

import importlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Set

from .dialects import Dialect, get_dialect


@dataclass(frozen=True)
class DatabaseConfig:
    dialect: str
    user: str = ""
    password: str = ""
    host: str = "127.0.0.1"
    port: int = 0
    service_name: str = ""
    database: str = ""
    path: str = ""

    @classmethod
    def from_mapping(cls, data: Dict[str, Any], default_dialect: str = "generic") -> "DatabaseConfig":
        def pick(*names: str, default: Any = "") -> Any:
            for name in names:
                value = data.get(name)
                if value not in (None, ""):
                    return value
            return default

        dialect = str(
            pick("DB_DIALECT", "EC_SQL_DIALECT", "dialect", "dbDialect", default=default_dialect)
        ).strip().lower()
        port_raw = pick("DB_PORT", "port", "dbPort", default=0)
        try:
            port = int(port_raw or 0)
        except (TypeError, ValueError):
            port = 0
        return cls(
            dialect=dialect or default_dialect,
            user=str(pick("DB_USER", "username", "user", "dbUser", default="")).strip(),
            password=str(pick("DB_PASSWORD", "password", "pwd", "dbPassword", default="")).strip(),
            host=str(pick("DB_HOST", "host", "dbHost", default="127.0.0.1")).strip(),
            port=port,
            service_name=str(
                pick("DB_SERVICE_NAME", "service_name", "serviceName", "dbService", default="")
            ).strip(),
            database=str(pick("DB_DATABASE", "database", "dbName", default="")).strip(),
            path=str(pick("DB_PATH", "path", "dbPath", "databasePath", default="")).strip(),
        )

    def to_session_dict(self) -> Dict[str, Any]:
        return {
            "DB_DIALECT": self.dialect,
            "DB_USER": self.user,
            "DB_PASSWORD": self.password,
            "DB_HOST": self.host,
            "DB_PORT": self.port,
            "DB_SERVICE_NAME": self.service_name,
            "DB_DATABASE": self.database,
            "DB_PATH": self.path,
        }

    def redacted(self) -> Dict[str, Any]:
        out = self.to_session_dict()
        if out.get("DB_PASSWORD"):
            out["DB_PASSWORD"] = "***"
        return out


def default_config_from_env(default_dialect: str = "generic") -> DatabaseConfig:
    return DatabaseConfig.from_mapping(
        {
            "DB_DIALECT": os.environ.get("EC_SQL_DIALECT") or os.environ.get("DB_DIALECT") or default_dialect,
            "DB_USER": os.environ.get("DB_USER", ""),
            "DB_PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "DB_HOST": os.environ.get("DB_HOST", "127.0.0.1"),
            "DB_PORT": os.environ.get("DB_PORT", ""),
            "DB_SERVICE_NAME": os.environ.get("DB_SERVICE_NAME", ""),
            "DB_DATABASE": os.environ.get("DB_DATABASE", ""),
            "DB_PATH": os.environ.get("DB_PATH", ""),
        },
        default_dialect=default_dialect,
    )


def make_oracle_dsn(cfg: DatabaseConfig) -> str:
    port = cfg.port or 1521
    return (
        f"(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST={cfg.host})(PORT={port}))"
        f"(CONNECT_DATA=(SERVICE_NAME={cfg.service_name})))"
    )


def _connect_oracle(cfg: DatabaseConfig):
    oracledb = importlib.import_module("oracledb")
    client_dir = os.environ.get("ORACLE_CLIENT_DIR", "").strip()
    if client_dir:
        try:
            oracledb.init_oracle_client(lib_dir=client_dir)
        except Exception:
            # Oracle client can only be initialized once per process.  Reusing an
            # already initialized client is fine.
            pass
    return oracledb.connect(user=cfg.user, password=cfg.password, dsn=make_oracle_dsn(cfg))


def _db_file(cfg: DatabaseConfig, default_name: str) -> str:
    value = cfg.path or cfg.database or default_name
    if value != ":memory:":
        return str(Path(value).expanduser())
    return value


def _infer_file_dialect(cfg: DatabaseConfig) -> str:
    target = (cfg.path or cfg.database or "").lower()
    suffix = Path(target).suffix if target and target != ":memory:" else ""
    if suffix in {".duckdb", ".ddb"}:
        return "duckdb"
    if suffix in {".sqlite", ".sqlite3", ".db"} or target == ":memory:":
        return "sqlite"
    return "sqlite"


def _coerce_config(config: DatabaseConfig | Dict[str, Any], default_dialect: str = "generic") -> DatabaseConfig:
    return (
        config
        if isinstance(config, DatabaseConfig)
        else DatabaseConfig.from_mapping(config, default_dialect=default_dialect)
    )


def _concrete_config(config: DatabaseConfig | Dict[str, Any], default_dialect: str = "generic") -> DatabaseConfig:
    cfg = _coerce_config(config, default_dialect=default_dialect)
    dialect_name = cfg.dialect
    if get_dialect(dialect_name).name == "generic":
        dialect_name = _infer_file_dialect(cfg)
        return DatabaseConfig(
            dialect=dialect_name,
            user=cfg.user,
            password=cfg.password,
            host=cfg.host,
            port=cfg.port,
            service_name=cfg.service_name,
            database=cfg.database,
            path=cfg.path,
        )
    return cfg


def connect_database(config: DatabaseConfig | Dict[str, Any], default_dialect: str = "generic"):
    cfg = _concrete_config(config, default_dialect=default_dialect)
    dialect = get_dialect(cfg.dialect)
    if dialect.name == "oracle":
        return _connect_oracle(cfg)
    if dialect.name == "sqlite":
        return sqlite3.connect(_db_file(cfg, ":memory:"))
    if dialect.name == "duckdb":
        duckdb = importlib.import_module("duckdb")
        return duckdb.connect(_db_file(cfg, ":memory:"))
    if dialect.name in {"postgres", "snowflake", "bigquery"}:
        raise NotImplementedError(
            f"{dialect.name} connection requires a deployment-specific connector package. "
            "Use benchmark runners or add a connector adapter for this environment."
        )
    raise NotImplementedError(f"Unsupported database dialect: {cfg.dialect}")


def fetch_table_columns(config: DatabaseConfig | Dict[str, Any], table: str, default_dialect: str = "generic") -> Optional[Set[str]]:
    table_u = (table or "").upper()
    if not table_u:
        return None
    cfg = _concrete_config(config, default_dialect=default_dialect)
    dialect: Dialect = get_dialect(cfg.dialect)
    conn = connect_database(cfg, default_dialect=default_dialect)
    try:
        cur = conn.cursor()
        if dialect.name == "oracle":
            cur.execute(
                "SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :table_name",
                {"table_name": table_u},
            )
            return {str(row[0]).upper() for row in cur.fetchall() if row and row[0]}
        if dialect.name == "sqlite":
            quoted = table.replace('"', '""')
            cur.execute(f'PRAGMA table_info("{quoted}")')
            return {str(row[1]).upper() for row in cur.fetchall() if len(row) > 1 and row[1]}
        if dialect.name == "duckdb":
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE upper(table_name) = ?",
                [table_u],
            )
            return {str(row[0]).upper() for row in cur.fetchall() if row and row[0]}
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE upper(table_name) = %s",
            (table_u,),
        )
        return {str(row[0]).upper() for row in cur.fetchall() if row and row[0]}
    finally:
        conn.close()
