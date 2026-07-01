from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ecsql_generic import DuckDBDialect
from ecsql_generic.dictionary import from_duckdb_database


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def find_duckdb(directory: Path, preferred_name: str = "") -> Path | None:
    if preferred_name:
        preferred = directory / preferred_name
        if preferred.exists():
            return preferred
    files = sorted(directory.glob("*.duckdb"))
    return files[0] if files else None


def execute_probe(db_path: Path, table: str, col_indexes: List[int], limit: int = 3) -> Dict[str, Any]:
    try:
        import duckdb  # type: ignore
    except ModuleNotFoundError as exc:
        return {"ok": False, "error": f"duckdb missing: {exc}", "columns": [], "sample_rows": []}

    dialect = DuckDBDialect()
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        info = conn.execute(f"DESCRIBE {dialect.quote_identifier(table)}").fetchall()
        columns = [str(row[0]) for row in info]
        if col_indexes:
            selected = [
                columns[idx]
                for idx in col_indexes
                if isinstance(idx, int) and 0 <= idx < len(columns)
            ]
        else:
            selected = columns[:20]
        projection = ", ".join(dialect.quote_identifier(col) for col in selected) or "*"
        rows = conn.execute(
            f"SELECT {projection} FROM {dialect.quote_identifier(table)} LIMIT {int(limit)}"
        ).fetchall()
        return {"ok": True, "error": "", "columns": selected, "sample_rows": rows}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "columns": [], "sample_rows": []}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test Spider2-DBT DuckDB artifacts")
    parser.add_argument("--spider-root", default=r"D:\text2sql_datasets\Spider2")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--out", default="artifacts/spider2_dbt_smoke.json")
    args = parser.parse_args()

    root = Path(args.spider_root)
    dbt_root = root / "spider2-dbt"
    examples = {row["instance_id"]: row for row in read_jsonl(dbt_root / "examples" / "spider2-dbt.jsonl")}
    gold_rows = read_jsonl(dbt_root / "evaluation_suite" / "gold" / "spider2_eval.jsonl")
    if args.limit > 0:
        gold_rows = gold_rows[: args.limit]

    results: List[Dict[str, Any]] = []
    for row in gold_rows:
        instance_id = row["instance_id"]
        params = ((row.get("evaluation") or {}).get("parameters") or {})
        gold_name = str(params.get("gold") or "")
        condition_tabs = list(params.get("condition_tabs") or [])
        condition_cols = list(params.get("condition_cols") or [])
        start_db = find_duckdb(dbt_root / "examples" / instance_id, gold_name)
        gold_db = find_duckdb(dbt_root / "evaluation_suite" / "gold" / instance_id, gold_name)
        start_schema_ok = False
        gold_schema_ok = False
        start_table_count = 0
        gold_table_count = 0
        start_error = ""
        gold_error = ""
        if start_db:
            try:
                start_schema = from_duckdb_database(start_db)
                start_schema_ok = True
                start_table_count = len(start_schema.tables)
            except Exception as exc:
                start_error = f"{type(exc).__name__}: {exc}"
        else:
            start_error = "missing start duckdb"
        if gold_db:
            try:
                gold_schema = from_duckdb_database(gold_db)
                gold_schema_ok = True
                gold_table_count = len(gold_schema.tables)
            except Exception as exc:
                gold_error = f"{type(exc).__name__}: {exc}"
        else:
            gold_error = "missing gold duckdb"

        table_probes = []
        for idx, table in enumerate(condition_tabs):
            indexes = condition_cols[idx] if idx < len(condition_cols) and isinstance(condition_cols[idx], list) else []
            table_probes.append(
                {
                    "table": table,
                    "gold_probe": execute_probe(gold_db, table, indexes) if gold_db else {"ok": False, "error": "missing gold db"},
                    "start_probe": execute_probe(start_db, table, indexes) if start_db else {"ok": False, "error": "missing start db"},
                }
            )
        results.append(
            {
                "instance_id": instance_id,
                "instruction": examples.get(instance_id, {}).get("instruction", ""),
                "start_db": str(start_db) if start_db else "",
                "gold_db": str(gold_db) if gold_db else "",
                "start_schema_ok": start_schema_ok,
                "gold_schema_ok": gold_schema_ok,
                "start_table_count": start_table_count,
                "gold_table_count": gold_table_count,
                "start_error": start_error,
                "gold_error": gold_error,
                "condition_tabs": condition_tabs,
                "table_probes": table_probes,
            }
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    failures: List[Dict[str, Any]] = []
    for item in results:
        probes = item.get("table_probes") or []
        gold_probe_ok = bool(probes) and all(probe["gold_probe"].get("ok") for probe in probes)
        reasons: List[str] = []
        if not item["start_schema_ok"]:
            reasons.append("start_schema")
        if not item["gold_schema_ok"]:
            reasons.append("gold_schema")
        if not gold_probe_ok:
            reasons.append("gold_condition_probe")
        if reasons:
            failures.append(
                {
                    "instance_id": item["instance_id"],
                    "reasons": reasons,
                    "start_error": item["start_error"],
                    "gold_error": item["gold_error"],
                    "condition_tabs": item["condition_tabs"],
                    "probe_errors": [
                        {
                            "table": probe["table"],
                            "gold_error": probe["gold_probe"].get("error", ""),
                            "start_error": probe["start_probe"].get("error", ""),
                        }
                        for probe in probes
                        if (not probe["gold_probe"].get("ok")) or (not probe["start_probe"].get("ok"))
                    ],
                }
            )

    summary = {
        "cases": len(results),
        "start_schema_ok": sum(1 for item in results if item["start_schema_ok"]),
        "gold_schema_ok": sum(1 for item in results if item["gold_schema_ok"]),
        "gold_condition_probe_ok": sum(
            1
            for item in results
            if item["table_probes"] and all(probe["gold_probe"].get("ok") for probe in item["table_probes"])
        ),
        "failure_count": len(failures),
    }
    payload = {"summary": summary, "failures": failures, "results": results}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote: {out.resolve()}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if failures:
        print("failures:")
        for failure in failures:
            print(json.dumps(failure, ensure_ascii=False))
    for item in results[:3]:
        print(json.dumps({k: item[k] for k in ("instance_id", "start_table_count", "gold_table_count", "condition_tabs")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
