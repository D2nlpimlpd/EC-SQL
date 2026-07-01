"""Portable schema dictionary utilities.

This module defines a normalized representation and loaders that work for JSON
dictionaries, SQLite databases, and Spider-style table metadata. Retrieval code
can consume this representation without knowing the source database.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


@dataclass(frozen=True)
class ColumnDef:
    name: str
    data_type: str = ""
    description: str = ""
    label: str = ""
    nullable: Optional[bool] = None
    primary_key: bool = False

    @property
    def normalized_name(self) -> str:
        return self.name.upper()


@dataclass(frozen=True)
class TableDef:
    name: str
    columns: List[ColumnDef] = field(default_factory=list)
    description: str = ""
    label: str = ""
    table_type: str = "table"

    @property
    def normalized_name(self) -> str:
        return self.name.upper()

    def column_names(self) -> set[str]:
        return {col.normalized_name for col in self.columns}


@dataclass(frozen=True)
class SchemaDictionary:
    tables: Dict[str, TableDef]
    dialect: str = "generic"
    source: str = ""
    relationships: List[Dict[str, str]] = field(default_factory=list)

    def table_names(self) -> set[str]:
        return set(self.tables)

    def get_table(self, name: str) -> Optional[TableDef]:
        return self.tables.get(name.upper())

    def to_raganything_dict(self) -> Dict[str, Any]:
        main_tables: Dict[str, Any] = {}
        code_tables: Dict[str, Any] = {}
        for table in self.tables.values():
            target = code_tables if table.table_type == "code" else main_tables
            target[table.normalized_name] = {
                "table_name": table.normalized_name,
                "table_cn": table.label,
                "table_type": table.table_type,
                "is_code_table": table.table_type == "code",
                "short_description": table.description,
                "columns": [
                    {
                        "name": col.normalized_name,
                        "cn": col.label,
                        "usage": col.description,
                        "data_type": col.data_type,
                        "type_str": col.data_type,
                        "full_description": col.description,
                        "primary_key": col.primary_key,
                        "nullable": col.nullable,
                    }
                    for col in table.columns
                ],
            }
        return {
            "main_tables": main_tables,
            "code_tables": code_tables,
            "relationships": self.relationships,
            "metadata": {
                "dialect": self.dialect,
                "source": self.source,
                "main_table_count": len(main_tables),
                "code_table_count": len(code_tables),
                "total_count": len(main_tables) + len(code_tables),
            },
        }


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _column_from_mapping(raw: Mapping[str, Any]) -> ColumnDef:
    name = _clean(raw.get("name") or raw.get("column_name") or raw.get("column"))
    return ColumnDef(
        name=name,
        data_type=_clean(raw.get("data_type") or raw.get("type") or raw.get("type_str")),
        description=_clean(raw.get("description") or raw.get("usage") or raw.get("full_description")),
        label=_clean(raw.get("label") or raw.get("cn") or raw.get("column_cn")),
        nullable=raw.get("nullable") if isinstance(raw.get("nullable"), bool) else None,
        primary_key=bool(raw.get("primary_key") or raw.get("pk")),
    )


def _table_from_mapping(name: str, raw: Mapping[str, Any], default_type: str) -> TableDef:
    columns_raw = raw.get("columns", [])
    if isinstance(columns_raw, Mapping):
        columns_iter: Iterable[Any] = columns_raw.values()
    else:
        columns_iter = columns_raw or []
    columns = [_column_from_mapping(col) for col in columns_iter if isinstance(col, Mapping)]
    columns = [col for col in columns if col.name]
    table_type = "code" if raw.get("is_code_table") else default_type
    return TableDef(
        name=_clean(raw.get("table_name") or name),
        columns=columns,
        description=_clean(raw.get("description") or raw.get("short_description") or raw.get("detail_description")),
        label=_clean(raw.get("label") or raw.get("table_cn")),
        table_type=table_type,
    )


def load_json_dictionary(path: str | Path, dialect: str = "generic") -> SchemaDictionary:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    tables: Dict[str, TableDef] = {}

    if "tables" in payload and isinstance(payload["tables"], list):
        for item in payload["tables"]:
            if isinstance(item, Mapping):
                table = _table_from_mapping(_clean(item.get("name") or item.get("table_name")), item, "table")
                if table.name:
                    tables[table.normalized_name] = table
    else:
        for section, table_type in (("main_tables", "table"), ("code_tables", "code")):
            for name, raw in (payload.get(section, {}) or {}).items():
                if isinstance(raw, Mapping):
                    table = _table_from_mapping(name, raw, table_type)
                    if table.name:
                        tables[table.normalized_name] = table

    relationships = payload.get("relationships", [])
    if not isinstance(relationships, list):
        relationships = []
    meta = payload.get("metadata", {}) if isinstance(payload.get("metadata"), Mapping) else {}
    return SchemaDictionary(
        tables=tables,
        dialect=_clean(meta.get("dialect") or dialect or "generic"),
        source=str(source),
        relationships=[r for r in relationships if isinstance(r, dict)],
    )


def from_sqlite_database(path: str | Path) -> SchemaDictionary:
    db_path = Path(path)
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name, type FROM sqlite_master "
            "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
        tables: Dict[str, TableDef] = {}
        relationships: List[Dict[str, str]] = []
        for table_name, table_type in rows:
            cols = []
            try:
                table_info_rows = list(
                    conn.execute(f"PRAGMA table_info({quote_sqlite_ident(table_name)})")
                )
            except sqlite3.Error:
                table_info_rows = []
            for cid, name, dtype, notnull, _default, pk in table_info_rows:
                cols.append(
                    ColumnDef(
                        name=name,
                        data_type=dtype or "",
                        nullable=not bool(notnull),
                        primary_key=bool(pk),
                    )
                )
            try:
                fk_rows = list(
                    conn.execute(
                        f"PRAGMA foreign_key_list({quote_sqlite_ident(table_name)})"
                    )
                )
            except sqlite3.Error:
                fk_rows = []
            for fk in fk_rows:
                relationships.append(
                    {
                        "left": table_name.upper(),
                        "left_column": str(fk[3]).upper(),
                        "right": str(fk[2]).upper(),
                        "right_column": str(fk[4]).upper(),
                        "type": "foreign_key",
                    }
                )
            table = TableDef(
                name=table_name,
                columns=cols,
                table_type="view" if table_type == "view" else "table",
            )
            tables[table.normalized_name] = table
        return SchemaDictionary(
            tables=tables,
            dialect="sqlite",
            source=str(db_path),
            relationships=relationships,
        )
    finally:
        conn.close()


def from_duckdb_database(path: str | Path) -> SchemaDictionary:
    db_path = Path(path)
    try:
        import duckdb  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("duckdb package is required to load DuckDB schemas") from exc

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        table_rows = conn.execute(
            """
            SELECT table_name, table_type
            FROM information_schema.tables
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
            ORDER BY table_name
            """
        ).fetchall()
        tables: Dict[str, TableDef] = {}
        for table_name, table_type in table_rows:
            col_rows = conn.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = ?
                ORDER BY ordinal_position
                """,
                [table_name],
            ).fetchall()
            cols = [
                ColumnDef(
                    name=str(name),
                    data_type=str(dtype or ""),
                    nullable=str(nullable).upper() == "YES",
                )
                for name, dtype, nullable in col_rows
            ]
            table = TableDef(
                name=str(table_name),
                columns=cols,
                table_type="view" if str(table_type).upper() == "VIEW" else "table",
            )
            tables[table.normalized_name] = table
        return SchemaDictionary(tables=tables, dialect="duckdb", source=str(db_path))
    finally:
        conn.close()


def quote_sqlite_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def load_schema_dictionary(path: str | Path, dialect: str = "generic") -> SchemaDictionary:
    source = Path(path)
    if source.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
        return from_sqlite_database(source)
    if source.suffix.lower() == ".duckdb":
        return from_duckdb_database(source)
    if source.suffix.lower() == ".json":
        return load_json_dictionary(source, dialect=dialect)
    raise ValueError(f"Unsupported schema dictionary source: {source}")
