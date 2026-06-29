from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.audit_goal_readiness import DEFAULT_DATASET_ROOT, DEFAULT_MANIFEST, DEFAULT_RELEASE


DEFAULT_CHECKSUM = DEFAULT_RELEASE.with_suffix(".sha256")


def ps_quote(path: Path | str) -> str:
    text = str(path).replace("'", "''")
    return f"'{text}'"


def bash_quote(value: str | Path) -> str:
    return shlex.quote(str(value))


SAFE_REMOTE_PATH = re.compile(r"^[A-Za-z0-9_./~+=:-]+$")


def remote_path_arg(path: str) -> str:
    return path if SAFE_REMOTE_PATH.match(path) else bash_quote(path)


def remote_join(remote_dir: str, *parts: str) -> str:
    base = remote_dir.rstrip("/")
    suffix = "/".join(part.strip("/") for part in parts if part)
    return f"{base}/{suffix}" if suffix else base


def scp_remote(host: str, path: str) -> str:
    return f"{host}:{remote_path_arg(path)}"


def extract_zip_commands(archive: str, dest: str) -> list[str]:
    archive_arg = remote_path_arg(archive)
    dest_arg = remote_path_arg(dest)
    return [
        "if command -v unzip >/dev/null 2>&1; then",
        f"  unzip -q {archive_arg} -d {dest_arg}",
        "else",
        f"  python3 -m zipfile -e {archive_arg} {dest_arg}",
        "fi",
    ]


def result_bundle_name(run_id: str) -> str:
    return f"server_{run_id}_result_bundle.zip"


def result_checksum_name(run_id: str) -> str:
    return f"server_{run_id}_result_bundle.sha256"


def build_handoff(
    *,
    host: str,
    remote_dir: str,
    run_id: str,
    archive: Path,
    checksum: Path,
    packet: Path | None = None,
    packet_checksum: Path | None = None,
    local_return_dir: Path,
    dataset_root: Path,
    manifest: Path,
    remote_min_free_gb: float = 20.0,
    remote_require_gpu: bool = False,
    remote_require_ollama: bool = False,
) -> dict[str, Any]:
    release_name = archive.name
    checksum_name = checksum.name
    packet = packet or archive.parent / f"{run_id}_server_upload_packet.zip"
    packet_checksum = packet_checksum or packet.with_suffix(".sha256")
    packet_name = packet.name
    packet_checksum_name = packet_checksum.name
    packet_dir = f"{run_id}_upload_packet"
    packet_remote = remote_join(remote_dir, packet_dir)
    project_remote = remote_join(packet_remote, "boyuesql_spider2_server")
    summary_remote = remote_join(project_remote, "artifacts", "server_runs", run_id, "summary")
    run_remote = remote_join(project_remote, "artifacts", "server_runs", run_id)
    bundle_name = result_bundle_name(run_id)
    bundle_checksum_name = result_checksum_name(run_id)
    local_bundle = local_return_dir / bundle_name
    local_bundle_checksum = local_return_dir / bundle_checksum_name
    require_gpu_flag = " --remote-require-gpu" if remote_require_gpu else ""
    require_ollama_flag = " --remote-require-ollama" if remote_require_ollama else ""

    return {
        "run_id": run_id,
        "host": host,
        "remote_dir": remote_dir,
        "archive": str(archive),
        "checksum": str(checksum),
        "upload_packet": str(packet),
        "upload_packet_checksum": str(packet_checksum),
        "local_return_dir": str(local_return_dir),
        "local_prepare_powershell": [
            "python scripts/build_server_release.py",
            "python scripts/smoke_test_server_release.py",
            f"python scripts/build_dataset_scale_report.py --dataset-root {ps_quote(dataset_root)} --manifest {ps_quote(manifest)}",
            f"python scripts/build_server_acceptance_contract.py --run-id {run_id}",
            f"python scripts/build_server_submission_manifest.py --run-id {run_id}",
            (
                f"python scripts/build_server_upload_packet.py --run-id {run_id} "
                f"--archive {ps_quote(archive)} --checksum {ps_quote(checksum)} "
                f"--dataset-root {ps_quote(dataset_root)} --manifest {ps_quote(manifest)} "
                f"--host {host} --remote-dir {ps_quote(remote_dir)} "
                f"--local-return-dir {ps_quote(local_return_dir)} --out-dir {ps_quote(packet.parent)}"
            ),
            f"New-Item -ItemType Directory -Force {ps_quote(local_return_dir)} | Out-Null",
        ],
        "upload_powershell": [
            f"ssh {host} {ps_quote('mkdir -p ' + remote_path_arg(remote_dir))}",
            f"scp {ps_quote(packet)} {ps_quote(packet_checksum)} {scp_remote(host, remote_dir + '/')}",
        ],
        "remote_preflight_bash": [
            f"MIN_FREE_GB={remote_min_free_gb}",
            f"REQUIRE_GPU={1 if remote_require_gpu else 0}",
            f"REQUIRE_OLLAMA={1 if remote_require_ollama else 0}",
            "export MIN_FREE_GB REQUIRE_GPU REQUIRE_OLLAMA",
            f"mkdir -p {remote_path_arg(remote_dir)}",
            f"cd {remote_path_arg(remote_dir)}",
            "for cmd in bash python3 git sha256sum; do command -v \"$cmd\" >/dev/null || { echo \"missing $cmd\" >&2; exit 2; }; done",
            "python3 - <<'PY'",
            "import os, shutil",
            "import sys",
            "print('python=' + sys.version.split()[0])",
            "assert sys.version_info >= (3, 10), 'Python >= 3.10 is required'",
            "free_gb = shutil.disk_usage('.').free / (1024 ** 3)",
            "min_free_gb = float(os.environ.get('MIN_FREE_GB', '0') or '0')",
            "print(f'disk_free_gb={free_gb:.2f}, min_required_gb={min_free_gb:.2f}')",
            "assert free_gb >= min_free_gb, f'not enough free disk: {free_gb:.2f} GiB < {min_free_gb:.2f} GiB'",
            "PY",
            "df -h .",
            "gpu_available=0; command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=name,memory.total --format=csv,noheader && gpu_available=1 || true",
            "[ \"${REQUIRE_GPU}\" != \"1\" ] || [ \"${gpu_available}\" = \"1\" ] || { echo 'required GPU is not available' >&2; exit 3; }",
            "ollama_reachable=0; command -v curl >/dev/null && curl -fsS \"${OLLAMA_BASE_URL:-http://localhost:11434}/api/tags\" >/dev/null && ollama_reachable=1 && echo 'ollama reachable' || true",
            "[ \"${REQUIRE_OLLAMA}\" != \"1\" ] || [ \"${ollama_reachable}\" = \"1\" ] || { echo 'required Ollama endpoint is not reachable' >&2; exit 4; }",
        ],
        "local_supervised_handoff_powershell": [
            (
                f"python scripts/run_server_handoff.py --host {host} --remote-dir {ps_quote(remote_dir)} "
                f"--run-id {run_id} --stage supervise --packet {ps_quote(packet)} "
                f"--packet-checksum {ps_quote(packet_checksum)} "
                f"--local-return-dir {ps_quote(local_return_dir)} "
                f"--remote-min-free-gb {remote_min_free_gb}{require_gpu_flag}{require_ollama_flag} --execute"
            )
        ],
        "local_resume_handoff_powershell": [
            (
                f"python scripts/run_server_handoff.py --host {host} --remote-dir {ps_quote(remote_dir)} "
                f"--run-id {run_id} --stage resume --packet {ps_quote(packet)} "
                f"--packet-checksum {ps_quote(packet_checksum)} "
                f"--local-return-dir {ps_quote(local_return_dir)} --execute"
            )
        ],
        "server_foreground_bash": [
            f"cd {remote_path_arg(remote_dir)}",
            f"tr -d '\\r' < {remote_path_arg(packet_checksum_name)} | sha256sum -c -",
            f"rm -rf {remote_path_arg(packet_dir)}",
            *extract_zip_commands(packet_name, packet_dir),
            f"cd {remote_path_arg(packet_dir)}",
            f"RUN_ID={bash_quote(run_id)} bash RUN_PACKET_ON_SERVER.sh doctor",
            f"RUN_ID={bash_quote(run_id)} bash RUN_PACKET_ON_SERVER.sh foreground",
        ],
        "server_background_bash": [
            f"cd {remote_path_arg(remote_dir)}",
            f"tr -d '\\r' < {remote_path_arg(packet_checksum_name)} | sha256sum -c -",
            f"rm -rf {remote_path_arg(packet_dir)}",
            *extract_zip_commands(packet_name, packet_dir),
            f"cd {remote_path_arg(packet_dir)}",
            f"RUN_ID={bash_quote(run_id)} bash RUN_PACKET_ON_SERVER.sh doctor",
            f"RUN_ID={bash_quote(run_id)} bash RUN_PACKET_ON_SERVER.sh background",
        ],
        "server_resume_bash": [
            f"cd {remote_path_arg(packet_remote)}",
            f"RUN_ID={bash_quote(run_id)} bash RUN_PACKET_ON_SERVER.sh resume",
        ],
        "status_bash": [
            f"cd {remote_path_arg(packet_remote)}",
            f"RUN_ID={bash_quote(run_id)} bash RUN_PACKET_ON_SERVER.sh status",
            f"RUN_ID={bash_quote(run_id)} bash RUN_PACKET_ON_SERVER.sh validate",
        ],
        "diagnostics_bash": [
            f"cd {remote_path_arg(packet_remote)}",
            f"RUN_ID={bash_quote(run_id)} bash RUN_PACKET_ON_SERVER.sh diagnostics",
        ],
        "wait_bash": [
            f"cd {remote_path_arg(packet_remote)}",
            (
                f"while true; do RUN_ID={bash_quote(run_id)} bash RUN_PACKET_ON_SERVER.sh status || true; "
                f"test -s {remote_path_arg(remote_join(summary_remote, bundle_name))} "
                f"-a -s {remote_path_arg(remote_join(summary_remote, bundle_checksum_name))} && break; "
                f"pid=$(cat {remote_path_arg(remote_join(run_remote, 'server_job.pid'))} 2>/dev/null || true); "
                f"test -n \"$pid\" && kill -0 \"$pid\" 2>/dev/null || "
                f"(tail -n 120 {remote_path_arg(remote_join(run_remote, 'server_job.log'))} 2>/dev/null || true; exit 3); "
                "sleep 300; done"
            ),
        ],
        "download_powershell": [
            f"New-Item -ItemType Directory -Force {ps_quote(local_return_dir)} | Out-Null",
            f"scp {scp_remote(host, remote_join(summary_remote, bundle_name))} {ps_quote(local_bundle)}",
            f"scp {scp_remote(host, remote_join(summary_remote, bundle_checksum_name))} {ps_quote(local_bundle_checksum)}",
        ],
        "local_acceptance_powershell": [
            (
                f"python scripts/finalize_server_result.py {ps_quote(local_bundle)} "
                f"--checksum {ps_quote(local_bundle_checksum)} --run-id {run_id} "
                f"--overwrite --dataset-root {ps_quote(dataset_root)} --manifest {ps_quote(manifest)}"
            ),
        ],
        "local_pending_diagnostics_powershell": [
            (
                f"python scripts/verify_server_result_bundle.py {ps_quote(local_bundle)} "
                f"--checksum {ps_quote(local_bundle_checksum)} --run-id {run_id} --allow-pending"
            ),
            (
                f"python scripts/import_server_result_bundle.py {ps_quote(local_bundle)} "
                f"--checksum {ps_quote(local_bundle_checksum)} --run-id {run_id} --overwrite --allow-pending"
            ),
        ],
    }


def fenced_block(lang: str, commands: list[str]) -> list[str]:
    return [f"```{lang}", *commands, "```"]


def markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# BoyueSQL Server Handoff Commands: {payload['run_id']}",
        "",
        f"- Host: `{payload['host']}`",
        f"- Remote directory: `{payload['remote_dir']}`",
        f"- Local return directory: `{payload['local_return_dir']}`",
        f"- Release archive: `{payload['archive']}`",
        f"- Checksum: `{payload['checksum']}`",
        f"- Upload packet: `{payload['upload_packet']}`",
        f"- Upload packet checksum: `{payload['upload_packet_checksum']}`",
        "",
        "## 1. Local Preparation",
        "",
        *fenced_block("powershell", payload["local_prepare_powershell"]),
        "",
        "## 2. Upload Server Packet",
        "",
        *fenced_block("powershell", payload["upload_powershell"]),
        "",
        "## 3. Remote Preflight",
        "",
        "Run this on the server before a long benchmark to verify shell, Python, checksum tools, disk, GPU visibility, and optional Ollama reachability.",
        "",
        *fenced_block("bash", payload["remote_preflight_bash"]),
        "",
        "## 4. One-Command Supervised Handoff",
        "",
        "This command uploads, launches the background run, waits for the result bundle, finalizes it locally, copies the server-result abstract, and falls back to a diagnostics bundle if the server job fails before acceptance.",
        "",
        *fenced_block("powershell", payload["local_supervised_handoff_powershell"]),
        "",
        "## 5A. Server Foreground Run",
        "",
        *fenced_block("bash", payload["server_foreground_bash"]),
        "",
        "## 5B. Server Background Run",
        "",
        *fenced_block("bash", payload["server_background_bash"]),
        "",
        "## 5C. Resume Interrupted Server Run",
        "",
        "Use these commands only after the packet directory already exists on the server.",
        "",
        *fenced_block("bash", payload["server_resume_bash"]),
        "",
        *fenced_block("powershell", payload["local_resume_handoff_powershell"]),
        "",
        "## 6. Server Status / Validation",
        "",
        *fenced_block("bash", payload["status_bash"]),
        "",
        "## 7. Wait For Background Result Bundle",
        "",
        *fenced_block("bash", payload["wait_bash"]),
        "",
        "## 8. Diagnostics If The Server Job Fails Or Times Out",
        "",
        *fenced_block("bash", payload["diagnostics_bash"]),
        "",
        "## 9. Download Result Bundle",
        "",
        *fenced_block("powershell", payload["download_powershell"]),
        "",
        "## 10. Local Acceptance",
        "",
        *fenced_block("powershell", payload["local_acceptance_powershell"]),
        "",
        "## 11. Local Pending Diagnostics Import",
        "",
        *fenced_block("powershell", payload["local_pending_diagnostics_powershell"]),
        "",
    ]
    return "\n".join(lines)


def write_handoff(payload: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(payload["run_id"])
    json_path = out_dir / f"{run_id}_server_handoff_commands.json"
    md_path = out_dir / f"{run_id}_server_handoff_commands.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(markdown(payload), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate upload/run/download commands for a BoyueSQL Linux server run.")
    parser.add_argument("--host", default="user@server.example.com", help="SSH target, e.g. user@host.")
    parser.add_argument("--remote-dir", default="~/boyuesql_spider2_run")
    parser.add_argument("--run-id", default="server_full_spider2")
    parser.add_argument("--archive", default=str(DEFAULT_RELEASE))
    parser.add_argument("--checksum", default=str(DEFAULT_CHECKSUM))
    parser.add_argument("--packet", default="", help="Server upload packet. Defaults to OUT_DIR/RUN_ID_server_upload_packet.zip.")
    parser.add_argument("--packet-checksum", default="", help="Upload packet checksum. Defaults to PACKET with .sha256 suffix.")
    parser.add_argument("--local-return-dir", default=str(PROJECT_ROOT / "artifacts" / "server_return"))
    parser.add_argument("--dataset-root", default=str(DEFAULT_DATASET_ROOT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--remote-min-free-gb", type=float, default=20.0)
    parser.add_argument("--remote-require-gpu", action="store_true")
    parser.add_argument("--remote-require-ollama", action="store_true")
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "artifacts" / "server_release"))
    args = parser.parse_args()

    payload = build_handoff(
        host=args.host,
        remote_dir=args.remote_dir,
        run_id=args.run_id,
        archive=Path(args.archive),
        checksum=Path(args.checksum),
        packet=Path(args.packet) if args.packet else None,
        packet_checksum=Path(args.packet_checksum) if args.packet_checksum else None,
        local_return_dir=Path(args.local_return_dir),
        dataset_root=Path(args.dataset_root),
        manifest=Path(args.manifest),
        remote_min_free_gb=args.remote_min_free_gb,
        remote_require_gpu=args.remote_require_gpu,
        remote_require_ollama=args.remote_require_ollama,
    )
    json_path, md_path = write_handoff(payload, Path(args.out_dir))
    print(f"[server-handoff] json: {json_path}")
    print(f"[server-handoff] markdown: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
