from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.audit_goal_readiness import DEFAULT_DATASET_ROOT, DEFAULT_MANIFEST, DEFAULT_RELEASE
from scripts.build_server_handoff_commands import (
    remote_join,
    remote_path_arg,
    result_bundle_name,
    result_checksum_name,
    scp_remote,
)


DEFAULT_CHECKSUM = DEFAULT_RELEASE.with_suffix(".sha256")


def expand_stage(stage: str, background: bool) -> list[str]:
    if stage == "submit":
        return ["prepare", "remote-preflight", "upload", "doctor", "launch"]
    if stage == "collect":
        return ["download", "accept"]
    if stage == "collect-diagnostics":
        return ["diagnostics", "download", "accept-pending"]
    if stage == "all":
        steps = ["prepare", "remote-preflight", "upload", "doctor", "launch"]
        if not background:
            steps.extend(["download", "accept"])
        return steps
    return [stage]


def packet_dir_name(run_id: str) -> str:
    return f"{run_id}_upload_packet"


def packet_remote_dir(args: argparse.Namespace) -> str:
    return remote_join(args.remote_dir, packet_dir_name(args.run_id))


def project_remote_dir(args: argparse.Namespace) -> str:
    return remote_join(packet_remote_dir(args), "boyuesql_spider2_server")


def remote_extract_zip(archive: str, dest: str) -> str:
    archive_arg = remote_path_arg(archive)
    dest_arg = remote_path_arg(dest)
    return f"""if command -v unzip >/dev/null 2>&1; then
  unzip -q {archive_arg} -d {dest_arg}
else
  python3 -m zipfile -e {archive_arg} {dest_arg}
fi"""


def print_cmd(cmd: list[str]) -> None:
    print("[handoff] $ " + " ".join(str(part) for part in cmd))


def run_cmd(cmd: list[str], *, execute: bool, cwd: Path = PROJECT_ROOT) -> None:
    print_cmd(cmd)
    if execute:
        subprocess.run([str(part) for part in cmd], cwd=str(cwd), check=True)


def run_ssh_script(host: str, script: str, *, execute: bool) -> None:
    print(f"[handoff] ssh {host} bash -s <<'EOF'\n{script.rstrip()}\nEOF")
    if execute:
        subprocess.run(["ssh", host, "bash", "-s"], input=script, text=True, check=True)


def prepare(args: argparse.Namespace) -> None:
    run_cmd([sys.executable, "scripts/build_server_release.py"], execute=args.execute)
    run_cmd([sys.executable, "scripts/smoke_test_server_release.py"], execute=args.execute)
    run_cmd(
        [sys.executable, "scripts/build_server_submission_manifest.py", "--run-id", args.run_id],
        execute=args.execute,
    )
    run_cmd(
        [
            sys.executable,
            "scripts/build_server_upload_packet.py",
            "--run-id",
            args.run_id,
            "--archive",
            str(args.archive),
            "--checksum",
            str(args.checksum),
            "--dataset-root",
            str(args.dataset_root),
            "--manifest",
            str(args.manifest),
            "--host",
            args.host,
            "--remote-dir",
            args.remote_dir,
            "--local-return-dir",
            str(args.local_return_dir),
            "--out-dir",
            str(args.packet.parent),
        ],
        execute=args.execute,
    )
    run_cmd(
        [
            sys.executable,
            "scripts/build_server_handoff_commands.py",
            "--host",
            args.host,
            "--remote-dir",
            args.remote_dir,
            "--run-id",
            args.run_id,
            "--local-return-dir",
            str(args.local_return_dir),
        ],
        execute=args.execute,
    )
    if args.execute:
        args.local_return_dir.mkdir(parents=True, exist_ok=True)
    else:
        print(f"[handoff] mkdir local return dir: {args.local_return_dir}")


def remote_preflight(args: argparse.Namespace) -> None:
    remote_dir = remote_path_arg(args.remote_dir)
    min_free_gb = max(0.0, float(args.remote_min_free_gb))
    require_gpu = "1" if args.remote_require_gpu else "0"
    require_ollama = "1" if args.remote_require_ollama else "0"
    script = f"""set -euo pipefail
echo "[remote-preflight] host=$(hostname)"
MIN_FREE_GB={min_free_gb}
REQUIRE_GPU={require_gpu}
REQUIRE_OLLAMA={require_ollama}
export MIN_FREE_GB REQUIRE_GPU REQUIRE_OLLAMA
mkdir -p {remote_dir}
cd {remote_dir}
for cmd in bash python3 git sha256sum; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[remote-preflight] missing required command: $cmd" >&2
    exit 2
  fi
done
python3 - <<'PY'
import os
import shutil
import sys
print("[remote-preflight] python=" + sys.version.split()[0])
if sys.version_info < (3, 10):
    raise SystemExit("Python >= 3.10 is required")
usage = shutil.disk_usage(".")
free_gb = usage.free / (1024 ** 3)
min_free_gb = float(os.environ.get("MIN_FREE_GB", "0") or "0")
print(f"[remote-preflight] disk_free_gb={{free_gb:.2f}}, min_required_gb={{min_free_gb:.2f}}")
if free_gb < min_free_gb:
    raise SystemExit(f"not enough free disk: {{free_gb:.2f}} GiB < {{min_free_gb:.2f}} GiB")
PY
echo "[remote-preflight] disk_table:"
df -h . || true
gpu_available=0
if command -v nvidia-smi >/dev/null 2>&1; then
  echo "[remote-preflight] gpu:"
  if nvidia-smi --query-gpu=name,memory.total --format=csv,noheader; then
    gpu_available=1
  else
    nvidia-smi || true
  fi
else
  echo "[remote-preflight] gpu: nvidia-smi not found"
fi
if [ "${{REQUIRE_GPU}}" = "1" ] && [ "${{gpu_available}}" != "1" ]; then
  echo "[remote-preflight] required GPU is not available" >&2
  exit 3
fi
ollama_reachable=0
if command -v curl >/dev/null 2>&1; then
  if curl -fsS "${{OLLAMA_BASE_URL:-http://localhost:11434}}/api/tags" >/dev/null 2>&1; then
    echo "[remote-preflight] ollama: reachable"
    ollama_reachable=1
  else
    echo "[remote-preflight] ollama: not reachable yet"
  fi
else
  echo "[remote-preflight] curl: not found"
fi
if [ "${{REQUIRE_OLLAMA}}" = "1" ] && [ "${{ollama_reachable}}" != "1" ]; then
  echo "[remote-preflight] required Ollama endpoint is not reachable" >&2
  exit 4
fi
"""
    run_ssh_script(args.host, script, execute=args.execute)


def upload(args: argparse.Namespace) -> None:
    mkdir_script = f"set -euo pipefail\nmkdir -p {remote_path_arg(args.remote_dir)}\n"
    run_ssh_script(args.host, mkdir_script, execute=args.execute)
    run_cmd(
        [
            "scp",
            str(args.packet),
            str(args.packet_checksum),
            scp_remote(args.host, args.remote_dir.rstrip("/") + "/"),
        ],
        execute=args.execute,
    )


def launch(args: argparse.Namespace) -> None:
    mode = "background" if args.background else "foreground"
    skip_models = "1" if args.skip_models else "0"
    script = f"""set -euo pipefail
cd {remote_path_arg(args.remote_dir)}
tr -d '\\r' < {remote_path_arg(args.packet_checksum.name)} | sha256sum -c -
rm -rf {remote_path_arg(packet_dir_name(args.run_id))}
{remote_extract_zip(args.packet.name, packet_dir_name(args.run_id))}
cd {remote_path_arg(packet_dir_name(args.run_id))}
RUN_ID={args.run_id} SKIP_MODELS={skip_models} bash RUN_PACKET_ON_SERVER.sh {mode}
"""
    run_ssh_script(args.host, script, execute=args.execute)


def doctor(args: argparse.Namespace) -> None:
    script = f"""set -euo pipefail
cd {remote_path_arg(args.remote_dir)}
tr -d '\\r' < {remote_path_arg(args.packet_checksum.name)} | sha256sum -c -
rm -rf {remote_path_arg(packet_dir_name(args.run_id))}
{remote_extract_zip(args.packet.name, packet_dir_name(args.run_id))}
cd {remote_path_arg(packet_dir_name(args.run_id))}
RUN_ID={args.run_id} bash RUN_PACKET_ON_SERVER.sh doctor
"""
    run_ssh_script(args.host, script, execute=args.execute)


def status(args: argparse.Namespace) -> None:
    script = f"""set -euo pipefail
cd {remote_path_arg(packet_remote_dir(args))}
RUN_ID={args.run_id} bash RUN_PACKET_ON_SERVER.sh status
RUN_ID={args.run_id} bash RUN_PACKET_ON_SERVER.sh validate
"""
    run_ssh_script(args.host, script, execute=args.execute)


def diagnostics(args: argparse.Namespace) -> None:
    script = f"""set -euo pipefail
cd {remote_path_arg(packet_remote_dir(args))}
RUN_ID={args.run_id} bash RUN_PACKET_ON_SERVER.sh diagnostics
"""
    run_ssh_script(args.host, script, execute=args.execute)


def resume(args: argparse.Namespace) -> None:
    script = f"""set -euo pipefail
cd {remote_path_arg(packet_remote_dir(args))}
RUN_ID={args.run_id} bash RUN_PACKET_ON_SERVER.sh resume
"""
    run_ssh_script(args.host, script, execute=args.execute)


def wait(args: argparse.Namespace) -> None:
    project_remote = project_remote_dir(args)
    summary_remote = remote_join(
        project_remote,
        "artifacts",
        "server_runs",
        args.run_id,
        "summary",
    )
    bundle = remote_join(summary_remote, result_bundle_name(args.run_id))
    checksum = remote_join(summary_remote, result_checksum_name(args.run_id))
    run_dir = remote_join(project_remote, "artifacts", "server_runs", args.run_id)
    project_dir = remote_path_arg(project_remote)
    poll = max(5, int(args.poll_seconds))
    max_wait = max(0, int(args.max_wait_seconds))
    script = f"""set -euo pipefail
cd {remote_path_arg(packet_remote_dir(args))}
bundle={remote_path_arg(bundle)}
checksum={remote_path_arg(checksum)}
pid_file={remote_path_arg(remote_join(run_dir, "server_job.pid"))}
log_file={remote_path_arg(remote_join(run_dir, "server_job.log"))}
elapsed=0
while true; do
  echo "[wait] $(date -Is) checking result bundle"
  RUN_ID={args.run_id} bash RUN_PACKET_ON_SERVER.sh status || true
  if [ -s "$bundle" ] && [ -s "$checksum" ]; then
    echo "[wait] result bundle exists; verifying before declaring ready"
    (cd {project_dir} && python3 scripts/verify_server_result_bundle.py "$bundle" --checksum "$checksum" --run-id {args.run_id})
    echo "[wait] verified result bundle is ready: $bundle"
    exit 0
  fi
  if [ -f "$pid_file" ]; then
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
      echo "[wait] background PID $pid is not running and result bundle is absent" >&2
      if [ -f "$log_file" ]; then
        echo "[wait] log tail:" >&2
        tail -n 120 "$log_file" >&2 || true
      fi
      exit 3
    fi
  fi
  if [ {max_wait} -gt 0 ] && [ "$elapsed" -ge {max_wait} ]; then
    echo "[wait] timed out after {max_wait}s without a result bundle" >&2
    exit 124
  fi
  sleep {poll}
  elapsed=$((elapsed + {poll}))
done
"""
    run_ssh_script(args.host, script, execute=args.execute)


def download(args: argparse.Namespace) -> None:
    summary_remote = remote_join(
        packet_remote_dir(args),
        "boyuesql_spider2_server",
        "artifacts",
        "server_runs",
        args.run_id,
        "summary",
    )
    args.local_return_dir.mkdir(parents=True, exist_ok=True)
    bundle = args.local_return_dir / result_bundle_name(args.run_id)
    checksum = args.local_return_dir / result_checksum_name(args.run_id)
    run_cmd(
        ["scp", scp_remote(args.host, remote_join(summary_remote, bundle.name)), str(bundle)],
        execute=args.execute,
    )
    run_cmd(
        ["scp", scp_remote(args.host, remote_join(summary_remote, checksum.name)), str(checksum)],
        execute=args.execute,
    )


def accept(args: argparse.Namespace) -> None:
    bundle = args.local_return_dir / result_bundle_name(args.run_id)
    checksum = args.local_return_dir / result_checksum_name(args.run_id)
    run_cmd(
        [
            sys.executable,
            "scripts/finalize_server_result.py",
            str(bundle),
            "--checksum",
            str(checksum),
            "--run-id",
            args.run_id,
            "--overwrite",
            "--dataset-root",
            str(args.dataset_root),
            "--manifest",
            str(args.manifest),
        ],
        execute=args.execute,
    )


def accept_pending(args: argparse.Namespace) -> None:
    bundle = args.local_return_dir / result_bundle_name(args.run_id)
    checksum = args.local_return_dir / result_checksum_name(args.run_id)
    run_cmd(
        [
            sys.executable,
            "scripts/verify_server_result_bundle.py",
            str(bundle),
            "--checksum",
            str(checksum),
            "--run-id",
            args.run_id,
            "--allow-pending",
        ],
        execute=args.execute,
    )
    run_cmd(
        [
            sys.executable,
            "scripts/import_server_result_bundle.py",
            str(bundle),
            "--checksum",
            str(checksum),
            "--run-id",
            args.run_id,
            "--overwrite",
            "--allow-pending",
        ],
        execute=args.execute,
    )


def supervise(args: argparse.Namespace) -> None:
    """Run a background server job end to end, collecting diagnostics after launch failures."""
    previous_background = args.background
    args.background = True
    primary_steps = ["prepare", "remote-preflight", "upload", "doctor", "launch", "wait", "download", "accept"]
    fallback_steps = ["diagnostics", "download", "accept-pending"]
    try:
        if not args.execute:
            print("[handoff] supervise primary steps: " + ",".join(primary_steps))
            for step in primary_steps:
                STAGE_RUNNERS[step](args)
            print("[handoff] supervise fallback if launched run fails before acceptance: " + ",".join(fallback_steps))
            for step in fallback_steps:
                STAGE_RUNNERS[step](args)
            return

        launched = False
        try:
            for step in primary_steps:
                STAGE_RUNNERS[step](args)
                if step == "launch":
                    launched = True
        except Exception:
            if not launched:
                print(
                    "[handoff] supervised run failed before launch; no server run diagnostics bundle exists yet",
                    file=sys.stderr,
                )
                raise
            print("[handoff] supervised launched run failed before acceptance; collecting diagnostics bundle", file=sys.stderr)
            for step in fallback_steps:
                STAGE_RUNNERS[step](args)
            raise
    finally:
        args.background = previous_background


STAGE_RUNNERS = {
    "prepare": prepare,
    "remote-preflight": remote_preflight,
    "upload": upload,
    "doctor": doctor,
    "launch": launch,
    "status": status,
    "diagnostics": diagnostics,
    "resume": resume,
    "wait": wait,
    "download": download,
    "accept": accept,
    "accept-pending": accept_pending,
    "supervise": supervise,
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run or execute the BoyueSQL server handoff: local package "
            "preparation, upload, launch, result download, and acceptance."
        )
    )
    parser.add_argument("--host", required=True, help="SSH target, e.g. user@host.")
    parser.add_argument("--remote-dir", default="~/boyuesql_spider2_run")
    parser.add_argument("--run-id", default="server_full_spider2")
    parser.add_argument(
        "--stage",
        choices=[
            "prepare",
            "remote-preflight",
            "upload",
            "doctor",
            "launch",
            "status",
            "diagnostics",
            "resume",
            "wait",
            "download",
            "accept",
            "accept-pending",
            "submit",
            "collect",
            "collect-diagnostics",
            "supervise",
            "all",
        ],
        default="submit",
    )
    parser.add_argument("--archive", type=Path, default=DEFAULT_RELEASE)
    parser.add_argument("--checksum", type=Path, default=DEFAULT_CHECKSUM)
    parser.add_argument("--packet", type=Path, default=None, help="Upload packet path. Defaults to release dir/RUN_ID_server_upload_packet.zip.")
    parser.add_argument("--packet-checksum", type=Path, default=None, help="Upload packet checksum. Defaults to PACKET with .sha256 suffix.")
    parser.add_argument("--local-return-dir", type=Path, default=PROJECT_ROOT / "artifacts" / "server_return")
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--background", action="store_true", help="Use paper-launch instead of foreground paper-run.")
    parser.add_argument("--skip-models", action="store_true", help="Do not pull Ollama models during launch.")
    parser.add_argument("--poll-seconds", type=int, default=300, help="Polling interval for --stage wait.")
    parser.add_argument("--max-wait-seconds", type=int, default=0, help="0 means wait indefinitely.")
    parser.add_argument("--remote-min-free-gb", type=float, default=20.0, help="Minimum free GiB required on the remote run directory during remote-preflight.")
    parser.add_argument("--remote-require-gpu", action="store_true", help="Fail remote-preflight if nvidia-smi cannot report a GPU.")
    parser.add_argument("--remote-require-ollama", action="store_true", help="Fail remote-preflight if OLLAMA_BASE_URL /api/tags is unreachable.")
    parser.add_argument("--execute", action="store_true", help="Actually run commands. Omit for dry-run.")
    args = parser.parse_args()
    if args.packet is None:
        args.packet = DEFAULT_RELEASE.parent / f"{args.run_id}_server_upload_packet.zip"
    if args.packet_checksum is None:
        args.packet_checksum = args.packet.with_suffix(".sha256")

    steps = expand_stage(args.stage, args.background)
    print(f"[handoff] stage={args.stage}, steps={','.join(steps)}, execute={args.execute}")
    if args.stage == "all" and args.background:
        print("[handoff] background all stops after launch; run --stage collect after the server job finishes.")
    for step in steps:
        STAGE_RUNNERS[step](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
