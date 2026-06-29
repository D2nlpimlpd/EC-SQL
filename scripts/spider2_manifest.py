from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def read_local_map(spider_root: Path) -> Dict[str, str]:
    local_map_path = (
        spider_root
        / "spider2-lite"
        / "resource"
        / "databases"
        / "spider2-localdb"
        / "local-map.jsonl"
    )
    if not local_map_path.exists():
        return {}
    try:
        first = next(read_jsonl(local_map_path))
    except StopIteration:
        return {}
    return {str(k): str(v) for k, v in first.items()}


def sqlite_db_path(spider_root: Path, instance_id: str, local_map: Mapping[str, str]) -> str:
    db_name = local_map.get(instance_id)
    if not db_name:
        return ""
    localdb = (
        spider_root
        / "spider2-lite"
        / "resource"
        / "databases"
        / "spider2-localdb"
    )
    direct = localdb / f"{db_name}.sqlite"
    if direct.exists():
        return str(direct)
    candidates = list(localdb.rglob(f"{db_name}.sqlite"))
    return str(candidates[0]) if candidates else ""


def detect_lite_engine(spider_root: Path, task: Dict[str, Any]) -> str:
    db = str(task.get("db", "")).lower()
    instance_id = str(task.get("instance_id", "")).lower()
    if instance_id.startswith("local"):
        return "sqlite"
    if instance_id.startswith("bq") or instance_id.startswith("ga"):
        return "bigquery"
    if instance_id.startswith("sf_bq"):
        return "snowflake+bigquery"
    if instance_id.startswith("sf"):
        return "snowflake"
    localdb = spider_root / "spider2-lite" / "resource" / "databases" / "spider2-localdb"
    if list(localdb.rglob(f"{db}.sqlite")) or list(localdb.rglob(f"*{db}*.sqlite")):
        return "sqlite"
    resource_root = spider_root / "spider2-lite" / "resource" / "databases"
    for engine in ("sqlite", "bigquery", "snowflake"):
        engine_root = resource_root / engine
        if engine_root.exists():
            for path in engine_root.rglob("*"):
                if path.is_dir() and path.name.lower() == db:
                    return engine
    ext = str(task.get("external_knowledge", "")).lower()
    if ext.startswith("snowflake") or "snowflake" in ext:
        return "snowflake"
    if ext.startswith("bigquery") or "bigquery" in ext or db in {"ga4", "ga360"}:
        return "bigquery"
    return "unknown"


def build_manifest(spider_root: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    local_map = read_local_map(spider_root)
    lite_path = spider_root / "spider2-lite" / "spider2-lite.jsonl"
    if lite_path.exists():
        for task in read_jsonl(lite_path):
            instance_id = str(task.get("instance_id") or "")
            engine = detect_lite_engine(spider_root, task)
            items.append(
                {
                    "setting": "spider2-lite",
                    "instance_id": instance_id,
                    "db": task.get("db"),
                    "engine": engine,
                    "db_path": sqlite_db_path(spider_root, instance_id, local_map)
                    if engine == "sqlite"
                    else "",
                    "question": task.get("question"),
                    "external_knowledge": task.get("external_knowledge", ""),
                }
            )
    snow_path = spider_root / "spider2-snow" / "spider2-snow.jsonl"
    if snow_path.exists():
        for task in read_jsonl(snow_path):
            items.append(
                {
                    "setting": "spider2-snow",
                    "instance_id": task.get("instance_id"),
                    "db": task.get("db"),
                    "engine": "snowflake",
                    "db_path": "",
                    "question": task.get("question"),
                    "external_knowledge": task.get("external_knowledge", ""),
                }
            )
    dbt_path = spider_root / "spider2-dbt" / "examples" / "spider2-dbt.jsonl"
    if dbt_path.exists():
        for task in read_jsonl(dbt_path):
            instance_id = task.get("instance_id")
            items.append(
                {
                    "setting": "spider2-dbt",
                    "instance_id": instance_id,
                    "db": instance_id,
                    "engine": "duckdb-dbt",
                    "db_path": str(spider_root / "spider2-dbt" / "examples" / str(instance_id)),
                    "question": task.get("instruction"),
                    "external_knowledge": f"spider2-dbt/examples/{instance_id}",
                }
            )
    return items


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "setting",
        "instance_id",
        "db",
        "engine",
        "db_path",
        "question",
        "external_knowledge",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a local Spider2 task manifest")
    parser.add_argument(
        "--root",
        "--spider-root",
        dest="root",
        default=r"D:\text2sql_datasets\Spider2",
        help="Path to the Spider2 checkout, or to a parent directory containing Spider2.",
    )
    parser.add_argument("--out", default="artifacts/spider2_manifest.csv")
    parser.add_argument("--sample", type=int, default=5)
    args = parser.parse_args()

    root = Path(args.root)
    if root.name.lower() != "spider2":
        root = root / "Spider2"
    if not root.exists():
        raise SystemExit(
            f"Spider2 root does not exist: {root}. "
            "Run scripts/download_spider2.sh or set --spider-root/DATASET_ROOT correctly."
        )
    rows = build_manifest(root)
    write_csv(Path(args.out), rows)

    counts: Dict[str, int] = {}
    for row in rows:
        key = f"{row['setting']}:{row['engine']}"
        counts[key] = counts.get(key, 0) + 1
    print(f"manifest: {Path(args.out).resolve()}")
    print(f"tasks: {len(rows)}")
    for key, value in sorted(counts.items()):
        print(f"{key}: {value}")
    for row in rows[: max(args.sample, 0)]:
        print(json.dumps(row, ensure_ascii=False)[:500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
