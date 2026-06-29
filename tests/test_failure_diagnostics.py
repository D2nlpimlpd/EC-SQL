import unittest
from pathlib import Path

from scripts.analyze_experiment_failures import (
    failure_rows_for_artifact,
    summarize_failures,
)


class FailureDiagnosticsTests(unittest.TestCase):
    def test_classifies_sqlite_failure_types(self) -> None:
        payload = {
            "results": [
                {
                    "instance_id": "s1",
                    "system": "direct",
                    "model": "qwen2.5-coder:7b",
                    "exec_ok": False,
                    "exec_error": "OperationalError: no such column: c.customer_id",
                    "sql": "select c.customer_id from orders o",
                },
                {
                    "instance_id": "s2",
                    "system": "boyuesql",
                    "model": "qwen3-vl:8b",
                    "exec_ok": True,
                    "result_exact": True,
                    "semantic_pass": False,
                    "semantic_guard_errors": ["semantic guard: missing required column AMOUNT"],
                    "no_null_placeholder": True,
                    "sql": "select count(*) from orders",
                },
                {
                    "instance_id": "s3",
                    "system": "schema_only",
                    "exec_ok": True,
                    "result_exact": False,
                    "semantic_pass": False,
                    "no_null_placeholder": False,
                    "sql": "select NULL AS amount from orders",
                },
            ]
        }

        rows = failure_rows_for_artifact(Path("spider2_sqlite_boyuesql_ablation_qwen3.json"), payload)
        self.assertEqual([row["error_class"] for row in rows], ["invalid_column", "semantic_guard", "null_placeholder"])
        self.assertEqual([row["stage"] for row in rows], ["execution", "semantic_guard", "semantic"])

    def test_classifies_dbt_missing_ref(self) -> None:
        payload = {
            "results": [
                {
                    "instance_id": "d1",
                    "system": "dbt_llm_edit",
                    "model": "qwen3-vl:8b",
                    "run_ok": False,
                    "semantic_pass": False,
                    "error": "Compilation Error: model depends on a node named missing_orders",
                }
            ]
        }

        rows = failure_rows_for_artifact(Path("spider2_dbt_llm_edit_qwen3.json"), payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stage"], "dbt_run")
        self.assertEqual(rows[0]["error_class"], "missing_ref")

    def test_classifies_skipped_gold_case(self) -> None:
        payload = {
            "results": [
                {
                    "instance_id": "s4",
                    "system": "boyuesql",
                    "skipped": True,
                    "skip_reason": "gold SQL missing",
                }
            ]
        }

        rows = failure_rows_for_artifact(Path("spider2_sqlite_experiment.json"), payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stage"], "skipped")
        self.assertEqual(rows[0]["error_class"], "missing_artifact")

    def test_classifies_smoke_probe_failures(self) -> None:
        payload = [
            {
                "instance_id": "sm1",
                "probe_ok": False,
                "probe_error": "OperationalError: no such table: missing_table",
                "gold_exec_ok": False,
                "gold_error": "gold sql missing",
            }
        ]

        rows = failure_rows_for_artifact(Path("spider2_sqlite_smoke.json"), payload)
        self.assertEqual([row["stage"] for row in rows], ["schema_probe", "gold_probe"])
        self.assertEqual([row["error_class"] for row in rows], ["missing_table", "missing_artifact"])

    def test_summary_groups_by_suite_system_model_stage_and_class(self) -> None:
        rows = [
            {
                "suite": "spider2-sqlite",
                "system": "direct",
                "model": "m",
                "stage": "execution",
                "error_class": "invalid_column",
            },
            {
                "suite": "spider2-sqlite",
                "system": "direct",
                "model": "m",
                "stage": "execution",
                "error_class": "invalid_column",
            },
        ]

        summary = summarize_failures(rows)
        self.assertEqual(summary[0]["count"], 2)
        self.assertEqual(summary[0]["error_class"], "invalid_column")


if __name__ == "__main__":
    unittest.main()
