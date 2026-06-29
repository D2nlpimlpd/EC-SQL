from __future__ import annotations

import argparse
import csv
import json
import os
import re
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_SQLITE_SYSTEMS = "boyuesql,no_semantic_templates,no_external_knowledge,no_schema_retrieval,no_repair"
DEFAULT_SQLITE_BASELINES = "direct,din_sql_style,dail_sql_style,self_debug_style,mac_sql_style,chess_style"
DBT_ABLATION_LABELS = [
    "boyuesql_ablation_no_declared_model_synthesis",
    "boyuesql_ablation_no_duckdb_type_repair",
    "boyuesql_ablation_no_missing_ref_source_fallback",
    "boyuesql_ablation_no_declared_column_completion",
    "boyuesql_ablation_no_related_dimension_enrichment",
    "boyuesql_ablation_no_fact_pivot_synthesis",
    "boyuesql_ablation_no_final_failed_model_placeholder",
]


def split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def flag(name: str, default: str) -> bool:
    return str(os.environ.get(name, default)).strip() == "1"


def value(name: str, default: str) -> str:
    return os.environ.get(name, default)


def slugify(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text)


def full_profile_config(run_id: str, out_dir: Path) -> dict[str, Any]:
    boyuesql_models = value("BOYUESQL_MODELS", value("BOYUESQL_MODEL", "qwen3-vl:8b"))
    baseline_models = value("BASELINE_MODELS", value("BASELINE_MODEL", "qwen2.5-coder:7b") + ",sqlcoder:7b,qwen3:32b")
    dbt_edit_models = value("DBT_EDIT_MODELS", boyuesql_models)
    return {
        "RUN_ID": run_id,
        "OUT_DIR": str(out_dir),
        "RUN_SMOKE": value("RUN_SMOKE", "1"),
        "RUN_SQLITE_SCHEMA_ONLY": value("RUN_SQLITE_SCHEMA_ONLY", "1"),
        "RUN_SQLITE_LLM": value("RUN_SQLITE_LLM", "1"),
        "RUN_DBT_BASELINE": value("RUN_DBT_BASELINE", "1"),
        "RUN_DBT_BOYUESQL": value("RUN_DBT_BOYUESQL", "1"),
        "RUN_DBT_ABLATIONS": value("RUN_DBT_ABLATIONS", "1"),
        "RUN_DBT_LLM": value("RUN_DBT_LLM", "0"),
        "SQLITE_SMOKE_LIMIT": value("SQLITE_SMOKE_LIMIT", "135"),
        "DBT_SMOKE_LIMIT": value("DBT_SMOKE_LIMIT", "68"),
        "SQLITE_SCHEMA_ONLY_LIMIT": value("SQLITE_SCHEMA_ONLY_LIMIT", "135"),
        "SQLITE_LLM_LIMIT": value("SQLITE_LLM_LIMIT", "135"),
        "SQLITE_GOLD_CASE_LIMIT": value("SQLITE_GOLD_CASE_LIMIT", "0"),
        "SQLITE_GOLD_CASE_OFFSET": value("SQLITE_GOLD_CASE_OFFSET", "0"),
        "DBT_BASELINE_LIMIT": value("DBT_BASELINE_LIMIT", "68"),
        "DBT_BOYUESQL_LIMIT": value("DBT_BOYUESQL_LIMIT", "68"),
        "DBT_ABLATION_LIMIT": value("DBT_ABLATION_LIMIT", "68"),
        "DBT_LLM_LIMIT": value("DBT_LLM_LIMIT", "68"),
        "BOYUESQL_MODELS": boyuesql_models,
        "BASELINE_MODELS": baseline_models,
        "DBT_EDIT_MODELS": dbt_edit_models,
        "SQLITE_SYSTEMS": value("SQLITE_SYSTEMS", DEFAULT_SQLITE_SYSTEMS),
        "SQLITE_BASELINE_SYSTEMS": value("SQLITE_BASELINE_SYSTEMS", DEFAULT_SQLITE_BASELINES),
    }


def artifact_row(stage: str, artifact: str, suite: str, systems: str, models: str, cases: str, enabled: bool) -> dict[str, str]:
    return {
        "enabled": "1" if enabled else "0",
        "stage": stage,
        "artifact": artifact,
        "suite": suite,
        "systems": systems,
        "models": models,
        "expected_cases": cases,
    }


def expected_artifacts(config: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    run_smoke = config["RUN_SMOKE"] == "1"
    rows.append(
        artifact_row(
            "sqlite_smoke",
            "spider2_sqlite_smoke.json",
            "spider2-sqlite-smoke",
            "smoke",
            "",
            config["SQLITE_SMOKE_LIMIT"],
            run_smoke,
        )
    )
    rows.append(
        artifact_row(
            "dbt_smoke",
            "spider2_dbt_smoke.json",
            "spider2-dbt-smoke",
            "smoke",
            "",
            config["DBT_SMOKE_LIMIT"],
            run_smoke,
        )
    )
    rows.append(
        artifact_row(
            "sqlite_schema_only",
            "spider2_sqlite_schema_only.json",
            "spider2-sqlite",
            "schema_only",
            "",
            config["SQLITE_SCHEMA_ONLY_LIMIT"],
            config["RUN_SQLITE_SCHEMA_ONLY"] == "1",
        )
    )
    if config["RUN_SQLITE_LLM"] == "1":
        for model in split_csv(config["BOYUESQL_MODELS"]):
            rows.append(
                artifact_row(
                    "sqlite_boyuesql_ablations",
                    f"spider2_sqlite_boyuesql_ablation_{slugify(model)}.json",
                    "spider2-sqlite",
                    config["SQLITE_SYSTEMS"],
                    model,
                    config["SQLITE_LLM_LIMIT"],
                    True,
                )
            )
        for model in split_csv(config["BASELINE_MODELS"]):
            rows.append(
                artifact_row(
                    "sqlite_sota_baselines",
                    f"spider2_sqlite_sota_baselines_{slugify(model)}.json",
                    "spider2-sqlite",
                    config["SQLITE_BASELINE_SYSTEMS"],
                    model,
                    config["SQLITE_LLM_LIMIT"],
                    True,
                )
            )
    rows.append(
        artifact_row(
            "dbt_starter_baseline",
            "spider2_dbt_existing_project.json",
            "spider2-dbt",
            "existing_project",
            "",
            config["DBT_BASELINE_LIMIT"],
            config["RUN_DBT_BASELINE"] == "1",
        )
    )
    rows.append(
        artifact_row(
            "dbt_boyuesql_full",
            "spider2_dbt_llm_edit_boyuesql_deterministic_full.json",
            "spider2-dbt",
            "boyuesql_deterministic_full",
            "deterministic",
            config["DBT_BOYUESQL_LIMIT"],
            config["RUN_DBT_BOYUESQL"] == "1",
        )
    )
    if config["RUN_DBT_ABLATIONS"] == "1":
        for label in DBT_ABLATION_LABELS:
            rows.append(
                artifact_row(
                    "dbt_ablation",
                    f"spider2_dbt_llm_edit_{label}.json",
                    "spider2-dbt",
                    label,
                    "deterministic",
                    config["DBT_ABLATION_LIMIT"],
                    True,
                )
            )
    if config["RUN_DBT_LLM"] == "1":
        for model in split_csv(config["DBT_EDIT_MODELS"]):
            rows.append(
                artifact_row(
                    "dbt_llm_edit",
                    f"spider2_dbt_llm_edit_{slugify(model)}.json",
                    "spider2-dbt-edit",
                    "dbt_llm_edit",
                    model,
                    config["DBT_LLM_LIMIT"],
                    True,
                )
            )
    return rows


def int_value(raw: Any, default: int = 0) -> int:
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return default


def coverage_summary(config: dict[str, Any], rows: list[dict[str, str]]) -> dict[str, Any]:
    enabled = [row for row in rows if row["enabled"] == "1"]
    sqlite_systems = split_csv(config["SQLITE_SYSTEMS"]) if config["RUN_SQLITE_LLM"] == "1" else []
    sqlite_baseline_systems = split_csv(config["SQLITE_BASELINE_SYSTEMS"]) if config["RUN_SQLITE_LLM"] == "1" else []
    baseline_models = split_csv(config["BASELINE_MODELS"]) if config["RUN_SQLITE_LLM"] == "1" else []
    dbt_ablations = DBT_ABLATION_LABELS if config["RUN_DBT_ABLATIONS"] == "1" else []
    checks = [
        {
            "requirement": "SQLite BoyueSQL full row with >=20 gold cases",
            "value": int("boyuesql" in sqlite_systems and int_value(config["SQLITE_LLM_LIMIT"]) >= 20),
            "threshold": 1,
        },
        {
            "requirement": "SQLite ablation systems",
            "value": sum(1 for system in sqlite_systems if system.startswith("no_")),
            "threshold": 3,
        },
        {
            "requirement": "SOTA-style SQLite baseline systems",
            "value": len(sqlite_baseline_systems),
            "threshold": 4,
        },
        {
            "requirement": "SOTA baseline models",
            "value": len(baseline_models),
            "threshold": 3,
        },
        {
            "requirement": "DBT starter-project baseline with 68 cases",
            "value": int(config["RUN_DBT_BASELINE"] == "1" and int_value(config["DBT_BASELINE_LIMIT"]) >= 68),
            "threshold": 1,
        },
        {
            "requirement": "DBT BoyueSQL deterministic full with 68 cases",
            "value": int(config["RUN_DBT_BOYUESQL"] == "1" and int_value(config["DBT_BOYUESQL_LIMIT"]) >= 68),
            "threshold": 1,
        },
        {
            "requirement": "DBT ablation systems with 68 cases",
            "value": len(dbt_ablations) if int_value(config["DBT_ABLATION_LIMIT"]) >= 68 else 0,
            "threshold": 5,
        },
    ]
    for check in checks:
        check["status"] = "PASS" if int(check["value"]) >= int(check["threshold"]) else "FAIL"
    return {
        "run_id": config["RUN_ID"],
        "enabled_artifact_count": len(enabled),
        "sqlite_case_limit": int_value(config["SQLITE_LLM_LIMIT"]),
        "dbt_case_limit": int_value(config["DBT_BOYUESQL_LIMIT"]),
        "sqlite_systems": sqlite_systems,
        "sqlite_baseline_systems": sqlite_baseline_systems,
        "baseline_models": baseline_models,
        "dbt_ablation_systems": dbt_ablations,
        "checks": checks,
        "status": "PASS" if all(check["status"] == "PASS" for check in checks) else "FAIL",
    }


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = ["enabled", "stage", "artifact", "suite", "systems", "models", "expected_cases"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def markdown(config: dict[str, Any], rows: list[dict[str, str]]) -> str:
    enabled = [row for row in rows if row["enabled"] == "1"]
    coverage = coverage_summary(config, rows)
    lines = [
        f"# Server Matrix Plan: {config['RUN_ID']}",
        "",
        "## Commands",
        "",
        "```bash",
        "bash scripts/one_click_linux.sh models",
        f"RUN_ID={config['RUN_ID']} bash scripts/one_click_linux.sh launch",
        f"RUN_ID={config['RUN_ID']} bash scripts/one_click_linux.sh status",
        f"RUN_ID={config['RUN_ID']} bash scripts/one_click_linux.sh validate",
        f"RUN_ID={config['RUN_ID']} bash scripts/one_click_linux.sh evidence",
        f"AUDIT_STRICT=1 SERVER_RUN_ID={config['RUN_ID']} bash scripts/one_click_linux.sh audit",
        "```",
        "",
        "## Expected JSON Artifacts",
        "",
        "| Enabled | Stage | Artifact | Suite | Systems | Models | Expected Cases |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                escape_md(row[field])
                for field in ["enabled", "stage", "artifact", "suite", "systems", "models", "expected_cases"]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            f"Enabled artifact count: {len(enabled)}",
            "",
            "## Coverage Gate Preview",
            "",
            f"Overall preview status: **{coverage['status']}**",
            "",
            "| Requirement | Planned Value | Threshold | Status |",
            "| --- | ---: | ---: | --- |",
        ]
    )
    for check in coverage["checks"]:
        lines.append(
            f"| {escape_md(check['requirement'])} | {check['value']} | {check['threshold']} | {check['status']} |"
        )
    lines.extend(
        [
            "",
            "Run `RUN_ID=<id> bash scripts/one_click_linux.sh validate` after completion; it must pass before the goal can be claimed complete.",
        ]
    )
    return "\n".join(lines) + "\n"


def escape_md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def coverage_markdown(coverage: dict[str, Any]) -> str:
    lines = [
        f"# Server Matrix Coverage Preview: {coverage['run_id']}",
        "",
        f"Overall preview status: **{coverage['status']}**",
        "",
        "| Requirement | Planned Value | Threshold | Status |",
        "| --- | ---: | ---: | --- |",
    ]
    for check in coverage["checks"]:
        lines.append(
            f"| {escape_md(check['requirement'])} | {check['value']} | {check['threshold']} | {check['status']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan the full BoyueSQL Spider2 server experiment matrix.")
    parser.add_argument("--run-id", default=os.environ.get("RUN_ID", "server_full_spider2"))
    parser.add_argument("--out-dir", default=os.environ.get("OUT_DIR", ""))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else PROJECT_ROOT / "artifacts" / "server_runs" / args.run_id
    config = full_profile_config(args.run_id, out_dir)
    rows = expected_artifacts(config)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_csv = out_dir / "expected_artifacts.csv"
    plan_md = out_dir / "server_matrix_plan.md"
    plan_json = out_dir / "server_matrix_plan.json"
    coverage_json = out_dir / "server_matrix_coverage.json"
    coverage_md = out_dir / "server_matrix_coverage.md"
    coverage = coverage_summary(config, rows)
    write_csv(plan_csv, rows)
    plan_md.write_text(markdown(config, rows), encoding="utf-8")
    plan_json.write_text(
        json.dumps({"config": config, "coverage": coverage, "artifacts": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    coverage_json.write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    coverage_md.write_text(coverage_markdown(coverage), encoding="utf-8")
    if args.json:
        print(plan_json.read_text(encoding="utf-8"))
    else:
        print(f"[server-plan] csv: {plan_csv}")
        print(f"[server-plan] markdown: {plan_md}")
        print(f"[server-plan] json: {plan_json}")
        print(f"[server-plan] coverage: {coverage_json}")
        print(f"[server-plan] enabled artifacts: {sum(1 for row in rows if row['enabled'] == '1')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
