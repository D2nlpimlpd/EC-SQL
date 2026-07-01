import argparse
import unittest
from pathlib import Path

from scripts.aggregate_experiment_results import infer_suite, summary_rows_for_artifact, normalize_summary_row
from scripts.register_external_baseline_result import build_payload


class AggregateExperimentResultsTests(unittest.TestCase):
    def test_infers_sqlite_suite_from_model_matrix_filename(self) -> None:
        self.assertEqual(
            infer_suite(Path("spider2_sqlite_ecsql_ablation_qwen3-vl_8b.json"), {}),
            "spider2-sqlite",
        )
        self.assertEqual(
            infer_suite(Path("spider2_sqlite_sota_baselines_qwen2.5-coder_7b.json"), {}),
            "spider2-sqlite",
        )
        self.assertEqual(
            infer_suite(Path("sqlite_runner_schema_only_check.json"), {}),
            "spider2-sqlite",
        )

    def test_infers_dbt_suites_from_server_filenames(self) -> None:
        self.assertEqual(
            infer_suite(Path("spider2_dbt_existing_project.json"), {}),
            "spider2-dbt",
        )
        self.assertEqual(
            infer_suite(Path("spider2_dbt_llm_edit_qwen3-vl_8b.json"), {}),
            "spider2-dbt-edit",
        )

    def test_summary_row_includes_baseline_scope_metadata(self) -> None:
        row = normalize_summary_row(
            artifact=Path("spider2_sqlite_sota_baselines_qwen2.5-coder_7b.json"),
            suite="spider2-sqlite",
            system="din_sql_style",
            model="qwen2.5-coder:7b",
            metrics={"cases": 5, "ER": 20.0},
            rows=[],
        )
        self.assertEqual(row["implementation_type"], "prompt_style_proxy")
        self.assertEqual(row["official_reproduction"], "False")
        self.assertEqual(row["baseline_reference"], "DIN-SQL")
        self.assertIn("not execute the official DIN-SQL code", row["notes"])

    def test_external_official_baseline_metadata_overrides_proxy_manifest(self) -> None:
        args = argparse.Namespace(
            suite="spider2-sqlite",
            system="mac_sql_style",
            model="gpt-4.1",
            cases=24,
            er="91.7",
            re="87.5",
            ser="83.3",
            ser_evaluable="",
            run_ok="",
            semantic_pass="",
            gold_evaluable="",
            gold_artifact_issues="",
            avg_latency_s="12.5",
            baseline_reference="MAC-SQL official",
            report_label="MAC-SQL official reproduction",
            implementation_type="official_external_reproduction",
            official_reproduction="True",
            source_repo="https://example.invalid/mac-sql",
            source_commit="abc123",
            source_command="bash run.sh",
            source_artifact="mac_sql_results.json",
            notes="external run",
        )
        payload = build_payload(args)
        rows = summary_rows_for_artifact(Path("mac_sql_official_registered.json"), payload)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["implementation_type"], "official_external_reproduction")
        self.assertEqual(row["official_reproduction"], "True")
        self.assertEqual(row["baseline_reference"], "MAC-SQL official")
        self.assertEqual(row["report_label"], "MAC-SQL official reproduction")
        self.assertEqual(row["model"], "gpt-4.1")
        self.assertEqual(row["SER"], 83.3)


if __name__ == "__main__":
    unittest.main()
