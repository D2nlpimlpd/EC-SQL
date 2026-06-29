from __future__ import annotations

import csv
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.audit_goal_readiness import PROJECT_ROOT
from scripts.plan_server_matrix import coverage_summary, expected_artifacts, full_profile_config, main, slugify


class ServerMatrixPlanTests(unittest.TestCase):
    run_id = "unit_matrix_plan"

    def tearDown(self) -> None:
        run_dir = PROJECT_ROOT / "artifacts" / "server_runs" / self.run_id
        if run_dir.exists():
            shutil.rmtree(run_dir)

    def test_slugify_matches_shell_intent(self) -> None:
        self.assertEqual(slugify("qwen2.5-coder:7b"), "qwen2.5-coder_7b")
        self.assertEqual(slugify("model/name with spaces"), "model_name_with_spaces")

    def test_expected_full_profile_artifacts(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = full_profile_config(self.run_id, PROJECT_ROOT / "artifacts" / "server_runs" / self.run_id)
        rows = expected_artifacts(config)
        coverage = coverage_summary(config, rows)
        artifacts = {row["artifact"] for row in rows if row["enabled"] == "1"}
        self.assertIn("spider2_sqlite_boyuesql_ablation_qwen3-vl_8b.json", artifacts)
        self.assertIn("spider2_sqlite_sota_baselines_qwen2.5-coder_7b.json", artifacts)
        self.assertIn("spider2_sqlite_sota_baselines_sqlcoder_7b.json", artifacts)
        self.assertIn("spider2_sqlite_sota_baselines_qwen3_32b.json", artifacts)
        self.assertIn("spider2_dbt_llm_edit_boyuesql_deterministic_full.json", artifacts)
        self.assertIn("spider2_dbt_llm_edit_boyuesql_ablation_no_duckdb_type_repair.json", artifacts)
        self.assertEqual(sum(1 for row in rows if row["stage"] == "dbt_ablation"), 7)
        self.assertEqual(coverage["status"], "PASS")
        checks = {row["requirement"]: row for row in coverage["checks"]}
        self.assertGreaterEqual(checks["SOTA-style SQLite baseline systems"]["value"], 4)
        self.assertGreaterEqual(checks["SOTA baseline models"]["value"], 3)
        self.assertGreaterEqual(checks["DBT ablation systems with 68 cases"]["value"], 5)

    def test_main_writes_plan_files(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("sys.argv", ["plan_server_matrix.py", "--run-id", self.run_id]):
                self.assertEqual(main(), 0)
        run_dir = PROJECT_ROOT / "artifacts" / "server_runs" / self.run_id
        csv_path = run_dir / "expected_artifacts.csv"
        self.assertTrue(csv_path.exists())
        self.assertTrue((run_dir / "server_matrix_plan.md").exists())
        self.assertTrue((run_dir / "server_matrix_plan.json").exists())
        self.assertTrue((run_dir / "server_matrix_coverage.md").exists())
        self.assertTrue((run_dir / "server_matrix_coverage.json").exists())
        self.assertIn("Coverage Gate Preview", (run_dir / "server_matrix_plan.md").read_text(encoding="utf-8"))
        self.assertIn('"status": "PASS"', (run_dir / "server_matrix_coverage.json").read_text(encoding="utf-8"))
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertGreaterEqual(len([row for row in rows if row["enabled"] == "1"]), 10)


if __name__ == "__main__":
    unittest.main()
