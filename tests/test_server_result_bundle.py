from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.build_server_diagnostics import write_diagnostics
from scripts.build_server_result_bundle import build_bundle
from scripts.finalize_server_result import finalize_server_result
from scripts.import_server_result_bundle import import_bundle
from scripts.smoke_test_server_acceptance_flow import smoke_acceptance_flow
from scripts.verify_server_result_bundle import verify_bundle


class ServerResultBundleTests(unittest.TestCase):
    def test_complete_server_run_builds_returnable_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_id = "unit_bundle_complete"
            run_dir = Path(tmp) / run_id
            self._write_complete_run(run_dir, run_id)

            archive, checksum, check = build_bundle(run_id, str(run_dir))

            self.assertEqual(check.status, "PASS")
            self.assertTrue(archive.exists())
            self.assertTrue(checksum.exists())
            self.assertIn(archive.name, checksum.read_text(encoding="utf-8"))

            manifest = run_dir / "summary" / f"server_{run_id}_result_bundle_manifest.json"
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["run_id"], run_id)
            self.assertEqual(payload["validation"]["status"], "PASS")
            self.assertEqual(payload["abstract_validation"]["status"], "PASS")
            self.assertEqual(payload["expected_artifacts_validation"]["status"], "PASS")
            self.assertEqual(payload["launch_marker_validation"]["status"], "PASS")
            self.assertEqual(payload["completion_status"], "PASS")
            self.assertEqual(payload["missing_expected_artifacts"], [])

            with zipfile.ZipFile(archive, "r") as zf:
                names = set(zf.namelist())
                cert = json.loads(
                    zf.read(f"summary/server_{run_id}_completion_certificate.json").decode("utf-8")
                )
            self.assertIn("expected_artifacts.csv", names)
            self.assertIn("server_matrix_coverage.json", names)
            self.assertIn("server_matrix_coverage.md", names)
            self.assertIn("server_job.marker", names)
            self.assertIn("launch.env", names)
            self.assertIn(f"summary/server_{run_id}.md", names)
            self.assertIn(f"summary/server_{run_id}_evidence.md", names)
            self.assertIn(f"summary/server_{run_id}_abstract.tex", names)
            self.assertIn(f"summary/server_{run_id}_dataset_scale_report.md", names)
            self.assertIn(f"summary/server_{run_id}_acceptance_contract.md", names)
            self.assertIn(f"summary/server_{run_id}_completion_certificate.json", names)
            self.assertIn(f"summary/server_{run_id}_result_bundle_manifest.json", names)
            self.assertEqual(cert["expected_artifacts_check"]["status"], "PASS")
            self.assertEqual(cert["launch_marker_check"]["status"], "PASS")
            self.assertEqual(cert["launch_marker"]["RUN_ID"], run_id)
            self.assertEqual(cert["completion_status"], "PASS")

            errors, verified_manifest = verify_bundle(archive, checksum, run_id)
            self.assertEqual(errors, [])
            self.assertEqual(verified_manifest["run_id"], run_id)

    def test_result_bundle_verifier_rejects_bad_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_id = "unit_bundle_bad_checksum"
            run_dir = Path(tmp) / run_id
            self._write_complete_run(run_dir, run_id)
            archive, checksum, _ = build_bundle(run_id, str(run_dir))
            checksum.write_text(f"deadbeef  {archive.name}\n", encoding="utf-8")

            errors, _ = verify_bundle(archive, checksum, run_id)
            self.assertTrue(any("checksum mismatch" in error for error in errors))

    def test_result_bundle_verifier_rejects_bad_completion_certificate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_id = "unit_bundle_bad_certificate"
            run_dir = Path(tmp) / run_id
            self._write_complete_run(run_dir, run_id)
            archive, checksum, _ = build_bundle(run_id, str(run_dir))
            bad_archive = run_dir / "summary" / f"server_{run_id}_bad_certificate.zip"

            cert_name = f"summary/server_{run_id}_completion_certificate.json"
            with zipfile.ZipFile(archive, "r") as src, zipfile.ZipFile(
                bad_archive,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as dst:
                for info in src.infolist():
                    if info.filename == cert_name:
                        cert = json.loads(src.read(info.filename).decode("utf-8"))
                        cert["expected_artifacts_check"]["status"] = "FAIL"
                        cert["missing_expected_artifacts"] = ["missing.json"]
                        dst.writestr(info.filename, json.dumps(cert, ensure_ascii=False, indent=2) + "\n")
                    else:
                        dst.writestr(info, src.read(info.filename))

            errors, _ = verify_bundle(bad_archive, None, run_id)
            self.assertTrue(any("completion certificate expected-artifacts status" in error for error in errors))

    def test_result_bundle_verifier_rejects_bad_launch_marker_certificate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_id = "unit_bundle_bad_marker_cert"
            run_dir = Path(tmp) / run_id
            self._write_complete_run(run_dir, run_id)
            archive, _, _ = build_bundle(run_id, str(run_dir))
            bad_archive = run_dir / "summary" / f"server_{run_id}_bad_marker_certificate.zip"

            cert_name = f"summary/server_{run_id}_completion_certificate.json"
            with zipfile.ZipFile(archive, "r") as src, zipfile.ZipFile(
                bad_archive,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as dst:
                for info in src.infolist():
                    if info.filename == cert_name:
                        cert = json.loads(src.read(info.filename).decode("utf-8"))
                        cert["launch_marker"]["RUN_MARKER_ID"] = "stale-marker"
                        dst.writestr(info.filename, json.dumps(cert, ensure_ascii=False, indent=2) + "\n")
                    else:
                        dst.writestr(info, src.read(info.filename))

            errors, _ = verify_bundle(bad_archive, None, run_id)
            self.assertTrue(any("launch marker mismatch" in error for error in errors))

    def test_complete_server_bundle_imports_to_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_id = "unit_bundle_import"
            run_dir = Path(tmp) / "source" / run_id
            dest_dir = Path(tmp) / "imported" / run_id
            self._write_complete_run(run_dir, run_id)
            archive, checksum, _ = build_bundle(run_id, str(run_dir))

            dest, report = import_bundle(archive, checksum, run_id, dest_dir)

            self.assertEqual(dest, dest_dir)
            self.assertEqual(report["matrix_check"]["status"], "PASS")
            self.assertEqual(report["server_result_abstract_check"]["status"], "PASS")
            self.assertTrue((dest_dir / "summary" / f"server_{run_id}.csv").exists())
            self.assertTrue((dest_dir / "summary" / f"server_{run_id}_import_report.json").exists())

    def test_finalize_server_result_imports_audits_and_copies_abstract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_id = "unit_bundle_finalize"
            source_dir = root / "source" / run_id
            dest_dir = root / "imported" / run_id
            abstract_copy = root / "paper" / "server_abstract.tex"
            root_copy = root / "paper_root_abstract.tex"
            dataset_root = root / "Spider2"
            dataset_root.mkdir(parents=True, exist_ok=True)
            manifest = root / "manifest.csv"
            self._write_manifest(manifest)
            self._write_complete_run(source_dir, run_id)
            archive, checksum, _ = build_bundle(run_id, str(source_dir))
            subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "build_server_release.py")],
                cwd=str(Path(__file__).resolve().parents[1]),
                check=True,
                text=True,
                capture_output=True,
            )

            dest, report = finalize_server_result(
                archive=archive,
                checksum=checksum,
                run_id=run_id,
                dest_dir=dest_dir,
                overwrite=True,
                dataset_root=dataset_root,
                manifest=manifest,
                abstract_output=abstract_copy,
                root_abstract_output=root_copy,
            )

            self.assertEqual(dest, dest_dir)
            self.assertEqual(report["completion_status"], "PASS")
            self.assertTrue(abstract_copy.exists())
            self.assertTrue(root_copy.exists())
            self.assertIn(run_id, abstract_copy.read_text(encoding="utf-8"))
            self.assertTrue((dest_dir / "summary" / f"server_{run_id}_final_acceptance_report.json").exists())
            self.assertTrue((dest_dir / "summary" / f"server_{run_id}_final_acceptance_audit.md").exists())

    def test_result_bundle_verifier_rejects_unvalidated_abstract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_id = "unit_bundle_bad_abstract"
            run_dir = Path(tmp) / run_id
            self._write_complete_run(run_dir, run_id)
            (run_dir / "summary" / f"server_{run_id}_abstract.tex").write_text(
                "\\begin{abstract}Unit\\end{abstract}\n",
                encoding="utf-8",
            )

            with self.assertRaises(RuntimeError):
                build_bundle(run_id, str(run_dir))

    def test_acceptance_flow_smoke_exercises_build_verify_import(self) -> None:
        notes = smoke_acceptance_flow("unit_acceptance_flow")
        joined = "\n".join(notes)
        self.assertIn("synthetic_matrix=PASS", joined)
        self.assertIn("verify_bundle=PASS", joined)
        self.assertIn("import_bundle=PASS", joined)
        self.assertIn("finalize=PASS", joined)

    def test_bundle_import_refuses_existing_destination_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_id = "unit_bundle_import_existing"
            run_dir = Path(tmp) / "source" / run_id
            dest_dir = Path(tmp) / "imported" / run_id
            self._write_complete_run(run_dir, run_id)
            archive, checksum, _ = build_bundle(run_id, str(run_dir))
            (dest_dir / "summary").mkdir(parents=True, exist_ok=True)
            (dest_dir / "summary" / f"server_{run_id}.csv").write_text("existing\n", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                import_bundle(archive, checksum, run_id, dest_dir)

    def test_bundle_import_rejects_path_traversal_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_id = "unit_bundle_import_traversal"
            run_dir = Path(tmp) / "source" / run_id
            dest_dir = Path(tmp) / "imported" / run_id
            self._write_complete_run(run_dir, run_id)
            archive, _, _ = build_bundle(run_id, str(run_dir))
            with zipfile.ZipFile(archive, "a") as zf:
                zf.writestr("../outside.txt", "nope\n")

            with self.assertRaises(ValueError):
                import_bundle(archive, None, run_id, dest_dir)

    def test_incomplete_run_requires_allow_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_id = "unit_bundle_pending"
            run_dir = Path(tmp) / run_id
            summary = run_dir / "summary"
            summary.mkdir(parents=True, exist_ok=True)
            (summary / f"server_{run_id}.csv").write_text("artifact,suite,system,model,cases\n", encoding="utf-8")

            with self.assertRaises(RuntimeError):
                build_bundle(run_id, str(run_dir), allow_pending=False)

            archive, checksum, check = build_bundle(run_id, str(run_dir), allow_pending=True)
            self.assertEqual(check.status, "PENDING")
            self.assertTrue(archive.exists())
            self.assertTrue(checksum.exists())

    def test_diagnostics_bundle_captures_pending_run_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_id = "unit_bundle_diagnostics"
            run_dir = Path(tmp) / run_id
            summary = run_dir / "summary"
            summary.mkdir(parents=True, exist_ok=True)
            (run_dir / "server_job.log").write_text("first\nsecond\nthird\n", encoding="utf-8")
            (run_dir / "run_config.env").write_text("RUN_ID=unit_bundle_diagnostics\n", encoding="utf-8")
            with (run_dir / "expected_artifacts.csv").open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["enabled", "artifact", "stage", "suite"])
                writer.writeheader()
                writer.writerow({"enabled": "1", "artifact": "missing.json", "stage": "unit", "suite": "sqlite"})

            json_path, md_path = write_diagnostics(run_id, str(run_dir), tail_lines=2)
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["matrix_check"]["status"], "PENDING")
            self.assertIn("third", payload["log"]["tail"])
            self.assertIn("missing.json", "\n".join(payload["expected_artifacts"]["missing"]))

            archive, checksum, check = build_bundle(run_id, str(run_dir), allow_pending=True)
            self.assertEqual(check.status, "PENDING")
            errors, _ = verify_bundle(archive, checksum, run_id, allow_pending=True)
            self.assertEqual(errors, [])
            with zipfile.ZipFile(archive, "r") as zf:
                names = set(zf.namelist())
            self.assertIn(f"summary/server_{run_id}_diagnostics.json", names)
            self.assertIn(f"summary/server_{run_id}_diagnostics.md", names)

    def _write_complete_run(self, run_dir: Path, run_id: str) -> None:
        summary_dir = run_dir / "summary"
        summary_dir.mkdir(parents=True, exist_ok=True)
        rows = [
            ("spider2-sqlite", "boyuesql", "qwen3-vl:8b", 24, "sqlite_boyuesql.json"),
            ("spider2-sqlite", "no_semantic_templates", "qwen3-vl:8b", 24, "sqlite_boyuesql.json"),
            ("spider2-sqlite", "no_external_knowledge", "qwen3-vl:8b", 24, "sqlite_boyuesql.json"),
            ("spider2-sqlite", "no_schema_retrieval", "qwen3-vl:8b", 24, "sqlite_boyuesql.json"),
            ("spider2-sqlite", "direct", "qwen2.5-coder:7b", 24, "sqlite_baselines_qwen.json"),
            ("spider2-sqlite", "din_sql_style", "qwen2.5-coder:7b", 24, "sqlite_baselines_qwen.json"),
            ("spider2-sqlite", "dail_sql_style", "qwen2.5-coder:7b", 24, "sqlite_baselines_qwen.json"),
            ("spider2-sqlite", "self_debug_style", "qwen2.5-coder:7b", 24, "sqlite_baselines_qwen.json"),
            ("spider2-sqlite", "direct", "sqlcoder:7b", 24, "sqlite_baselines_sqlcoder.json"),
            ("spider2-sqlite", "direct", "qwen3:32b", 24, "sqlite_baselines_qwen3_32b.json"),
            ("spider2-dbt", "existing_project", "", 68, "spider2_dbt_existing_project.json"),
            ("spider2-dbt", "boyuesql_deterministic_full", "", 68, "spider2_dbt_llm_edit_boyuesql_deterministic_full.json"),
            ("spider2-dbt", "boyuesql_ablation_no_declared_model_synthesis", "", 68, "dbt_ablation.json"),
            ("spider2-dbt", "boyuesql_ablation_no_duckdb_type_repair", "", 68, "dbt_ablation.json"),
            ("spider2-dbt", "boyuesql_ablation_no_missing_ref_source_fallback", "", 68, "dbt_ablation.json"),
            ("spider2-dbt", "boyuesql_ablation_no_declared_column_completion", "", 68, "dbt_ablation.json"),
            ("spider2-dbt", "boyuesql_ablation_no_related_dimension_enrichment", "", 68, "dbt_ablation.json"),
        ]
        for _, _, _, _, artifact in rows:
            (run_dir / artifact).write_text('{"summary": {"cases": 1}, "results": []}\n', encoding="utf-8")

        with (summary_dir / f"server_{run_id}.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
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
                ],
            )
            writer.writeheader()
            for suite, system, model, cases, artifact in rows:
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
                        "implementation_type": "",
                        "official_reproduction": "",
                    }
                )

        with (run_dir / "expected_artifacts.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["enabled", "artifact", "stage", "suite"])
            writer.writeheader()
            for artifact in sorted({row[4] for row in rows}):
                writer.writerow({"enabled": "1", "artifact": artifact, "stage": "unit", "suite": "unit"})

        (run_dir / "server_matrix_plan.md").write_text("# Plan\n", encoding="utf-8")
        (run_dir / "server_matrix_plan.json").write_text("{}\n", encoding="utf-8")
        (run_dir / "server_matrix_coverage.md").write_text("# Coverage\n\nOverall preview status: **PASS**\n", encoding="utf-8")
        (run_dir / "server_matrix_coverage.json").write_text('{"status": "PASS"}\n', encoding="utf-8")
        (run_dir / "run_config.env").write_text(f"RUN_ID={run_id}\n", encoding="utf-8")
        (run_dir / "planned_steps.txt").write_text("unit\n", encoding="utf-8")
        (run_dir / "launch.env").write_text(
            f"RUN_ID={run_id}\nRUN_MARKER_ID=unit-marker\nRUN_STARTED_AT_EPOCH=1700000000\n",
            encoding="utf-8",
        )
        (run_dir / "server_job.pid").write_text("12345\n", encoding="utf-8")
        (run_dir / "server_job.marker").write_text(
            f"RUN_ID={run_id}\nRUN_MARKER_ID=unit-marker\nRUN_STARTED_AT_EPOCH=1700000000\nMODE=paper-run\n",
            encoding="utf-8",
        )
        (run_dir / "server_job.log").write_text("done\n", encoding="utf-8")
        (summary_dir / f"server_{run_id}_cases.csv").write_text("artifact,suite,system\n", encoding="utf-8")
        (summary_dir / f"server_{run_id}.md").write_text("# Summary\n", encoding="utf-8")
        (summary_dir / f"server_{run_id}_failures.csv").write_text("suite,system\n", encoding="utf-8")
        (summary_dir / f"server_{run_id}_failures_cases.csv").write_text("suite,system\n", encoding="utf-8")
        (summary_dir / f"server_{run_id}_failures.md").write_text("# Failures\n", encoding="utf-8")
        (summary_dir / f"server_{run_id}_evidence.csv").write_text("suite,system\n", encoding="utf-8")
        (summary_dir / f"server_{run_id}_evidence.md").write_text("# Evidence\n", encoding="utf-8")
        (summary_dir / f"server_{run_id}_results_snippet.tex").write_text("% snippet\n", encoding="utf-8")
        (summary_dir / f"server_{run_id}_abstract.tex").write_text(
            "\\begin{abstract}\n"
            f"BoyueSQL Spider2 validated server run {run_id} includes a SOTA-style baseline "
            "comparison and semantic pass rate evidence for the completed matrix.\n"
            "\\end{abstract}\n",
            encoding="utf-8",
        )
        (summary_dir / f"server_{run_id}_dataset_scale_report.json").write_text('{"status": "PASS"}\n', encoding="utf-8")
        (summary_dir / f"server_{run_id}_dataset_scale_report.md").write_text("# Dataset Scale\n", encoding="utf-8")
        (summary_dir / f"server_{run_id}_acceptance_contract.json").write_text('{"version": 1}\n', encoding="utf-8")
        (summary_dir / f"server_{run_id}_acceptance_contract.md").write_text("# Acceptance Contract\n", encoding="utf-8")

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
