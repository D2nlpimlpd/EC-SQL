from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_ROOT = Path(r"D:\text2sql_datasets\Spider2")
DEFAULT_MANIFEST = PROJECT_ROOT / "artifacts" / "spider2_manifest.csv"
DEFAULT_JSON = PROJECT_ROOT / "artifacts" / "dataset_scale_report.json"
DEFAULT_MD = PROJECT_ROOT / "artifacts" / "dataset_scale_report.md"

MIN_MANIFEST_ROWS = 600
MIN_UNIQUE_INSTANCES = 600
MIN_SQLITE_ROWS = 100
MIN_DBT_ROWS = 68


@dataclass(frozen=True)
class DatasetScaleReport:
    dataset_root: str
    manifest: str
    dataset_root_exists: bool
    manifest_exists: bool
    manifest_rows: int
    unique_instances: int
    unique_logical_dbs: int
    unique_engines: int
    setting_engine_counts: dict[str, int]
    sqlite_rows: int
    sqlite_rows_with_existing_db_path: int
    dbt_rows: int
    snowflake_rows: int
    bigquery_rows: int
    threshold_rows: int
    threshold_unique_instances: int
    threshold_sqlite_rows: int
    threshold_dbt_rows: int
    status: str
    evidence: str


def _inc(mapping: dict[str, int], key: str) -> None:
    mapping[key] = mapping.get(key, 0) + 1


def _sorted_counts(mapping: dict[str, int]) -> dict[str, int]:
    return {key: mapping[key] for key in sorted(mapping)}


def build_report(dataset_root: Path, manifest: Path) -> DatasetScaleReport:
    setting_engine_counts: dict[str, int] = {}
    instances: set[str] = set()
    dbs: set[str] = set()
    engines: set[str] = set()
    rows = 0
    sqlite_rows = 0
    sqlite_existing = 0
    dbt_rows = 0
    snowflake_rows = 0
    bigquery_rows = 0

    if manifest.exists():
        with manifest.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                rows += 1
                setting = str(row.get("setting") or "unknown")
                engine = str(row.get("engine") or "unknown")
                instance_id = str(row.get("instance_id") or "").strip()
                db = str(row.get("db") or "").strip()
                db_path = str(row.get("db_path") or "").strip()

                _inc(setting_engine_counts, f"{setting}:{engine}")
                if instance_id:
                    instances.add(instance_id)
                if db:
                    dbs.add(db)
                if engine:
                    engines.add(engine)
                if engine == "sqlite":
                    sqlite_rows += 1
                    if db_path and Path(db_path).exists():
                        sqlite_existing += 1
                if engine == "duckdb-dbt":
                    dbt_rows += 1
                if "snowflake" in engine:
                    snowflake_rows += 1
                if "bigquery" in engine:
                    bigquery_rows += 1

    passes = (
        dataset_root.exists()
        and manifest.exists()
        and rows >= MIN_MANIFEST_ROWS
        and len(instances) >= MIN_UNIQUE_INSTANCES
        and sqlite_rows >= MIN_SQLITE_ROWS
        and dbt_rows >= MIN_DBT_ROWS
    )
    status = "PASS" if passes else "FAIL"
    evidence = (
        f"{rows} manifest rows; {len(instances)} unique instances/projects; "
        f"{len(dbs)} logical DB names; {len(engines)} engines; "
        f"sqlite={sqlite_rows} ({sqlite_existing} with local DB files); "
        f"dbt={dbt_rows}; snowflake={snowflake_rows}; bigquery={bigquery_rows}"
    )
    return DatasetScaleReport(
        dataset_root=str(dataset_root),
        manifest=str(manifest),
        dataset_root_exists=dataset_root.exists(),
        manifest_exists=manifest.exists(),
        manifest_rows=rows,
        unique_instances=len(instances),
        unique_logical_dbs=len(dbs),
        unique_engines=len(engines),
        setting_engine_counts=_sorted_counts(setting_engine_counts),
        sqlite_rows=sqlite_rows,
        sqlite_rows_with_existing_db_path=sqlite_existing,
        dbt_rows=dbt_rows,
        snowflake_rows=snowflake_rows,
        bigquery_rows=bigquery_rows,
        threshold_rows=MIN_MANIFEST_ROWS,
        threshold_unique_instances=MIN_UNIQUE_INSTANCES,
        threshold_sqlite_rows=MIN_SQLITE_ROWS,
        threshold_dbt_rows=MIN_DBT_ROWS,
        status=status,
        evidence=evidence,
    )


def to_markdown(report: DatasetScaleReport) -> str:
    counts_rows = "\n".join(
        f"| `{key}` | {value} |" for key, value in report.setting_engine_counts.items()
    )
    if not counts_rows:
        counts_rows = "| none | 0 |"
    return "\n".join(
        [
            "# Spider2 Dataset Scale Report",
            "",
            f"- Status: **{report.status}**",
            f"- Dataset root: `{report.dataset_root}`",
            f"- Manifest: `{report.manifest}`",
            f"- Evidence: {report.evidence}",
            "",
            "## Thresholds",
            "",
            "| Requirement | Observed | Threshold |",
            "| --- | ---: | ---: |",
            f"| Manifest rows | {report.manifest_rows} | {report.threshold_rows} |",
            f"| Unique instances/projects | {report.unique_instances} | {report.threshold_unique_instances} |",
            f"| SQLite rows | {report.sqlite_rows} | {report.threshold_sqlite_rows} |",
            f"| DBT rows | {report.dbt_rows} | {report.threshold_dbt_rows} |",
            "",
            "## Setting/Engine Coverage",
            "",
            "| Setting/engine | Rows |",
            "| --- | ---: |",
            counts_rows,
            "",
            "This report is generated directly from the local Spider2 manifest. "
            "It documents the broad benchmark scale used for EC-SQL generalization "
            "and is separate from execution/SER evidence, which is produced by the "
            "benchmark and server-result reports.",
            "",
        ]
    )


def write_report(report: DatasetScaleReport, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = asdict(report)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(to_markdown(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Spider2 dataset scale/evidence report.")
    parser.add_argument("--dataset-root", default=str(DEFAULT_DATASET_ROOT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--json-out", default=str(DEFAULT_JSON))
    parser.add_argument("--md-out", default=str(DEFAULT_MD))
    parser.add_argument("--strict", action="store_true", help="Return non-zero when thresholds fail.")
    args = parser.parse_args()

    report = build_report(Path(args.dataset_root), Path(args.manifest))
    write_report(report, Path(args.json_out), Path(args.md_out))
    print(f"[dataset-scale] {report.status}: {report.evidence}")
    print(f"[dataset-scale] json: {Path(args.json_out).resolve()}")
    print(f"[dataset-scale] markdown: {Path(args.md_out).resolve()}")
    return 0 if report.status == "PASS" or not args.strict else 2


if __name__ == "__main__":
    raise SystemExit(main())
