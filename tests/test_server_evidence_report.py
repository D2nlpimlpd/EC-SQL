from __future__ import annotations

import csv
import shutil
import unittest
from pathlib import Path

from scripts.audit_goal_readiness import PROJECT_ROOT
from scripts.build_server_abstract import build_abstract
from scripts.build_server_evidence_report import build_report, category


class ServerEvidenceReportTests(unittest.TestCase):
    run_id = "unit_evidence_report"

    def tearDown(self) -> None:
        run_dir = PROJECT_ROOT / "artifacts" / "server_runs" / self.run_id
        if run_dir.exists():
            shutil.rmtree(run_dir)

    def test_build_report_writes_csv_markdown_and_latex(self) -> None:
        self._write_complete_server_run()
        check, paths = build_report(self.run_id)
        self.assertEqual(check.status, "PASS")
        self.assertTrue(paths.evidence_csv.exists())
        self.assertTrue(paths.evidence_md.exists())
        self.assertTrue(paths.snippet_tex.exists())
        self.assertIn("Server Evidence Report", paths.evidence_md.read_text(encoding="utf-8"))
        self.assertIn("Best EC-SQL SQLite result", paths.snippet_tex.read_text(encoding="utf-8"))
        with paths.evidence_csv.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        categories = {row["category"] for row in rows}
        self.assertIn("sqlite_ecsql_full", categories)
        self.assertIn("sqlite_sota_baseline", categories)
        self.assertIn("dbt_ecsql_full", categories)
        self.assertIn("dbt_ablation", categories)
        baseline_rows = [row for row in rows if row["category"] == "sqlite_sota_baseline"]
        self.assertTrue(baseline_rows)
        self.assertIn("implementation_type", baseline_rows[0])
        self.assertIn("official_reproduction", baseline_rows[0])
        self.assertTrue(any(row["implementation_type"] == "prompt_style_proxy" for row in baseline_rows))
        self.assertTrue(any(row["official_reproduction"] == "False" for row in baseline_rows))
        self.assertIn("prompt-style proxies", paths.evidence_md.read_text(encoding="utf-8"))

    def test_build_abstract_from_validated_server_evidence(self) -> None:
        self._write_complete_server_run()
        abstract = build_abstract(self.run_id)

        self.assertTrue(abstract.exists())
        text = abstract.read_text(encoding="utf-8")
        self.assertIn("\\begin{abstract}", text)
        self.assertIn("validated server run", text)
        self.assertIn("\\texttt{unit\\_evidence\\_report}", text)
        self.assertIn("24 locally", text)
        self.assertIn("68 Spider2-DBT tasks", text)
        self.assertIn("3 SQLite ablations", text)
        self.assertIn("5 DBT ablations", text)
        self.assertIn("6 SOTA-style baseline rows", text)
        self.assertIn("100.00\\%", text)

    def test_official_external_sqlite_result_is_baseline_category(self) -> None:
        self.assertEqual(
            category(
                {
                    "suite": "spider2-sqlite",
                    "system": "mac_sql_official",
                    "implementation_type": "official_external_reproduction",
                    "official_reproduction": "True",
                }
            ),
            "sqlite_sota_baseline",
        )

    def _write_complete_server_run(self) -> None:
        run_dir = PROJECT_ROOT / "artifacts" / "server_runs" / self.run_id
        summary_dir = run_dir / "summary"
        summary_dir.mkdir(parents=True, exist_ok=True)
        rows = [
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
        ]
        for _, _, _, _, artifact in rows:
            (run_dir / artifact).write_text('{"summary": {"cases": 1}, "results": []}\n', encoding="utf-8")
        fields = ["artifact", "suite", "system", "model", "cases", "ER", "RE", "SER", "SER_evaluable", "avg_latency_s"]
        with (summary_dir / f"server_{self.run_id}.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for suite, system, model, cases, artifact in rows:
                writer.writerow(
                    {
                        "artifact": artifact,
                        "suite": suite,
                        "system": system,
                        "model": model,
                        "cases": cases,
                        "ER": 100.0,
                        "RE": 100.0,
                        "SER": 100.0,
                        "SER_evaluable": 100.0,
                        "avg_latency_s": 1.23,
                    }
                )
        (summary_dir / f"server_{self.run_id}_cases.csv").write_text("artifact,suite,system\n", encoding="utf-8")
        (summary_dir / f"server_{self.run_id}.md").write_text("# Summary\n", encoding="utf-8")
        (summary_dir / f"server_{self.run_id}_failures.md").write_text("# Failures\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
