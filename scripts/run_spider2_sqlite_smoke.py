from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from boyuesql_generic import SQLiteDialect
from boyuesql_generic.dictionary import from_sqlite_database


def read_manifest(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def gold_sql_path(spider_root: Path, instance_id: str) -> Path:
    return spider_root / "spider2-lite" / "evaluation_suite" / "gold" / "sql" / f"{instance_id}.sql"


def execute_sqlite(db_path: Path, sql: str, fetch_rows: int = 3) -> Dict[str, Any]:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(sql.strip().rstrip(";"))
        cols = [desc[0] for desc in (cur.description or [])]
        rows = cur.fetchmany(fetch_rows)
        return {"ok": True, "columns": cols, "sample_rows": rows, "error": ""}
    except Exception as exc:
        return {"ok": False, "columns": [], "sample_rows": [], "error": f"{type(exc).__name__}: {exc}"}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test generic schema/dialect handling on Spider2-Lite SQLite tasks"
    )
    parser.add_argument("--manifest", default="artifacts/spider2_manifest.csv")
    parser.add_argument("--spider-root", default=r"D:\text2sql_datasets\Spider2")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--out", default="artifacts/spider2_sqlite_smoke.json")
    args = parser.parse_args()

    manifest = read_manifest(Path(args.manifest))
    spider_root = Path(args.spider_root)
    rows = [r for r in manifest if r.get("engine") == "sqlite" and r.get("db_path")]
    dialect = SQLiteDialect()
    results: List[Dict[str, Any]] = []

    for row in rows[: args.limit]:
        db_path = Path(row["db_path"])
        schema = from_sqlite_database(db_path)
        tables = sorted(schema.table_names())
        probe_sql = dialect.limit_query(f"SELECT * FROM {dialect.quote_identifier(tables[0])}", 1) if tables else ""
        probe = execute_sqlite(db_path, probe_sql) if probe_sql else {"ok": False, "error": "no tables"}
        gold_path = gold_sql_path(spider_root, row["instance_id"])
        gold = {"ok": False, "error": "gold sql missing", "columns": [], "sample_rows": []}
        if gold_path.exists():
            gold = execute_sqlite(db_path, gold_path.read_text(encoding="utf-8"))
        results.append(
            {
                "instance_id": row["instance_id"],
                "db": row["db"],
                "db_path": str(db_path),
                "table_count": len(tables),
                "column_count": sum(len(t.columns) for t in schema.tables.values()),
                "probe_ok": bool(probe.get("ok")),
                "probe_error": probe.get("error", ""),
                "gold_sql_exists": gold_path.exists(),
                "gold_exec_ok": bool(gold.get("ok")),
                "gold_error": gold.get("error", ""),
                "gold_columns": gold.get("columns", []),
            }
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote: {out.resolve()}")
    print(f"cases: {len(results)}")
    print(f"probe_ok: {sum(1 for r in results if r['probe_ok'])}/{len(results)}")
    print(f"gold_exec_ok: {sum(1 for r in results if r['gold_exec_ok'])}/{len(results)}")
    for item in results[:3]:
        print(json.dumps(item, ensure_ascii=False)[:800])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
