from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.verify_server_release import verify_archive
from scripts.check_linux_shell_scripts import find_bash


KEY_PYTHON_FILES = [
    "ecsql_service.py",
    "scripts/server_preflight.py",
    "scripts/check_linux_shell_scripts.py",
    "scripts/plan_server_matrix.py",
    "scripts/validate_server_matrix.py",
    "scripts/build_server_evidence_report.py",
    "scripts/build_server_result_bundle.py",
    "scripts/verify_server_result_bundle.py",
    "scripts/import_server_result_bundle.py",
    "scripts/build_server_handoff_commands.py",
    "scripts/run_server_handoff.py",
    "scripts/build_dataset_scale_report.py",
    "scripts/build_server_acceptance_contract.py",
    "scripts/build_server_upload_packet.py",
    "scripts/smoke_test_server_upload_packet.py",
    "scripts/smoke_test_server_acceptance_flow.py",
]


def assert_lf_only_shell_scripts(root: Path) -> int:
    checked = 0
    for path in sorted((root / "scripts").glob("*.sh")):
        data = path.read_bytes()
        if b"\r" in data:
            raise ValueError(f"shell script is not LF-only: {path}")
        checked += 1
    if checked == 0:
        raise ValueError(f"no shell scripts found under {root / 'scripts'}")
    return checked


def safe_member_name(raw_name: str) -> str:
    name = raw_name.replace("\\", "/").strip("/")
    path = PurePosixPath(name)
    if not name or path.is_absolute():
        raise ValueError(f"unsafe empty/absolute archive path: {raw_name}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe archive path traversal: {raw_name}")
    if any(":" in part for part in path.parts):
        raise ValueError(f"unsafe archive path contains colon: {raw_name}")
    return path.as_posix()


def extract_release(archive: Path, dest: Path) -> Path:
    root_names: set[str] = set()
    with zipfile.ZipFile(archive, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            safe_name = safe_member_name(info.filename)
            root_names.add(safe_name.split("/", 1)[0])
            target = dest / safe_name
            target.resolve().relative_to(dest.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    if len(root_names) != 1:
        raise ValueError(f"release archive must contain exactly one root directory, found {sorted(root_names)}")
    root = dest / next(iter(root_names))
    if not root.exists():
        raise ValueError(f"release root was not extracted: {root}")
    return root


def run_checked(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
        env=env,
    )
    output = (completed.stdout + completed.stderr).strip()
    if completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(cmd)}\n{output}")
    return output


def bash_python_path() -> str:
    return str(Path(sys.executable)).replace("\\", "/")


def smoke_release(archive: Path, checksum: Path, prefix: str) -> list[str]:
    release_errors = verify_archive(archive, checksum, prefix)
    if release_errors:
        raise RuntimeError("release verification failed:\n" + "\n".join(f"- {error}" for error in release_errors))

    notes: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ecsql_release_smoke_") as tmp:
        root = extract_release(archive, Path(tmp))
        notes.append(f"extracted={root}")
        shell_count = assert_lf_only_shell_scripts(root)
        notes.append(f"lf_only_shell_scripts={shell_count}")
        run_checked([sys.executable, "-m", "py_compile", *KEY_PYTHON_FILES], root)
        notes.append(f"py_compile={len(KEY_PYTHON_FILES)} files")
        shell_output = run_checked([sys.executable, "scripts/check_linux_shell_scripts.py"], root)
        notes.append(shell_output.splitlines()[-1])
        bash_path = find_bash()
        if not bash_path:
            raise RuntimeError("no usable bash executable found for one-click contract smoke")
        contract_env = os.environ.copy()
        contract_env["PYTHON"] = bash_python_path()
        contract_env["RUN_ID"] = "release_smoke_contract"
        contract_output = run_checked(
            [str(bash_path), "scripts/one_click_linux.sh", "contract"],
            root,
            env=contract_env,
        )
        if "[acceptance-contract]" not in contract_output:
            raise RuntimeError("one_click contract did not generate acceptance contract:\n" + contract_output)
        contract_md = root / "artifacts" / "server_runs" / "release_smoke_contract" / "summary" / "server_release_smoke_contract_acceptance_contract.md"
        if not contract_md.exists():
            raise RuntimeError(f"one_click contract did not write expected file: {contract_md}")
        notes.append("one_click_contract=PASS")
        preflight_output = run_checked(
            [
                sys.executable,
                "scripts/server_preflight.py",
                "--project-root",
                str(root),
                "--skip-ollama",
                "--warn-only",
            ],
            root,
        )
        if "[ok] preflight: all checks passed" not in preflight_output:
            raise RuntimeError("server_preflight did not report success:\n" + preflight_output)
        notes.append("preflight=PASS")
    return notes


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract and smoke-test the clean EC-SQL server release package.")
    parser.add_argument("--archive", default=str(PROJECT_ROOT / "artifacts" / "server_release" / "ecsql_spider2_server.zip"))
    parser.add_argument("--checksum", default=str(PROJECT_ROOT / "artifacts" / "server_release" / "ecsql_spider2_server.sha256"))
    parser.add_argument("--prefix", default="ecsql_spider2_server")
    args = parser.parse_args()

    try:
        notes = smoke_release(Path(args.archive), Path(args.checksum), args.prefix)
    except Exception as exc:
        print(f"[release-smoke] FAILED: {exc}", file=sys.stderr)
        return 1
    print("[release-smoke] PASS")
    for note in notes:
        print(f"[release-smoke] {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
