from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.audit_goal_readiness import check_server_matrix
from scripts.build_server_result_bundle import build_bundle
from scripts.finalize_server_result import finalize_server_result
from scripts.import_server_result_bundle import import_bundle
from scripts.verify_server_result_bundle import verify_bundle


SUMMARY_FIELDS = [
    "artifact",
    "suite",
    "system",
    "model",
    "cases",
    "ER",
    "RE",
    "SER",
    "implementation_type",
    "official_reproduction",
]


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_json_artifact(path: Path, suite: str, system: str, cases: int) -> None:
    payload = {
        "suite": suite,
        "system": system,
        "summary": {system: {"cases": cases, "ER": 100.0, "RE": 100.0, "SER": 100.0}},
        "results": [],
        "smoke_only": True,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_manifest(path: Path, dataset_root: Path) -> None:
    dataset_root.mkdir(parents=True, exist_ok=True)
    fields = ["setting", "instance_id", "db", "engine", "db_path", "question", "external_knowledge"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for i in range(100):
            writer.writerow(
                {
                    "setting": "spider2-lite",
                    "instance_id": f"sqlite_{i:04d}",
                    "db": f"sqlite_db_{i:04d}",
                    "engine": "sqlite",
                    "db_path": "",
                    "question": "synthetic sqlite question",
                    "external_knowledge": "",
                }
            )
        for i in range(68):
            writer.writerow(
                {
                    "setting": "spider2-dbt",
                    "instance_id": f"dbt_{i:04d}",
                    "db": f"dbt_project_{i:04d}",
                    "engine": "duckdb-dbt",
                    "db_path": "",
                    "question": "synthetic dbt question",
                    "external_knowledge": "",
                }
            )
        for i in range(500):
            writer.writerow(
                {
                    "setting": "spider2-snow",
                    "instance_id": f"snow_{i:04d}",
                    "db": f"snow_db_{i:04d}",
                    "engine": "snowflake",
                    "db_path": "",
                    "question": "synthetic snowflake question",
                    "external_knowledge": "",
                }
            )


def summary_rows() -> list[dict[str, object]]:
    rows: list[tuple[str, str, str, str, int]] = [
        ("spider2_sqlite_ecsql_ablation_qwen3-vl_8b.json", "spider2-sqlite", "ecsql", "qwen3-vl:8b", 24),
        ("spider2_sqlite_ecsql_ablation_qwen3-vl_8b.json", "spider2-sqlite", "no_semantic_templates", "qwen3-vl:8b", 24),
        ("spider2_sqlite_ecsql_ablation_qwen3-vl_8b.json", "spider2-sqlite", "no_external_knowledge", "qwen3-vl:8b", 24),
        ("spider2_sqlite_ecsql_ablation_qwen3-vl_8b.json", "spider2-sqlite", "no_schema_retrieval", "qwen3-vl:8b", 24),
        ("spider2_sqlite_sota_baselines_qwen2.5-coder_7b.json", "spider2-sqlite", "direct", "qwen2.5-coder:7b", 24),
        ("spider2_sqlite_sota_baselines_qwen2.5-coder_7b.json", "spider2-sqlite", "din_sql_style", "qwen2.5-coder:7b", 24),
        ("spider2_sqlite_sota_baselines_qwen2.5-coder_7b.json", "spider2-sqlite", "dail_sql_style", "qwen2.5-coder:7b", 24),
        ("spider2_sqlite_sota_baselines_qwen2.5-coder_7b.json", "spider2-sqlite", "self_debug_style", "qwen2.5-coder:7b", 24),
        ("spider2_sqlite_sota_baselines_sqlcoder_7b.json", "spider2-sqlite", "mac_sql_style", "sqlcoder:7b", 24),
        ("spider2_sqlite_sota_baselines_sqlcoder_7b.json", "spider2-sqlite", "chess_style", "sqlcoder:7b", 24),
        ("spider2_sqlite_sota_baselines_qwen3_32b.json", "spider2-sqlite", "direct", "qwen3:32b", 24),
        ("spider2_dbt_existing_project.json", "spider2-dbt", "existing_project", "", 68),
        ("spider2_dbt_llm_edit_ecsql_deterministic_full.json", "spider2-dbt", "ecsql_deterministic_full", "", 68),
        (
            "spider2_dbt_llm_edit_ecsql_ablation_no_declared_model_synthesis.json",
            "spider2-dbt",
            "ecsql_ablation_no_declared_model_synthesis",
            "",
            68,
        ),
        (
            "spider2_dbt_llm_edit_ecsql_ablation_no_duckdb_type_repair.json",
            "spider2-dbt",
            "ecsql_ablation_no_duckdb_type_repair",
            "",
            68,
        ),
        (
            "spider2_dbt_llm_edit_ecsql_ablation_no_missing_ref_source_fallback.json",
            "spider2-dbt",
            "ecsql_ablation_no_missing_ref_source_fallback",
            "",
            68,
        ),
        (
            "spider2_dbt_llm_edit_ecsql_ablation_no_declared_column_completion.json",
            "spider2-dbt",
            "ecsql_ablation_no_declared_column_completion",
            "",
            68,
        ),
        (
            "spider2_dbt_llm_edit_ecsql_ablation_no_related_dimension_enrichment.json",
            "spider2-dbt",
            "ecsql_ablation_no_related_dimension_enrichment",
            "",
            68,
        ),
    ]
    return [
        {
            "artifact": artifact,
            "suite": suite,
            "system": system,
            "model": model,
            "cases": cases,
            "ER": 100.0,
            "RE": 100.0,
            "SER": 100.0,
            "implementation_type": "",
            "official_reproduction": "",
        }
        for artifact, suite, system, model, cases in rows
    ]


def write_complete_synthetic_run(run_dir: Path, run_id: str) -> None:
    summary_dir = run_dir / "summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    rows = summary_rows()

    artifacts = sorted({str(row["artifact"]) for row in rows})
    for artifact in artifacts:
        first = next(row for row in rows if row["artifact"] == artifact)
        write_json_artifact(run_dir / artifact, str(first["suite"]), str(first["system"]), int(first["cases"]))

    write_json_artifact(run_dir / "spider2_sqlite_smoke.json", "spider2-sqlite-smoke", "smoke", 135)
    write_json_artifact(run_dir / "spider2_dbt_smoke.json", "spider2-dbt-smoke", "smoke", 68)
    write_json_artifact(run_dir / "spider2_sqlite_schema_only.json", "spider2-sqlite", "schema_only", 135)

    expected_rows = [
        {"enabled": "1", "stage": "smoke", "artifact": "spider2_sqlite_smoke.json", "suite": "spider2-sqlite-smoke"},
        {"enabled": "1", "stage": "smoke", "artifact": "spider2_dbt_smoke.json", "suite": "spider2-dbt-smoke"},
        {"enabled": "1", "stage": "schema_only", "artifact": "spider2_sqlite_schema_only.json", "suite": "spider2-sqlite"},
    ]
    expected_rows.extend(
        {"enabled": "1", "stage": "summary", "artifact": artifact, "suite": "server-matrix"} for artifact in artifacts
    )
    write_csv(run_dir / "expected_artifacts.csv", expected_rows, ["enabled", "stage", "artifact", "suite"])
    write_csv(summary_dir / f"server_{run_id}.csv", rows, SUMMARY_FIELDS)
    write_csv(summary_dir / f"server_{run_id}_cases.csv", rows, SUMMARY_FIELDS)

    (run_dir / "server_matrix_plan.md").write_text("# Synthetic Server Matrix Plan\n", encoding="utf-8")
    (run_dir / "server_matrix_plan.json").write_text('{"smoke_only": true}\n', encoding="utf-8")
    (run_dir / "run_config.env").write_text(f"RUN_ID={run_id}\nSMOKE_ONLY=1\n", encoding="utf-8")
    (run_dir / "launch.env").write_text(
        f"RUN_ID={run_id}\nRUN_MARKER_ID=synthetic-marker\nRUN_STARTED_AT_EPOCH=1700000000\n",
        encoding="utf-8",
    )
    (run_dir / "server_job.pid").write_text("12345\n", encoding="utf-8")
    (run_dir / "server_job.marker").write_text(
        f"RUN_ID={run_id}\nRUN_MARKER_ID=synthetic-marker\nRUN_STARTED_AT_EPOCH=1700000000\nMODE=paper-run\n",
        encoding="utf-8",
    )
    (run_dir / "planned_steps.txt").write_text("synthetic acceptance flow smoke\n", encoding="utf-8")
    (run_dir / "server_job.log").write_text("synthetic job complete\n", encoding="utf-8")
    (summary_dir / f"server_{run_id}.md").write_text("# Synthetic Summary\n", encoding="utf-8")
    (summary_dir / f"server_{run_id}_failures.csv").write_text("suite,system,error\n", encoding="utf-8")
    (summary_dir / f"server_{run_id}_failures_cases.csv").write_text("suite,system,error\n", encoding="utf-8")
    (summary_dir / f"server_{run_id}_failures.md").write_text("# Synthetic Failures\n", encoding="utf-8")
    (summary_dir / f"server_{run_id}_evidence.csv").write_text("suite,system,SER\n", encoding="utf-8")
    (summary_dir / f"server_{run_id}_evidence.md").write_text("# Synthetic Evidence\n", encoding="utf-8")
    (summary_dir / f"server_{run_id}_results_snippet.tex").write_text("% synthetic snippet\n", encoding="utf-8")
    (summary_dir / f"server_{run_id}_abstract.tex").write_text(
        "\\begin{abstract}\n"
        f"EC-SQL Spider2 validated server run {run_id} includes a SOTA-style baseline "
        "comparison and semantic pass rate evidence for the synthetic acceptance-flow matrix.\n"
        "\\end{abstract}\n",
        encoding="utf-8",
    )
    (summary_dir / f"server_{run_id}_dataset_scale_report.json").write_text(
        '{"status": "PASS", "smoke_only": true}\n',
        encoding="utf-8",
    )
    (summary_dir / f"server_{run_id}_dataset_scale_report.md").write_text("# Synthetic Dataset Scale\n", encoding="utf-8")
    (summary_dir / f"server_{run_id}_acceptance_contract.json").write_text(
        '{"status": "PASS", "smoke_only": true}\n',
        encoding="utf-8",
    )
    (summary_dir / f"server_{run_id}_acceptance_contract.md").write_text("# Synthetic Acceptance Contract\n", encoding="utf-8")


def smoke_acceptance_flow(run_id: str) -> list[str]:
    notes: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ecsql_acceptance_smoke_") as tmp:
        root = Path(tmp)
        source = root / "source" / run_id
        imported = root / "imported" / run_id
        write_complete_synthetic_run(source, run_id)
        matrix = check_server_matrix(run_id, source)
        if matrix.status != "PASS":
            raise RuntimeError(f"synthetic source matrix did not pass: {matrix.status}: {matrix.evidence}")
        notes.append("synthetic_matrix=PASS")

        archive, checksum, bundle_check = build_bundle(run_id, str(source))
        if bundle_check.status != "PASS":
            raise RuntimeError(f"bundle builder returned {bundle_check.status}: {bundle_check.evidence}")
        notes.append(f"bundle={archive.name}")

        errors, manifest = verify_bundle(archive, checksum, run_id)
        if errors:
            raise RuntimeError("bundle verifier failed:\n" + "\n".join(f"- {error}" for error in errors))
        notes.append(f"verify_bundle=PASS files={len(manifest.get('files') or [])}")

        dest, report = import_bundle(archive, checksum, run_id, imported)
        matrix_report = report.get("matrix_check") or {}
        if matrix_report.get("status") != "PASS":
            raise RuntimeError(f"imported matrix did not pass: {matrix_report}")
        notes.append(f"import_bundle=PASS dest={dest}")

        dataset_root = root / "Spider2"
        manifest = root / "manifest.csv"
        write_manifest(manifest, dataset_root)
        final_dest = root / "finalized" / run_id
        abstract_copy = root / "paper" / "server_abstract.tex"
        root_abstract_copy = root / "paper_root_abstract.tex"
        final_dest, final_report = finalize_server_result(
            archive=archive,
            checksum=checksum,
            run_id=run_id,
            dest_dir=final_dest,
            overwrite=True,
            dataset_root=dataset_root,
            manifest=manifest,
            abstract_output=abstract_copy,
            root_abstract_output=root_abstract_copy,
        )
        if final_report.get("completion_status") != "PASS":
            raise RuntimeError(f"finalizer did not pass: {final_report}")
        if not abstract_copy.exists() or run_id not in abstract_copy.read_text(encoding="utf-8"):
            raise RuntimeError(f"finalizer did not copy the server abstract: {abstract_copy}")
        notes.append(f"finalize=PASS dest={final_dest}")
    return notes


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the server result bundle verification/import acceptance flow.")
    parser.add_argument("--run-id", default="acceptance_flow_smoke")
    args = parser.parse_args()
    try:
        notes = smoke_acceptance_flow(args.run_id)
    except Exception as exc:
        print(f"[acceptance-flow-smoke] FAILED: {exc}", file=sys.stderr)
        return 1
    print("[acceptance-flow-smoke] PASS")
    for note in notes:
        print(f"[acceptance-flow-smoke] {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
