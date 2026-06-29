from __future__ import annotations

import argparse
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class ServerHandoffScriptTests(unittest.TestCase):
    def test_one_click_exposes_resume_and_summarize_modes(self) -> None:
        text = (PROJECT_ROOT / "scripts" / "one_click_linux.sh").read_text(encoding="utf-8")
        self.assertIn("models     Pull required Ollama models", text)
        self.assertIn("dataset-report Build the Spider2 dataset scale report", text)
        self.assertIn("contract   Build the machine-readable server acceptance contract", text)
        self.assertIn("plan       Write expected server matrix", text)
        self.assertIn("launch     Start benchmark/resume in the background", text)
        self.assertIn("paper-run  Run the full foreground paper matrix", text)
        self.assertIn("paper-launch Start paper-run in the background", text)
        self.assertIn("resume     Resume an interrupted full benchmark", text)
        self.assertIn("summarize  Rebuild summary/failure reports", text)
        self.assertIn("status     Show PID/log/artifact status", text)
        self.assertIn("evidence   Build paper-ready evidence", text)
        self.assertIn("abstract   Build the server-result-grounded abstract", text)
        self.assertIn("bundle     Package server summaries", text)
        self.assertIn("upload-packet Package the release", text)
        self.assertIn("run_models()", text)
        self.assertIn("run_dataset_report()", text)
        self.assertIn("run_acceptance_contract()", text)
        self.assertIn("run_plan()", text)
        self.assertIn("run_launch()", text)
        self.assertIn("run_paper_run()", text)
        self.assertIn("run_paper_launch()", text)
        self.assertIn("run_resume()", text)
        self.assertIn("run_summarize()", text)
        self.assertIn("run_status()", text)
        self.assertIn("run_audit()", text)
        self.assertIn("run_evidence()", text)
        self.assertIn("run_abstract()", text)
        self.assertIn("run_bundle()", text)
        self.assertIn("SKIP_EXISTING=1", text)
        self.assertIn("pull_ollama_models.py", text)
        self.assertIn("download_hf_models.py", text)
        self.assertIn("qwen3:32b", text)
        self.assertIn("plan_server_matrix.py", text)
        self.assertIn("launch_server_benchmark.sh", text)
        self.assertIn("server_run_status.py", text)
        self.assertIn("validate_server_matrix.py", text)
        self.assertIn("build_server_evidence_report.py", text)
        self.assertIn("build_server_abstract.py", text)
        self.assertIn("build_server_abstract.py", text)
        self.assertIn("build_server_result_bundle.py", text)
        self.assertIn("aggregate_experiment_results.py", text)
        self.assertIn("*_registered.json", text)
        self.assertIn("analyze_experiment_failures.py", text)
        self.assertIn("audit_goal_readiness.py", text)
        self.assertIn("build_dataset_scale_report.py", text)
        self.assertIn("build_server_acceptance_contract.py", text)
        self.assertIn("build_server_upload_packet.py", text)
        setup = (PROJECT_ROOT / "scripts" / "setup_linux.sh").read_text(encoding="utf-8")
        self.assertIn('INSTALL_ORACLE="${INSTALL_ORACLE:-0}"', setup)
        self.assertIn('python -m pip install -r requirements-oracle.txt', setup)

    def test_preflight_checks_bash_and_venv(self) -> None:
        text = (PROJECT_ROOT / "scripts" / "server_preflight.py").read_text(encoding="utf-8")
        self.assertIn('check_command("git", "git")', text)
        self.assertIn("check_bash_command", text)
        self.assertIn("find_bash", text)
        self.assertIn("check_linux_shell_scripts", text)
        self.assertIn("check_shell_syntax", text)
        self.assertIn("--skip-shell-syntax", text)
        self.assertIn("def check_python_venv()", text)
        self.assertIn("python_venv", text)
        self.assertIn("pull_ollama_models.py", text)
        self.assertIn("download_hf_models.py", text)
        self.assertIn("plan_server_matrix.py", text)
        self.assertIn("launch_server_benchmark.sh", text)
        self.assertIn("server_run_status.py", text)
        self.assertIn("validate_server_matrix.py", text)
        self.assertIn("build_server_evidence_report.py", text)

    def test_release_manifest_mentions_resume_and_summarize(self) -> None:
        text = (PROJECT_ROOT / "scripts" / "build_server_release.py").read_text(encoding="utf-8")
        self.assertIn("one_click_linux.sh models", text)
        self.assertIn("one_click_linux.sh dataset-report", text)
        self.assertIn("one_click_linux.sh contract", text)
        self.assertIn("one_click_linux.sh plan", text)
        self.assertIn("one_click_linux.sh paper-run", text)
        self.assertIn("one_click_linux.sh paper-launch", text)
        self.assertIn("one_click_linux.sh launch", text)
        self.assertIn("one_click_linux.sh status", text)
        self.assertIn("one_click_linux.sh validate", text)
        self.assertIn("one_click_linux.sh evidence", text)
        self.assertIn("one_click_linux.sh abstract", text)
        self.assertIn("one_click_linux.sh bundle", text)
        self.assertIn("one_click_linux.sh upload-packet", text)
        self.assertIn("finalize_server_result.py", text)
        self.assertIn("verifies/imports the bundle", text)
        self.assertIn("smoke_test_server_release.py", text)
        self.assertIn("build_server_handoff_commands.py", text)
        self.assertIn("run_server_handoff.py", text)
        self.assertIn("one_click_linux.sh resume", text)
        self.assertIn("one_click_linux.sh summarize", text)
        self.assertIn("build_server_upload_packet.py", text)
        self.assertIn("requirements-oracle.txt", text)
        self.assertIn("SERVER_MODEL_GUIDE.md", text)
        self.assertIn("smoke_test_server_upload_packet.py", text)
        self.assertIn("smoke_test_server_acceptance_flow.py", text)
        self.assertIn("boyuesql_spider2_abstract.tex", text)
        self.assertIn("write_submission_manifest", text)
        self.assertIn("build_server_submission_manifest", text)
        self.assertIn("requirements-oracle.txt", text)

    def test_server_launch_status_and_validation_scripts_exist(self) -> None:
        pull = (PROJECT_ROOT / "scripts" / "pull_ollama_models.py").read_text(encoding="utf-8")
        hf_download = (PROJECT_ROOT / "scripts" / "download_hf_models.py").read_text(encoding="utf-8")
        plan = (PROJECT_ROOT / "scripts" / "plan_server_matrix.py").read_text(encoding="utf-8")
        launch = (PROJECT_ROOT / "scripts" / "launch_server_benchmark.sh").read_text(encoding="utf-8")
        status = (PROJECT_ROOT / "scripts" / "server_run_status.py").read_text(encoding="utf-8")
        validator = (PROJECT_ROOT / "scripts" / "validate_server_matrix.py").read_text(encoding="utf-8")
        evidence = (PROJECT_ROOT / "scripts" / "build_server_evidence_report.py").read_text(encoding="utf-8")
        bundle_verify = (PROJECT_ROOT / "scripts" / "verify_server_result_bundle.py").read_text(encoding="utf-8")
        bundle_import = (PROJECT_ROOT / "scripts" / "import_server_result_bundle.py").read_text(encoding="utf-8")
        finalizer = (PROJECT_ROOT / "scripts" / "finalize_server_result.py").read_text(encoding="utf-8")
        shell_check = (PROJECT_ROOT / "scripts" / "check_linux_shell_scripts.py").read_text(encoding="utf-8")
        release_smoke = (PROJECT_ROOT / "scripts" / "smoke_test_server_release.py").read_text(encoding="utf-8")
        handoff_commands = (PROJECT_ROOT / "scripts" / "build_server_handoff_commands.py").read_text(encoding="utf-8")
        handoff_runner = (PROJECT_ROOT / "scripts" / "run_server_handoff.py").read_text(encoding="utf-8")
        acceptance_contract = (PROJECT_ROOT / "scripts" / "build_server_acceptance_contract.py").read_text(encoding="utf-8")
        upload_packet = (PROJECT_ROOT / "scripts" / "build_server_upload_packet.py").read_text(encoding="utf-8")
        packet_smoke = (PROJECT_ROOT / "scripts" / "smoke_test_server_upload_packet.py").read_text(encoding="utf-8")
        acceptance_smoke = (PROJECT_ROOT / "scripts" / "smoke_test_server_acceptance_flow.py").read_text(encoding="utf-8")
        self.assertIn("/api/pull", pull)
        self.assertIn("Qwen/Qwen3-32B", hf_download)
        self.assertIn("snapshot_download", hf_download)
        self.assertIn("expected_artifacts.csv", plan)
        self.assertIn("spider2_sqlite_boyuesql_ablation_", plan)
        self.assertIn("nohup env RUN_ID", launch)
        self.assertIn("plan_server_matrix.py", launch)
        self.assertIn("server_job.pid", launch)
        self.assertIn("server_job.marker", launch)
        self.assertIn("RUN_MARKER_ID", launch)
        self.assertIn("fresh launch: clearing stale terminal bundle files", launch)
        self.assertIn("server_${RUN_ID}_result_bundle.zip", launch)
        self.assertIn("server_${RUN_ID}_completion_certificate.json", launch)
        self.assertIn("LAUNCH_MODE", launch)
        self.assertIn("log tail", status)
        self.assertIn("result bundles", status)
        self.assertIn("run_marker", status)
        self.assertIn("marker:", status)
        self.assertIn("expected progress", status)
        self.assertIn("expected_artifacts.csv", status)
        self.assertIn("check_server_matrix", validator)
        self.assertIn("--run-id", validator)
        self.assertIn("results_snippet.tex", evidence)
        self.assertIn("evidence.csv", evidence)
        self.assertIn("verify_bundle", bundle_verify)
        self.assertIn("expected_artifacts.csv", bundle_verify)
        self.assertIn("missing SOTA-style SQLite baseline coverage", bundle_verify)
        self.assertIn("verify_completion_certificate", bundle_verify)
        self.assertIn("expected-artifacts status is not PASS", bundle_verify)
        self.assertIn("launch-marker status is not PASS", bundle_verify)
        self.assertIn("server_job.marker", bundle_verify)
        self.assertIn("import_bundle", bundle_import)
        self.assertIn("finalize_server_result", finalizer)
        self.assertIn("server_run_dir", finalizer)
        self.assertIn("boyuesql_spider2_server_result_abstract.tex", finalizer)
        self.assertIn("check_server_matrix", bundle_import)
        self.assertIn("unsafe archive path", bundle_import)
        self.assertIn("bash -n", shell_check)
        self.assertIn("check_shell_scripts", shell_check)
        self.assertIn("BASH_EXE", shell_check)
        self.assertIn("smoke_release", release_smoke)
        self.assertIn("assert_lf_only_shell_scripts", release_smoke)
        self.assertIn("lf_only_shell_scripts", release_smoke)
        self.assertIn("server_preflight.py", release_smoke)
        self.assertIn("check_linux_shell_scripts.py", release_smoke)
        self.assertIn("one_click_contract=PASS", release_smoke)
        self.assertIn("one_click_linux.sh", release_smoke)
        self.assertIn("build_handoff", handoff_commands)
        self.assertIn("remote_preflight_bash", handoff_commands)
        self.assertIn("server_background_bash", handoff_commands)
        self.assertIn("server_resume_bash", handoff_commands)
        self.assertIn("local_resume_handoff_powershell", handoff_commands)
        self.assertIn("local_acceptance_powershell", handoff_commands)
        self.assertIn("expand_stage", handoff_runner)
        self.assertIn("def supervise", handoff_runner)
        self.assertIn("def remote_preflight", handoff_runner)
        self.assertIn('"remote-preflight": remote_preflight', handoff_runner)
        self.assertIn('"supervise": supervise', handoff_runner)
        self.assertIn("--execute", handoff_runner)
        self.assertIn("--poll-seconds", handoff_runner)
        self.assertIn("--max-wait-seconds", handoff_runner)
        self.assertIn("--remote-min-free-gb", handoff_runner)
        self.assertIn("--remote-require-gpu", handoff_runner)
        self.assertIn("--remote-require-ollama", handoff_runner)
        self.assertIn("--packet", handoff_runner)
        self.assertIn('"doctor": doctor', handoff_runner)
        self.assertIn('"resume": resume', handoff_runner)
        self.assertIn("def resume", handoff_runner)
        self.assertIn("RUN_PACKET_ON_SERVER.sh", handoff_runner)
        self.assertIn("packet_remote_dir", handoff_runner)
        self.assertIn("scripts/finalize_server_result.py", handoff_runner)
        self.assertIn("build_server_upload_packet.py", handoff_runner)
        self.assertIn("smoke_upload_packet", packet_smoke)
        self.assertIn("packet_manifest_layout=PASS", packet_smoke)
        self.assertIn("packet_handoff_doc=PASS", packet_smoke)
        self.assertIn("RUN_PACKET_ON_SERVER.sh", packet_smoke)
        self.assertIn("packet_run_script_bash_syntax", packet_smoke)
        self.assertIn("--run-doctor", packet_smoke)
        self.assertIn("packet_run_script_doctor=PASS", packet_smoke)
        self.assertIn("wait_for_bundle", packet_smoke)
        self.assertIn("doctor:", packet_smoke)
        self.assertIn("PYTHON=", upload_packet)
        self.assertIn("tr -d", upload_packet)
        self.assertIn("ensure_runtime", upload_packet)
        self.assertIn("RUN_PACKET_ON_SERVER.sh [doctor|setup|models|plan|foreground|background|resume", upload_packet)
        self.assertIn("bash scripts/one_click_linux.sh resume", upload_packet)
        self.assertIn("bash scripts/one_click_linux.sh abstract", upload_packet)
        self.assertIn("bash scripts/one_click_linux.sh summarize", upload_packet)
        self.assertIn("unconditional destructive release reset", packet_smoke)
        self.assertIn("smoke_acceptance_flow", acceptance_smoke)
        self.assertIn("verify_bundle=PASS", acceptance_smoke)
        self.assertIn("import_bundle=PASS", acceptance_smoke)
        self.assertIn("finalize=PASS", acceptance_smoke)
        self.assertIn('"wait": wait', handoff_runner)
        self.assertIn("verify_server_result_bundle.py", handoff_runner)
        self.assertIn("verified result bundle is ready", handoff_runner)
        self.assertIn("paper-launch", handoff_runner)
        self.assertIn("paper-run", handoff_runner)
        self.assertIn("RUN_PACKET_ON_SERVER.sh background", handoff_commands)
        self.assertIn("RUN_PACKET_ON_SERVER.sh foreground", handoff_commands)
        self.assertIn("RUN_PACKET_ON_SERVER.sh resume", handoff_commands)
        self.assertIn("--stage resume", handoff_commands)
        self.assertIn("finalize_server_result.py", handoff_commands)
        self.assertIn("copies the server-result abstract", handoff_commands)
        self.assertIn("local_supervised_handoff_powershell", handoff_commands)
        self.assertIn("Remote Preflight", handoff_commands)
        self.assertIn("MIN_FREE_GB", handoff_commands)
        self.assertIn("REQUIRE_GPU", handoff_commands)
        self.assertIn("REQUIRE_OLLAMA", handoff_commands)
        self.assertIn("--stage supervise --packet", handoff_commands)
        self.assertIn("dataset_scale_thresholds", acceptance_contract)
        self.assertIn("server_matrix_thresholds", acceptance_contract)
        self.assertIn("UPLOAD_PACKET_MANIFEST.json", upload_packet)
        self.assertIn("verify_packet", upload_packet)
        self.assertIn("verify_server_result_bundle.py", upload_packet)
        self.assertIn("verified result bundle is ready", upload_packet)

    def test_handoff_runner_resume_dry_run_uses_packet_entrypoint(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "run_server_handoff.py"),
                "--host",
                "alice@example.org",
                "--stage",
                "resume",
                "--run-id",
                "server_unit",
            ],
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("server_unit_upload_packet", completed.stdout)
        self.assertIn("RUN_PACKET_ON_SERVER.sh resume", completed.stdout)

    def test_handoff_runner_dry_run_does_not_require_ssh(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "run_server_handoff.py"),
                "--host",
                "alice@example.org",
                "--stage",
                "status",
            ],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("execute=False", completed.stdout)
        self.assertIn("ssh alice@example.org bash -s", completed.stdout)

    def test_handoff_runner_wait_dry_run_polls_for_bundle(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "run_server_handoff.py"),
                "--host",
                "alice@example.org",
                "--stage",
                "wait",
                "--poll-seconds",
                "5",
                "--max-wait-seconds",
                "10",
            ],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("checking result bundle", completed.stdout)
        self.assertIn("server_job.pid", completed.stdout)
        self.assertIn("timed out after 10s", completed.stdout)

    def test_handoff_runner_upload_dry_run_uses_single_packet(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "run_server_handoff.py"),
                "--host",
                "alice@example.org",
                "--stage",
                "upload",
                "--run-id",
                "server_unit",
            ],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("server_unit_server_upload_packet.zip", completed.stdout)
        self.assertIn("server_unit_server_upload_packet.sha256", completed.stdout)
        self.assertNotIn("boyuesql_spider2_server.zip E:", completed.stdout)

    def test_handoff_runner_remote_preflight_dry_run_checks_server_basics(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "run_server_handoff.py"),
                "--host",
                "alice@example.org",
                "--stage",
                "remote-preflight",
                "--run-id",
                "server_unit",
            ],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("[remote-preflight] host=$(hostname)", completed.stdout)
        self.assertIn("MIN_FREE_GB=20.0", completed.stdout)
        self.assertIn("command -v \"$cmd\"", completed.stdout)
        self.assertIn("Python >= 3.10", completed.stdout)
        self.assertIn("disk_free_gb", completed.stdout)
        self.assertIn("required GPU is not available", completed.stdout)
        self.assertIn("required Ollama endpoint is not reachable", completed.stdout)
        self.assertIn("nvidia-smi", completed.stdout)
        self.assertIn("/api/tags", completed.stdout)

    def test_handoff_runner_launch_dry_run_uses_packet_entrypoint(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "run_server_handoff.py"),
                "--host",
                "alice@example.org",
                "--stage",
                "launch",
                "--run-id",
                "server_unit",
                "--background",
            ],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("server_unit_upload_packet", completed.stdout)
        self.assertIn("RUN_PACKET_ON_SERVER.sh background", completed.stdout)
        self.assertNotIn("cd boyuesql_spider2_server", completed.stdout)

    def test_handoff_runner_doctor_dry_run_reextracts_current_packet(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "run_server_handoff.py"),
                "--host",
                "alice@example.org",
                "--stage",
                "doctor",
                "--run-id",
                "server_unit",
            ],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("rm -rf server_unit_upload_packet", completed.stdout)
        self.assertIn("if command -v unzip >/dev/null 2>&1", completed.stdout)
        self.assertIn("python3 -m zipfile -e server_unit_server_upload_packet.zip server_unit_upload_packet", completed.stdout)
        self.assertIn("RUN_PACKET_ON_SERVER.sh doctor", completed.stdout)

    def test_handoff_runner_supervise_dry_run_prints_primary_and_diagnostics_fallback(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "run_server_handoff.py"),
                "--host",
                "alice@example.org",
                "--stage",
                "supervise",
                "--run-id",
                "server_unit",
                "--poll-seconds",
                "5",
                "--max-wait-seconds",
                "10",
                "--remote-min-free-gb",
                "42",
            ],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("supervise primary steps: prepare,remote-preflight,upload,doctor,launch,wait,download,accept", completed.stdout)
        self.assertIn("MIN_FREE_GB=42.0", completed.stdout)
        self.assertIn("RUN_PACKET_ON_SERVER.sh background", completed.stdout)
        self.assertIn("checking result bundle", completed.stdout)
        self.assertIn("supervise fallback if launched run fails before acceptance: diagnostics,download,accept-pending", completed.stdout)
        self.assertIn("RUN_PACKET_ON_SERVER.sh diagnostics", completed.stdout)
        self.assertIn("--allow-pending", completed.stdout)

    def test_supervise_execute_does_not_collect_diagnostics_before_launch(self) -> None:
        import scripts.run_server_handoff as handoff

        calls: list[str] = []

        def record(name: str):
            def _runner(_args: argparse.Namespace) -> None:
                calls.append(name)
                if name == "upload":
                    raise RuntimeError("upload failed")

            return _runner

        original = handoff.STAGE_RUNNERS.copy()
        try:
            for name in ["prepare", "remote-preflight", "upload", "doctor", "launch", "wait", "download", "accept", "diagnostics", "accept-pending"]:
                handoff.STAGE_RUNNERS[name] = record(name)
            args = argparse.Namespace(background=False, execute=True)
            with self.assertRaisesRegex(RuntimeError, "upload failed"):
                handoff.supervise(args)
            self.assertEqual(calls, ["prepare", "remote-preflight", "upload"])
            self.assertFalse(args.background)
        finally:
            handoff.STAGE_RUNNERS.clear()
            handoff.STAGE_RUNNERS.update(original)

    def test_supervise_execute_collects_diagnostics_after_launch_failure(self) -> None:
        import scripts.run_server_handoff as handoff

        calls: list[str] = []

        def record(name: str):
            def _runner(_args: argparse.Namespace) -> None:
                calls.append(name)
                if name == "wait":
                    raise RuntimeError("wait failed")

            return _runner

        original = handoff.STAGE_RUNNERS.copy()
        try:
            for name in ["prepare", "remote-preflight", "upload", "doctor", "launch", "wait", "download", "accept", "diagnostics", "accept-pending"]:
                handoff.STAGE_RUNNERS[name] = record(name)
            args = argparse.Namespace(background=False, execute=True)
            with self.assertRaisesRegex(RuntimeError, "wait failed"):
                handoff.supervise(args)
            self.assertEqual(
                calls,
                ["prepare", "remote-preflight", "upload", "doctor", "launch", "wait", "diagnostics", "download", "accept-pending"],
            )
            self.assertFalse(args.background)
        finally:
            handoff.STAGE_RUNNERS.clear()
            handoff.STAGE_RUNNERS.update(original)

    def test_goal_readiness_audit_script_exists(self) -> None:
        text = (PROJECT_ROOT / "scripts" / "audit_goal_readiness.py").read_text(encoding="utf-8")
        self.assertIn("Full server SOTA matrix executed", text)
        self.assertIn("Server release smoke test", text)
        self.assertIn("Server handoff dry run", text)
        self.assertIn("Server upload packet smoke test", text)
        self.assertIn("Server result acceptance flow smoke test", text)
        self.assertIn("PENDING", text)
        self.assertIn("Completion rule", text)

    def test_linux_shell_scripts_pass_bash_syntax_check(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "check_linux_shell_scripts.py")],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("[shell-syntax] PASS", completed.stdout)


if __name__ == "__main__":
    unittest.main()
