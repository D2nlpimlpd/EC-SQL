from __future__ import annotations

import csv
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.build_server_handoff_commands import build_handoff, markdown as handoff_markdown
from scripts.build_server_acceptance_contract import contract_markdown, contract_payload
from scripts.build_server_submission_manifest import build_manifest, command_plan, markdown, read_checksum
from scripts.build_server_upload_packet import build_packet, verify_packet
from scripts.audit_goal_readiness import check_handoff_dry_run
from scripts.run_server_handoff import expand_stage


class ServerSubmissionManifestTests(unittest.TestCase):
    def test_command_plan_contains_validation_and_audit_steps(self) -> None:
        commands = command_plan("server_unit")
        joined = "\n".join(commands)
        self.assertIn("RUN_ID=server_unit bash scripts/one_click_linux.sh paper-run", joined)
        self.assertIn("RUN_ID=server_unit bash scripts/one_click_linux.sh paper-launch", joined)
        self.assertIn("bash scripts/one_click_linux.sh dataset-report", joined)
        self.assertIn("RUN_ID=server_unit bash scripts/one_click_linux.sh contract", joined)
        self.assertIn("RUN_ID=server_unit bash scripts/one_click_linux.sh validate", joined)
        self.assertIn("RUN_ID=server_unit bash scripts/one_click_linux.sh abstract", joined)
        self.assertIn("RUN_ID=server_unit bash scripts/one_click_linux.sh bundle", joined)
        self.assertIn("RUN_ID=server_unit bash scripts/one_click_linux.sh diagnostics", joined)
        self.assertIn("--stage remote-preflight", joined)
        self.assertIn("--allow-pending", joined)
        self.assertIn("AUDIT_STRICT=1 SERVER_RUN_ID=server_unit", joined)

    def test_acceptance_contract_records_required_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = contract_payload("server_unit", Path(tmp))
            text = contract_markdown(payload)
            self.assertEqual(payload["run_id"], "server_unit")
            self.assertGreaterEqual(payload["dataset_scale_thresholds"]["manifest_rows_min"], 600)
            self.assertGreaterEqual(payload["server_matrix_thresholds"]["baseline_models_min"], 3)
            self.assertIn("summary/server_server_unit_abstract.tex", payload["server_matrix_thresholds"]["required_reports"])
            self.assertTrue(payload["expected_artifacts"])
            self.assertIn("Completion Rule", text)
            self.assertIn("finalize_server_result.py", text)
            self.assertIn("server_unit", text)

    def test_build_manifest_records_release_integrity_and_expected_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "boyuesql_spider2_server.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("placeholder.txt", "placeholder")
            checksum = root / "boyuesql_spider2_server.sha256"
            checksum.write_text("abc123  boyuesql_spider2_server.zip\n", encoding="utf-8")
            manifest = root / "manifest.csv"
            with manifest.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["setting", "instance_id", "db", "engine", "db_path", "question", "external_knowledge"],
                )
                writer.writeheader()
                writer.writerow({"setting": "spider2-lite", "instance_id": "case001", "db": "db1", "engine": "sqlite"})
            payload = build_manifest(
                run_id="server_unit",
                out_dir=root,
                archive=archive,
                checksum=checksum,
                dataset_root=root,
                manifest=manifest,
                sqlite24=root / "missing_sqlite.json",
                dbt68=root / "missing_dbt.json",
                abstract=root / "missing_abstract.tex",
            )
            self.assertEqual(payload["submission"]["release_sha256"], "abc123")
            self.assertEqual(payload["submission"]["run_id"], "server_unit")
            self.assertTrue(payload["expected_artifacts"])
            self.assertIn("server_unit", "\n".join(payload["server_commands"]))
            text = markdown(payload)
            self.assertIn("Server Submission Manifest", text)
            self.assertIn("Spider2 dataset scale report", text)
            self.assertIn("Server acceptance contract", text)
            self.assertIn("Expected Artifacts", text)

    def test_read_checksum_handles_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            info = read_checksum(Path(tmp) / "missing.sha256")
            self.assertEqual(info["sha256"], "")

    def test_handoff_commands_cover_upload_run_download_and_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = build_handoff(
                host="alice@example.org",
                remote_dir="~/boyuesql_run",
                run_id="server_unit",
                archive=root / "boyuesql_spider2_server.zip",
                checksum=root / "boyuesql_spider2_server.sha256",
                local_return_dir=root / "return",
                dataset_root=root / "Spider2",
                manifest=root / "spider2_manifest.csv",
            )
            all_commands = "\n".join(
                command
                for key, value in payload.items()
                if key.endswith("_powershell") or key.endswith("_bash") or key == "status_bash"
                for command in value
            )
            self.assertIn("scp", all_commands)
            self.assertIn("build_dataset_scale_report.py", all_commands)
            self.assertIn("build_server_acceptance_contract.py", all_commands)
            self.assertIn("build_server_upload_packet.py", all_commands)
            self.assertIn("python3 git sha256sum", all_commands)
            self.assertIn("server_unit_server_upload_packet.zip", all_commands)
            self.assertIn("server_unit_server_upload_packet.sha256", all_commands)
            self.assertIn("RUN_PACKET_ON_SERVER.sh doctor", all_commands)
            self.assertIn("RUN_PACKET_ON_SERVER.sh background", all_commands)
            self.assertIn("RUN_PACKET_ON_SERVER.sh foreground", all_commands)
            self.assertIn("RUN_PACKET_ON_SERVER.sh diagnostics", all_commands)
            self.assertIn("server_unit_upload_packet/boyuesql_spider2_server", all_commands)
            self.assertIn("server_server_unit_result_bundle.zip", all_commands)
            self.assertIn("while true", all_commands)
            self.assertIn("server_job.pid", all_commands)
            self.assertIn("verify_server_result_bundle.py", all_commands)
            self.assertIn("import_server_result_bundle.py", all_commands)
            self.assertIn("finalize_server_result.py", all_commands)
            self.assertIn("--allow-pending", all_commands)
            self.assertIn("MIN_FREE_GB", all_commands)
            self.assertIn("disk_free_gb", all_commands)
            self.assertIn("REQUIRE_GPU", all_commands)
            self.assertIn("REQUIRE_OLLAMA", all_commands)
            self.assertIn("cd ~/boyuesql_run", all_commands)
            self.assertNotIn("cd '~/boyuesql_run'", all_commands)
            text = handoff_markdown(payload)
            self.assertIn("BoyueSQL Server Handoff Commands", text)
            self.assertIn("Server Background Run", text)
            self.assertIn("Wait For Background Result Bundle", text)

    def test_handoff_commands_can_require_remote_gpu_ollama_and_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = build_handoff(
                host="alice@example.org",
                remote_dir="~/boyuesql_run",
                run_id="server_unit",
                archive=root / "boyuesql_spider2_server.zip",
                checksum=root / "boyuesql_spider2_server.sha256",
                local_return_dir=root / "return",
                dataset_root=root / "Spider2",
                manifest=root / "spider2_manifest.csv",
                remote_min_free_gb=99,
                remote_require_gpu=True,
                remote_require_ollama=True,
            )
            all_commands = "\n".join(
                command
                for key, value in payload.items()
                if key.endswith("_powershell") or key.endswith("_bash") or key == "status_bash"
                for command in value
            )
            self.assertIn("MIN_FREE_GB=99", all_commands)
            self.assertIn("REQUIRE_GPU=1", all_commands)
            self.assertIn("REQUIRE_OLLAMA=1", all_commands)
            self.assertIn("--remote-min-free-gb 99", all_commands)
            self.assertIn("--remote-require-gpu", all_commands)
            self.assertIn("--remote-require-ollama", all_commands)

    def test_upload_packet_packages_release_and_handoff_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "boyuesql_spider2_server.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("placeholder.txt", "placeholder")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            checksum = root / "boyuesql_spider2_server.sha256"
            checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
            manifest = root / "manifest.csv"
            with manifest.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["setting", "instance_id", "db", "engine", "db_path", "question", "external_knowledge"],
                )
                writer.writeheader()
                writer.writerow({"setting": "spider2-lite", "instance_id": "case001", "db": "db1", "engine": "sqlite"})
            packet, packet_checksum, packet_manifest = build_packet(
                run_id="server_unit",
                out_dir=root,
                archive=archive,
                checksum=checksum,
                dataset_root=root,
                manifest=manifest,
                host="alice@example.org",
                remote_dir="~/boyuesql_run",
                local_return_dir=root / "return",
            )
            self.assertTrue(packet.exists())
            self.assertTrue(packet_checksum.exists())
            self.assertTrue(packet_manifest.exists())
            self.assertEqual(verify_packet(packet, packet_checksum), [])
            with zipfile.ZipFile(packet, "r") as zf:
                names = set(zf.namelist())
            self.assertIn("UPLOAD_PACKET_MANIFEST.json", names)
            self.assertIn("RUN_PACKET_ON_SERVER.sh", names)
            self.assertIn("release/boyuesql_spider2_server.zip", names)
            self.assertIn("docs/server_unit_server_handoff_commands.md", names)
            self.assertIn("docs/server_unit_server_submission_manifest.md", names)
            self.assertIn("docs/server_server_unit_acceptance_contract.md", names)

    def test_upload_packet_rejects_unmanifested_extra_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet = root / "packet.zip"
            with zipfile.ZipFile(packet, "w") as zf:
                zf.writestr(
                    "UPLOAD_PACKET_MANIFEST.json",
                    '{"files":[{"key":"release_archive","path":"release/boyuesql_spider2_server.zip","size_bytes":0,"sha256":""},'
                    '{"key":"release_checksum","path":"release/boyuesql_spider2_server.sha256","size_bytes":0,"sha256":""},'
                    '{"key":"handoff_md","path":"docs/handoff.md","size_bytes":0,"sha256":""},'
                    '{"key":"submission_md","path":"docs/submission.md","size_bytes":0,"sha256":""},'
                    '{"key":"dataset_md","path":"docs/dataset.md","size_bytes":0,"sha256":""},'
                    '{"key":"acceptance_contract_md","path":"docs/contract.md","size_bytes":0,"sha256":""},'
                    '{"key":"run_packet_script","path":"RUN_PACKET_ON_SERVER.sh","size_bytes":0,"sha256":""}]}\n',
                )
                for name in [
                    "release/boyuesql_spider2_server.zip",
                    "release/boyuesql_spider2_server.sha256",
                    "docs/handoff.md",
                    "docs/submission.md",
                    "docs/dataset.md",
                    "docs/contract.md",
                    "RUN_PACKET_ON_SERVER.sh",
                    "artifacts/raw_result.json",
                ]:
                    zf.writestr(name, "")

            errors = verify_packet(packet)

            self.assertTrue(any("unmanifested file" in error for error in errors), errors)

    def test_upload_packet_rejects_manifest_paths_outside_allowed_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet = root / "packet.zip"
            with zipfile.ZipFile(packet, "w") as zf:
                zf.writestr(
                    "UPLOAD_PACKET_MANIFEST.json",
                    '{"files":[{"key":"release_archive","path":"release/boyuesql_spider2_server.zip","size_bytes":0,"sha256":""},'
                    '{"key":"release_checksum","path":"release/boyuesql_spider2_server.sha256","size_bytes":0,"sha256":""},'
                    '{"key":"handoff_md","path":"docs/handoff.md","size_bytes":0,"sha256":""},'
                    '{"key":"submission_md","path":"docs/submission.md","size_bytes":0,"sha256":""},'
                    '{"key":"dataset_md","path":"docs/dataset.md","size_bytes":0,"sha256":""},'
                    '{"key":"acceptance_contract_md","path":"docs/contract.md","size_bytes":0,"sha256":""},'
                    '{"key":"run_packet_script","path":"RUN_PACKET_ON_SERVER.sh","size_bytes":0,"sha256":""},'
                    '{"key":"readiness_audit","path":"datasets/private.sqlite","size_bytes":0,"sha256":""}]}\n',
                )
                for name in [
                    "release/boyuesql_spider2_server.zip",
                    "release/boyuesql_spider2_server.sha256",
                    "docs/handoff.md",
                    "docs/submission.md",
                    "docs/dataset.md",
                    "docs/contract.md",
                    "RUN_PACKET_ON_SERVER.sh",
                    "datasets/private.sqlite",
                ]:
                    zf.writestr(name, "")

            errors = verify_packet(packet)

            self.assertTrue(any("outside allowed" in error for error in errors), errors)

    def test_handoff_runner_stage_expansion_is_safe_for_background_all(self) -> None:
        self.assertEqual(expand_stage("submit", background=True), ["prepare", "remote-preflight", "upload", "doctor", "launch"])
        self.assertEqual(expand_stage("collect", background=True), ["download", "accept"])
        self.assertEqual(expand_stage("collect-diagnostics", background=True), ["diagnostics", "download", "accept-pending"])
        self.assertEqual(
            expand_stage("all", background=False),
            ["prepare", "remote-preflight", "upload", "doctor", "launch", "download", "accept"],
        )
        self.assertEqual(expand_stage("all", background=True), ["prepare", "remote-preflight", "upload", "doctor", "launch"])

    def test_readiness_handoff_dry_run_checks_packet_native_doctor_launch_and_wait(self) -> None:
        check = check_handoff_dry_run("server_unit")
        self.assertEqual(check.status, "PASS", check.evidence)
        self.assertIn("remote-preflight/doctor/launch/diagnostics/wait", check.evidence)
        self.assertIn("upload packet", check.evidence)


if __name__ == "__main__":
    unittest.main()
