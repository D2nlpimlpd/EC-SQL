from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_server_upload_packet import verify_packet
from scripts.check_linux_shell_scripts import find_bash
from scripts.smoke_test_server_release import run_checked, safe_member_name, smoke_release


def extract_packet(packet: Path, dest: Path) -> Path:
    with zipfile.ZipFile(packet, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            safe_name = safe_member_name(info.filename)
            target = dest / safe_name
            target.resolve().relative_to(dest.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    manifest = dest / "UPLOAD_PACKET_MANIFEST.json"
    if not manifest.exists():
        raise ValueError(f"packet manifest was not extracted: {manifest}")
    return dest


def write_fake_pending_run(project_dir: Path, run_id: str) -> Path:
    run_dir = project_dir / "artifacts" / "server_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "server_job.log").write_text("packet smoke pending run\nmodel job stopped before bundle\n", encoding="utf-8")
    (run_dir / "run_config.env").write_text(f"RUN_ID={run_id}\n", encoding="utf-8")
    (run_dir / "planned_steps.txt").write_text("packet-smoke-diagnostics\n", encoding="utf-8")
    (run_dir / "expected_artifacts.csv").write_text(
        "enabled,artifact,stage,suite\n"
        "1,missing_sqlite_baseline.json,sqlite_baselines,spider2-sqlite\n",
        encoding="utf-8",
    )
    return run_dir


def smoke_upload_packet(
    packet: Path,
    checksum: Path | None = None,
    *,
    run_doctor: bool = False,
    run_diagnostics: bool = False,
) -> list[str]:
    errors = verify_packet(packet, checksum if checksum and checksum.exists() else None)
    if errors:
        raise RuntimeError("upload packet verification failed:\n" + "\n".join(f"- {error}" for error in errors))

    notes: list[str] = []
    notes.append("packet_manifest_layout=PASS")
    with tempfile.TemporaryDirectory(prefix="boyuesql_packet_smoke_") as tmp:
        root = extract_packet(packet, Path(tmp) / "packet")
        notes.append(f"packet_extracted={root}")
        manifest = json.loads((root / "UPLOAD_PACKET_MANIFEST.json").read_text(encoding="utf-8"))
        run_id = str(manifest.get("run_id") or "")
        notes.append(f"run_id={run_id or '(missing)'}")
        run_script = root / "RUN_PACKET_ON_SERVER.sh"
        if not run_script.exists():
            raise RuntimeError("packet does not contain RUN_PACKET_ON_SERVER.sh")
        run_script_text = run_script.read_text(encoding="utf-8")
        required_script_terms = [
            "paper-launch",
            "paper-run",
            "sha256sum -c",
            "one_click_linux.sh setup",
            "RESET_RELEASE",
            "doctor:",
            "--skip-ollama",
            "wait_for_bundle",
            "one_click_linux.sh bundle",
            "one_click_linux.sh diagnostics",
            "one_click_linux.sh audit",
            "extract_zip()",
            "-m zipfile -e",
        ]
        missing_script_terms = [term for term in required_script_terms if term not in run_script_text]
        if missing_script_terms:
            raise RuntimeError(f"RUN_PACKET_ON_SERVER.sh is incomplete; missing {missing_script_terms}")
        if "rm -rf boyuesql_spider2_server" in run_script_text:
            raise RuntimeError("RUN_PACKET_ON_SERVER.sh contains an unconditional destructive release reset")
        bash_path = find_bash()
        if bash_path:
            run_checked([str(bash_path), "-n", str(run_script)], root)
            notes.append("packet_run_script_bash_syntax=PASS")
            if run_doctor or run_diagnostics:
                env = dict(os.environ)
                env["PYTHON"] = sys.executable
                run_checked([str(bash_path), str(run_script), "doctor"], root, env=env)
                notes.append("packet_run_script_doctor=PASS")
            if run_diagnostics:
                project_dir = root / "boyuesql_spider2_server"
                if not run_id:
                    raise RuntimeError("cannot run diagnostics smoke because packet manifest has no run_id")
                write_fake_pending_run(project_dir, run_id)
                env = dict(os.environ)
                env["PYTHON"] = sys.executable
                env["RUN_ID"] = run_id
                run_checked([str(bash_path), str(run_script), "diagnostics"], root, env=env)
                summary_dir = project_dir / "artifacts" / "server_runs" / run_id / "summary"
                expected = [
                    summary_dir / f"server_{run_id}_diagnostics.json",
                    summary_dir / f"server_{run_id}_diagnostics.md",
                    summary_dir / f"server_{run_id}_result_bundle.zip",
                    summary_dir / f"server_{run_id}_result_bundle.sha256",
                ]
                missing = [str(path) for path in expected if not path.exists() or path.stat().st_size <= 0]
                if missing:
                    raise RuntimeError(f"diagnostics smoke did not create expected files: {missing}")
                notes.append("packet_run_script_diagnostics=PASS")
        else:
            notes.append("packet_run_script_bash_syntax=SKIPPED(no bash)")
            if run_doctor or run_diagnostics:
                raise RuntimeError("cannot run packet doctor/diagnostics smoke because no usable bash executable was found")
        handoff_docs = sorted((root / "docs").glob("*_server_handoff_commands.md"))
        if not handoff_docs:
            raise RuntimeError("packet does not contain a server handoff command sheet")
        handoff_text = handoff_docs[0].read_text(encoding="utf-8")
        required_handoff_terms = [
            "_server_upload_packet.zip",
            "_server_upload_packet.sha256",
            "_upload_packet",
            "RUN_PACKET_ON_SERVER.sh doctor",
            "RUN_PACKET_ON_SERVER.sh background",
            "RUN_PACKET_ON_SERVER.sh status",
            "RUN_PACKET_ON_SERVER.sh diagnostics",
            "boyuesql_spider2_server/artifacts/server_runs",
        ]
        missing_terms = [term for term in required_handoff_terms if term not in handoff_text]
        if missing_terms:
            raise RuntimeError(f"handoff command sheet is not packet-native; missing {missing_terms}")
        notes.append("packet_handoff_doc=PASS")

        release_archive = root / "release" / "boyuesql_spider2_server.zip"
        release_checksum = root / "release" / "boyuesql_spider2_server.sha256"
        if not release_archive.exists() or not release_checksum.exists():
            raise RuntimeError("packet does not contain release archive and checksum under release/")
        for note in smoke_release(release_archive, release_checksum, "boyuesql_spider2_server"):
            notes.append(f"release_{note}")
    return notes


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end smoke-test a BoyueSQL server upload packet.")
    parser.add_argument("--packet", default=str(PROJECT_ROOT / "artifacts" / "server_release" / "server_full_spider2_server_upload_packet.zip"))
    parser.add_argument("--checksum", default="", help="Defaults to PACKET with .sha256 suffix.")
    parser.add_argument(
        "--run-doctor",
        action="store_true",
        help="Also execute RUN_PACKET_ON_SERVER.sh doctor after extracting the packet.",
    )
    parser.add_argument(
        "--run-diagnostics",
        action="store_true",
        help="Also create a fake pending run and execute RUN_PACKET_ON_SERVER.sh diagnostics.",
    )
    args = parser.parse_args()

    packet = Path(args.packet)
    checksum = Path(args.checksum) if args.checksum else packet.with_suffix(".sha256")
    try:
        notes = smoke_upload_packet(packet, checksum, run_doctor=args.run_doctor, run_diagnostics=args.run_diagnostics)
    except Exception as exc:
        print(f"[packet-smoke] FAILED: {exc}", file=sys.stderr)
        return 1
    print("[packet-smoke] PASS")
    for note in notes:
        print(f"[packet-smoke] {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
