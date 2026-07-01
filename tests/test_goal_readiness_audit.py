from __future__ import annotations

import unittest
import csv
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from scripts.audit_goal_readiness import (
    PROJECT_ROOT,
    Check,
    check_acceptance_contract,
    check_dataset_scale_report,
    check_handoff_dry_run,
    check_manifest,
    check_server_matrix,
    check_server_result_abstract,
    check_workspace_residue,
    manifest_counts,
    markdown_report,
)
from scripts.build_dataset_scale_report import build_report, to_markdown


class GoalReadinessAuditTests(unittest.TestCase):
    def tearDown(self) -> None:
        for name in (
            "unit_matrix_complete",
            "unit_matrix_with_official",
            "unit_matrix_incomplete",
            "unit_matrix_missing_abstract",
        ):
            path = PROJECT_ROOT / "artifacts" / "server_runs" / name
            if path.exists():
                shutil.rmtree(path)

    def test_missing_server_run_is_pending(self) -> None:
        check = check_server_matrix(None)
        self.assertEqual(check.status, "PENDING")
        self.assertIn("SERVER_RUN_ID", check.evidence)
        abstract_check = check_server_result_abstract(None)
        self.assertEqual(abstract_check.status, "PENDING")
        self.assertIn("SERVER_RUN_ID", abstract_check.evidence)

    def test_manifest_audit_reports_unique_instance_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.csv"
            with manifest.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["setting", "instance_id", "db", "engine", "db_path", "question", "external_knowledge"],
                )
                writer.writeheader()
                for i in range(600):
                    writer.writerow(
                        {
                            "setting": "spider2-lite",
                            "instance_id": f"case{i:03d}",
                            "db": f"db{i % 200:03d}",
                            "engine": "sqlite",
                        }
                    )
                for i in range(68):
                    writer.writerow(
                        {
                            "setting": "spider2-dbt",
                            "instance_id": f"dbt{i:03d}",
                            "db": f"dbt{i:03d}",
                            "engine": "duckdb-dbt",
                        }
                    )
            rows, counts, coverage = manifest_counts(manifest)
            self.assertEqual(rows, 668)
            self.assertEqual(coverage["unique_instances"], 668)
            self.assertGreaterEqual(coverage["unique_dbs"], 200)
            check = check_manifest(manifest, root)
            self.assertEqual(check.status, "PASS")
            self.assertIn("unique instances/projects", check.evidence)
            self.assertIn("unique logical db names", check.evidence)
            scale = build_report(root, manifest)
            self.assertEqual(scale.status, "PASS")
            self.assertEqual(scale.manifest_rows, 668)
            self.assertEqual(scale.sqlite_rows, 600)
            self.assertEqual(scale.dbt_rows, 68)
            self.assertIn("Spider2 Dataset Scale Report", to_markdown(scale))
            scale_check = check_dataset_scale_report(root, manifest)
            self.assertEqual(scale_check.status, "PASS")
            self.assertIn("manifest rows", scale_check.evidence)
            contract_check = check_acceptance_contract("unit_contract")
            self.assertEqual(contract_check.status, "PASS")
            self.assertIn("enabled_artifacts", contract_check.evidence)

    def test_complete_server_matrix_passes(self) -> None:
        self._write_server_run(
            "unit_matrix_complete",
            [
                ("spider2-sqlite", "ecsql", "qwen3-vl:8b", 24, "sqlite_ecsql.json"),
                ("spider2-sqlite", "no_semantic_templates", "qwen3-vl:8b", 24, "sqlite_ecsql.json"),
                ("spider2-sqlite", "no_external_knowledge", "qwen3-vl:8b", 24, "sqlite_ecsql.json"),
                ("spider2-sqlite", "no_schema_retrieval", "qwen3-vl:8b", 24, "sqlite_ecsql.json"),
                ("spider2-sqlite", "direct", "qwen2.5-coder:7b", 24, "sqlite_baselines_qwen.json"),
                ("spider2-sqlite", "din_sql_style", "qwen2.5-coder:7b", 24, "sqlite_baselines_qwen.json"),
                ("spider2-sqlite", "dail_sql_style", "qwen2.5-coder:7b", 24, "sqlite_baselines_qwen.json"),
                ("spider2-sqlite", "self_debug_style", "qwen2.5-coder:7b", 24, "sqlite_baselines_qwen.json"),
                ("spider2-sqlite", "direct", "sqlcoder:7b", 24, "sqlite_baselines_sqlcoder.json"),
                ("spider2-sqlite", "direct", "qwen3:32b", 24, "sqlite_baselines_qwen3_32b.json"),
                ("spider2-dbt", "existing_project", "", 68, "spider2_dbt_existing_project.json"),
                ("spider2-dbt", "ecsql_deterministic_full", "", 68, "spider2_dbt_llm_edit_ecsql_deterministic_full.json"),
                ("spider2-dbt", "ecsql_ablation_no_declared_model_synthesis", "", 68, "dbt_ablation.json"),
                ("spider2-dbt", "ecsql_ablation_no_duckdb_type_repair", "", 68, "dbt_ablation.json"),
                ("spider2-dbt", "ecsql_ablation_no_missing_ref_source_fallback", "", 68, "dbt_ablation.json"),
                ("spider2-dbt", "ecsql_ablation_no_declared_column_completion", "", 68, "dbt_ablation.json"),
                ("spider2-dbt", "ecsql_ablation_no_related_dimension_enrichment", "", 68, "dbt_ablation.json"),
            ],
        )
        check = check_server_matrix("unit_matrix_complete")
        self.assertEqual(check.status, "PASS")
        self.assertIn("SOTA baselines=4", check.evidence)
        self.assertIn("DBT ablations=5", check.evidence)
        abstract_check = check_server_result_abstract("unit_matrix_complete")
        self.assertEqual(abstract_check.status, "PASS")
        self.assertIn("server_unit_matrix_complete_abstract.tex", abstract_check.evidence)

    def test_official_external_baseline_counts_toward_server_matrix(self) -> None:
        self._write_server_run(
            "unit_matrix_with_official",
            [
                ("spider2-sqlite", "ecsql", "qwen3-vl:8b", 24, "sqlite_ecsql.json"),
                ("spider2-sqlite", "no_semantic_templates", "qwen3-vl:8b", 24, "sqlite_ecsql.json"),
                ("spider2-sqlite", "no_external_knowledge", "qwen3-vl:8b", 24, "sqlite_ecsql.json"),
                ("spider2-sqlite", "no_schema_retrieval", "qwen3-vl:8b", 24, "sqlite_ecsql.json"),
                ("spider2-sqlite", "direct", "qwen2.5-coder:7b", 24, "sqlite_baselines_qwen.json"),
                ("spider2-sqlite", "din_sql_style", "qwen2.5-coder:7b", 24, "sqlite_baselines_qwen.json"),
                ("spider2-sqlite", "dail_sql_style", "qwen2.5-coder:7b", 24, "sqlite_baselines_qwen.json"),
                (
                    "spider2-sqlite",
                    "mac_sql_official",
                    "gpt-4.1",
                    24,
                    "mac_sql_official_registered.json",
                    "official_external_reproduction",
                    "True",
                ),
                ("spider2-sqlite", "direct", "qwen3:32b", 24, "sqlite_baselines_qwen3_32b.json"),
                ("spider2-dbt", "existing_project", "", 68, "spider2_dbt_existing_project.json"),
                ("spider2-dbt", "ecsql_deterministic_full", "", 68, "spider2_dbt_llm_edit_ecsql_deterministic_full.json"),
                ("spider2-dbt", "ecsql_ablation_no_declared_model_synthesis", "", 68, "dbt_ablation.json"),
                ("spider2-dbt", "ecsql_ablation_no_duckdb_type_repair", "", 68, "dbt_ablation.json"),
                ("spider2-dbt", "ecsql_ablation_no_missing_ref_source_fallback", "", 68, "dbt_ablation.json"),
                ("spider2-dbt", "ecsql_ablation_no_declared_column_completion", "", 68, "dbt_ablation.json"),
                ("spider2-dbt", "ecsql_ablation_no_related_dimension_enrichment", "", 68, "dbt_ablation.json"),
            ],
        )
        check = check_server_matrix("unit_matrix_with_official")
        self.assertEqual(check.status, "PASS")
        self.assertIn("mac_sql_official", check.evidence)
        self.assertIn("SOTA baselines=4", check.evidence)

    def test_incomplete_server_matrix_stays_pending(self) -> None:
        self._write_server_run(
            "unit_matrix_incomplete",
            [
                ("spider2-sqlite", "ecsql", "qwen3-vl:8b", 24, "sqlite_ecsql.json"),
                ("spider2-sqlite", "direct", "qwen2.5-coder:7b", 24, "sqlite_baselines_qwen.json"),
                ("spider2-dbt", "ecsql_deterministic_full", "", 68, "spider2_dbt_llm_edit_ecsql_deterministic_full.json"),
            ],
        )
        check = check_server_matrix("unit_matrix_incomplete")
        self.assertEqual(check.status, "PENDING")
        self.assertIn("incomplete server matrix", check.evidence)
        self.assertIn("SQLite ablations", check.evidence)

    def test_server_result_abstract_is_required_after_matrix_run(self) -> None:
        self._write_server_run(
            "unit_matrix_missing_abstract",
            [
                ("spider2-sqlite", "ecsql", "qwen3-vl:8b", 24, "sqlite_ecsql.json"),
            ],
            write_abstract=False,
        )
        check = check_server_result_abstract("unit_matrix_missing_abstract")
        self.assertEqual(check.status, "PENDING")
        self.assertIn("missing or empty", check.evidence)

    def test_cli_accepts_server_run_dir_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_id = "unit_matrix_override"
            run_dir = root / run_id
            self._write_server_run_at(
                run_dir,
                run_id,
                [
                    ("spider2-sqlite", "ecsql", "qwen3-vl:8b", 24, "sqlite_ecsql.json"),
                    ("spider2-sqlite", "no_semantic_templates", "qwen3-vl:8b", 24, "sqlite_ecsql.json"),
                    ("spider2-sqlite", "no_external_knowledge", "qwen3-vl:8b", 24, "sqlite_ecsql.json"),
                    ("spider2-sqlite", "no_schema_retrieval", "qwen3-vl:8b", 24, "sqlite_ecsql.json"),
                    ("spider2-sqlite", "direct", "qwen2.5-coder:7b", 24, "sqlite_baselines_qwen.json"),
                    ("spider2-sqlite", "din_sql_style", "qwen2.5-coder:7b", 24, "sqlite_baselines_qwen.json"),
                    ("spider2-sqlite", "dail_sql_style", "qwen2.5-coder:7b", 24, "sqlite_baselines_qwen.json"),
                    ("spider2-sqlite", "self_debug_style", "qwen2.5-coder:7b", 24, "sqlite_baselines_qwen.json"),
                    ("spider2-sqlite", "direct", "sqlcoder:7b", 24, "sqlite_baselines_sqlcoder.json"),
                    ("spider2-sqlite", "direct", "qwen3:32b", 24, "sqlite_baselines_qwen3_32b.json"),
                    ("spider2-dbt", "existing_project", "", 68, "spider2_dbt_existing_project.json"),
                    ("spider2-dbt", "ecsql_deterministic_full", "", 68, "spider2_dbt_llm_edit_ecsql_deterministic_full.json"),
                    ("spider2-dbt", "ecsql_ablation_no_declared_model_synthesis", "", 68, "dbt_ablation.json"),
                    ("spider2-dbt", "ecsql_ablation_no_duckdb_type_repair", "", 68, "dbt_ablation.json"),
                    ("spider2-dbt", "ecsql_ablation_no_missing_ref_source_fallback", "", 68, "dbt_ablation.json"),
                    ("spider2-dbt", "ecsql_ablation_no_declared_column_completion", "", 68, "dbt_ablation.json"),
                    ("spider2-dbt", "ecsql_ablation_no_related_dimension_enrichment", "", 68, "dbt_ablation.json"),
                ],
            )
            dataset_root = root / "Spider2"
            dataset_root.mkdir(parents=True, exist_ok=True)
            manifest = root / "manifest.csv"
            self._write_manifest(manifest)
            out = root / "audit.md"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "audit_goal_readiness.py"),
                    "--dataset-root",
                    str(dataset_root),
                    "--manifest",
                    str(manifest),
                    "--server-run-id",
                    run_id,
                    "--server-run-dir",
                    str(run_dir),
                    "--out",
                    str(out),
                ],
                cwd=str(PROJECT_ROOT),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            text = out.read_text(encoding="utf-8")
            self.assertIn("Server-result abstract generated", text)
            self.assertIn("Full server SOTA matrix executed", text)
            self.assertIn("PASS", text)

    def test_markdown_report_includes_completion_rule(self) -> None:
        report = markdown_report(
            [
                Check("Server release smoke test", "PASS", "ok"),
                Check("Spider2 dataset scale report", "PASS", "ok"),
                Check("Server acceptance contract", "PASS", "ok"),
                Check("Server handoff dry run", "PASS", "ok"),
                Check("Full server SOTA matrix executed", "PENDING", "missing"),
            ]
        )
        self.assertIn("Completion rule", report)
        self.assertIn("Server release smoke test", report)
        self.assertIn("Spider2 dataset scale report", report)
        self.assertIn("Server acceptance contract", report)
        self.assertIn("Server handoff dry run", report)
        self.assertIn("PENDING", report)

    def test_handoff_dry_run_check_passes_without_ssh(self) -> None:
        check = check_handoff_dry_run("unit_matrix_wait")
        self.assertEqual(check.status, "PASS")
        self.assertIn("doctor/launch/diagnostics/wait", check.evidence)
        self.assertIn("no ssh/scp execution", check.evidence)

    def test_workspace_residue_check_fails_on_private_legacy_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ase.tex").write_text("EXAM" + "_RECORD\n", encoding="utf-8")
            check = check_workspace_residue(root)
            self.assertEqual(check.status, "FAIL")
            self.assertIn("finding", check.evidence)

    def _write_server_run(self, run_id: str, rows: list[tuple], write_abstract: bool = True) -> None:
        run_dir = PROJECT_ROOT / "artifacts" / "server_runs" / run_id
        self._write_server_run_at(run_dir, run_id, rows, write_abstract=write_abstract)

    def _write_server_run_at(
        self,
        run_dir: Path,
        run_id: str,
        rows: list[tuple],
        write_abstract: bool = True,
    ) -> None:
        summary_dir = run_dir / "summary"
        summary_dir.mkdir(parents=True, exist_ok=True)
        for row in rows:
            artifact = row[4]
            (run_dir / artifact).write_text('{"summary": {"cases": 1}, "results": []}\n', encoding="utf-8")
        fields = [
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
        with (summary_dir / f"server_{run_id}.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                suite, system, model, cases, artifact = row[:5]
                implementation_type = row[5] if len(row) > 5 else ""
                official_reproduction = row[6] if len(row) > 6 else ""
                writer.writerow(
                    {
                        "artifact": artifact,
                        "suite": suite,
                        "system": system,
                        "model": model,
                        "cases": cases,
                        "ER": 100,
                        "RE": 100,
                        "SER": 100,
                        "implementation_type": implementation_type,
                        "official_reproduction": official_reproduction,
                    }
                )
        (summary_dir / f"server_{run_id}_cases.csv").write_text("artifact,suite,system\n", encoding="utf-8")
        (summary_dir / f"server_{run_id}.md").write_text("# Summary\n", encoding="utf-8")
        (summary_dir / f"server_{run_id}_failures.md").write_text("# Failures\n", encoding="utf-8")
        if write_abstract:
            (summary_dir / f"server_{run_id}_abstract.tex").write_text(
                "\\begin{abstract}\n"
                f"EC-SQL Spider2 validated server run {run_id} reports a SOTA-style baseline "
                "comparison and semantic pass rate evidence.\n"
                "\\end{abstract}\n",
                encoding="utf-8",
            )

    def _write_manifest(self, path: Path) -> None:
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["setting", "instance_id", "db", "engine", "db_path", "question", "external_knowledge"],
            )
            writer.writeheader()
            for i in range(600):
                writer.writerow(
                    {
                        "setting": "spider2-lite",
                        "instance_id": f"case{i:03d}",
                        "db": f"db{i % 220:03d}",
                        "engine": "sqlite",
                    }
                )
            for i in range(68):
                writer.writerow(
                    {
                        "setting": "spider2-dbt",
                        "instance_id": f"dbt{i:03d}",
                        "db": f"dbt{i:03d}",
                        "engine": "duckdb-dbt",
                    }
                )


if __name__ == "__main__":
    unittest.main()
