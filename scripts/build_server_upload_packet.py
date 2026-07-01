from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_dataset_scale_report import build_report, write_report
from scripts.build_server_acceptance_contract import contract_payload, default_out_dir, write_contract
from scripts.build_server_handoff_commands import build_handoff, write_handoff
from scripts.build_server_submission_manifest import write_submission_manifest


DEFAULT_RUN_ID = "server_full_spider2"
DEFAULT_OUT_DIR = PROJECT_ROOT / "artifacts" / "server_release"
DEFAULT_DATASET_ROOT = Path(r"D:\text2sql_datasets\Spider2")
DEFAULT_RELEASE = PROJECT_ROOT / "artifacts" / "server_release" / "ecsql_spider2_server.zip"
DEFAULT_CHECKSUM = PROJECT_ROOT / "artifacts" / "server_release" / "ecsql_spider2_server.sha256"
DEFAULT_MANIFEST = PROJECT_ROOT / "artifacts" / "spider2_manifest.csv"
DEFAULT_DBT68 = PROJECT_ROOT / "artifacts" / "spider2_dbt_llm_edit_dbt68_v10b_full.json"
DEFAULT_SQLITE24 = (
    PROJECT_ROOT
    / "artifacts"
    / "server_runs"
    / "sqlite_llm_server_gold24_v1"
    / "spider2_sqlite_ecsql_ablation_qwen2.5-coder_7b.json"
)
DEFAULT_ABSTRACT = PROJECT_ROOT / "artifacts" / "ecsql_spider2_abstract.tex"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_checksum(path: Path) -> tuple[str, str]:
    parts = path.read_text(encoding="utf-8").strip().split()
    if len(parts) < 2:
        raise ValueError(f"invalid checksum file: {path}")
    return parts[0], parts[1]


def run_packet_script(run_id: str, release_name: str, checksum_name: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

MODE="${{1:-${{PACKET_RUN_MODE:-background}}}}"
RUN_ID="${{RUN_ID:-{run_id}}}"
SKIP_MODELS="${{SKIP_MODELS:-0}}"
RESET_RELEASE="${{RESET_RELEASE:-0}}"
POLL_SECONDS="${{POLL_SECONDS:-300}}"
MAX_WAIT_SECONDS="${{MAX_WAIT_SECONDS:-0}}"
PYTHON="${{PYTHON:-python3}}"
ROOT="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
PROJECT_DIR="${{ROOT}}/ecsql_spider2_server"

cd "${{ROOT}}"

verify_embedded_release() {{
  test -s UPLOAD_PACKET_MANIFEST.json
  test -s release/{release_name}
  test -s release/{checksum_name}
  echo "[packet-run] verifying embedded release"
  (cd release && tr -d '\\r' < {checksum_name} | sha256sum -c -)
}}

extract_zip() {{
  local archive="$1"
  local dest="$2"
  if command -v unzip >/dev/null 2>&1; then
    unzip -q "$archive" -d "$dest"
  else
    "${{PYTHON}}" -m zipfile -e "$archive" "$dest"
  fi
}}

ensure_project() {{
  verify_embedded_release
  if [ "${{RESET_RELEASE}}" = "1" ] || [ ! -d "${{PROJECT_DIR}}" ]; then
    echo "[packet-run] extracting embedded release"
    rm -rf "${{PROJECT_DIR}}"
    extract_zip release/{release_name} .
  else
    echo "[packet-run] using existing release directory: ${{PROJECT_DIR}}"
  fi
  cd "${{PROJECT_DIR}}"
}}

ensure_runtime() {{
  ensure_project
  echo "[packet-run] preflight and Python/dataset setup"
  bash scripts/one_click_linux.sh preflight
  SETUP_SKIP_MODELS=1 bash scripts/one_click_linux.sh setup
}}

ensure_setup() {{
  ensure_runtime
  if [ "${{SKIP_MODELS}}" != "1" ]; then
    bash scripts/one_click_linux.sh models
  fi
}}

wait_for_bundle() {{
  ensure_project
  local summary_dir="artifacts/server_runs/${{RUN_ID}}/summary"
  local bundle="${{summary_dir}}/server_${{RUN_ID}}_result_bundle.zip"
  local checksum="${{summary_dir}}/server_${{RUN_ID}}_result_bundle.sha256"
  local pid_file="artifacts/server_runs/${{RUN_ID}}/server_job.pid"
  local log_file="artifacts/server_runs/${{RUN_ID}}/server_job.log"
  local elapsed=0
  while true; do
    echo "[packet-run] $(date -Is) checking result bundle"
    RUN_ID="${{RUN_ID}}" bash scripts/one_click_linux.sh status || true
    if [ -s "${{bundle}}" ] && [ -s "${{checksum}}" ]; then
      echo "[packet-run] result bundle exists; verifying before declaring ready"
      "${{PYTHON}}" scripts/verify_server_result_bundle.py "${{bundle}}" --checksum "${{checksum}}" --run-id "${{RUN_ID}}"
      echo "[packet-run] verified result bundle is ready: ${{bundle}}"
      return 0
    fi
    if [ -f "${{pid_file}}" ]; then
      local pid
      pid="$(cat "${{pid_file}}" 2>/dev/null || true)"
      if [ -n "${{pid}}" ] && ! kill -0 "${{pid}}" 2>/dev/null; then
        echo "[packet-run] background PID ${{pid}} is not running and bundle is absent" >&2
        tail -n 120 "${{log_file}}" 2>/dev/null || true
        echo "[packet-run] run 'RUN_ID=${{RUN_ID}} bash RUN_PACKET_ON_SERVER.sh diagnostics' to package partial logs/results" >&2
        return 3
      fi
    fi
    if [ "${{MAX_WAIT_SECONDS}}" -gt 0 ] && [ "${{elapsed}}" -ge "${{MAX_WAIT_SECONDS}}" ]; then
      echo "[packet-run] timed out after ${{MAX_WAIT_SECONDS}}s without result bundle" >&2
      echo "[packet-run] run 'RUN_ID=${{RUN_ID}} bash RUN_PACKET_ON_SERVER.sh diagnostics' to package partial logs/results" >&2
      return 124
    fi
    sleep "${{POLL_SECONDS}}"
    elapsed=$((elapsed + POLL_SECONDS))
  done
}}

case "${{MODE}}" in
  doctor)
    ensure_project
    echo "[packet-run] doctor: lightweight server checks without setup/model pulls"
    "${{PYTHON}}" scripts/server_preflight.py --project-root "$(pwd)" --skip-ollama --warn-only
    "${{PYTHON}}" scripts/check_linux_shell_scripts.py
    ;;
  setup)
    ensure_setup
    ;;
  models)
    ensure_runtime
    bash scripts/one_click_linux.sh models
    ;;
  plan)
    ensure_runtime
    RUN_ID="${{RUN_ID}}" bash scripts/one_click_linux.sh plan
    ;;
  foreground|paper-run)
    ensure_setup
    echo "[packet-run] starting foreground paper-run RUN_ID=${{RUN_ID}}"
    RUN_ID="${{RUN_ID}}" bash scripts/one_click_linux.sh paper-run
    ;;
  background|paper-launch)
    ensure_setup
    echo "[packet-run] starting background paper-launch RUN_ID=${{RUN_ID}}"
    RUN_ID="${{RUN_ID}}" bash scripts/one_click_linux.sh paper-launch
    RUN_ID="${{RUN_ID}}" bash scripts/one_click_linux.sh status
    ;;
  resume)
    ensure_runtime
    echo "[packet-run] resuming foreground benchmark RUN_ID=${{RUN_ID}}"
    RUN_ID="${{RUN_ID}}" bash scripts/one_click_linux.sh resume
    ;;
  status)
    ensure_project
    RUN_ID="${{RUN_ID}}" bash scripts/one_click_linux.sh status
    ;;
  summarize)
    ensure_project
    RUN_ID="${{RUN_ID}}" bash scripts/one_click_linux.sh summarize
    ;;
  wait)
    wait_for_bundle
    ;;
  validate)
    ensure_project
    RUN_ID="${{RUN_ID}}" bash scripts/one_click_linux.sh validate
    ;;
  evidence)
    ensure_project
    RUN_ID="${{RUN_ID}}" bash scripts/one_click_linux.sh evidence
    ;;
  abstract)
    ensure_project
    RUN_ID="${{RUN_ID}}" bash scripts/one_click_linux.sh abstract
    ;;
  bundle)
    ensure_project
    RUN_ID="${{RUN_ID}}" bash scripts/one_click_linux.sh bundle
    ;;
  diagnostics)
    ensure_project
    RUN_ID="${{RUN_ID}}" bash scripts/one_click_linux.sh diagnostics
    ;;
  audit)
    ensure_project
    AUDIT_STRICT="${{AUDIT_STRICT:-1}}" SERVER_RUN_ID="${{RUN_ID}}" bash scripts/one_click_linux.sh audit
    ;;
  *)
    echo "Usage: bash RUN_PACKET_ON_SERVER.sh [doctor|setup|models|plan|foreground|background|resume|status|summarize|wait|validate|evidence|abstract|bundle|diagnostics|audit]" >&2
    exit 2
    ;;
esac
"""


def write_run_packet_script(path: Path, run_id: str, release_name: str, checksum_name: str) -> Path:
    path.write_text(run_packet_script(run_id, release_name, checksum_name), encoding="utf-8", newline="\n")
    return path


def refresh_supporting_files(
    *,
    run_id: str,
    host: str,
    remote_dir: str,
    out_dir: Path,
    archive: Path,
    checksum: Path,
    dataset_root: Path,
    manifest: Path,
    local_return_dir: Path,
) -> dict[str, Path]:
    report = build_report(dataset_root, manifest)
    dataset_json = PROJECT_ROOT / "artifacts" / "dataset_scale_report.json"
    dataset_md = PROJECT_ROOT / "artifacts" / "dataset_scale_report.md"
    write_report(report, dataset_json, dataset_md)

    contract_dir = default_out_dir(run_id)
    contract_json = contract_dir / f"server_{run_id}_acceptance_contract.json"
    contract_md = contract_dir / f"server_{run_id}_acceptance_contract.md"
    write_contract(contract_payload(run_id, contract_dir), contract_json, contract_md)

    submission_json, submission_md = write_submission_manifest(
        run_id=run_id,
        out_dir=out_dir,
        archive=archive,
        checksum=checksum,
        dataset_root=dataset_root,
        manifest=manifest,
        sqlite24=DEFAULT_SQLITE24,
        dbt68=DEFAULT_DBT68,
        abstract=DEFAULT_ABSTRACT,
    )
    packet = out_dir / f"{run_id}_server_upload_packet.zip"
    handoff_payload = build_handoff(
        host=host,
        remote_dir=remote_dir,
        run_id=run_id,
        archive=archive,
        checksum=checksum,
        packet=packet,
        packet_checksum=packet.with_suffix(".sha256"),
        local_return_dir=local_return_dir,
        dataset_root=dataset_root,
        manifest=manifest,
    )
    handoff_json, handoff_md = write_handoff(handoff_payload, out_dir)
    run_script = write_run_packet_script(
        out_dir / f"{run_id}_RUN_PACKET_ON_SERVER.sh",
        run_id,
        archive.name,
        checksum.name,
    )

    readiness = PROJECT_ROOT / "artifacts" / "goal_readiness_audit.md"
    return {
        "release_archive": archive,
        "release_checksum": checksum,
        "handoff_json": handoff_json,
        "handoff_md": handoff_md,
        "submission_json": submission_json,
        "submission_md": submission_md,
        "dataset_json": dataset_json,
        "dataset_md": dataset_md,
        "acceptance_contract_json": contract_json,
        "acceptance_contract_md": contract_md,
        "run_packet_script": run_script,
        "readiness_audit": readiness,
    }


def packet_layout(files: dict[str, Path]) -> dict[str, str]:
    return {
        "release_archive": f"release/{files['release_archive'].name}",
        "release_checksum": f"release/{files['release_checksum'].name}",
        "handoff_json": f"docs/{files['handoff_json'].name}",
        "handoff_md": f"docs/{files['handoff_md'].name}",
        "submission_json": f"docs/{files['submission_json'].name}",
        "submission_md": f"docs/{files['submission_md'].name}",
        "dataset_json": "docs/dataset_scale_report.json",
        "dataset_md": "docs/dataset_scale_report.md",
        "acceptance_contract_json": f"docs/{files['acceptance_contract_json'].name}",
        "acceptance_contract_md": f"docs/{files['acceptance_contract_md'].name}",
        "run_packet_script": "RUN_PACKET_ON_SERVER.sh",
        "readiness_audit": "docs/goal_readiness_audit.md",
    }


def build_manifest_payload(run_id: str, files: dict[str, Path], layout: dict[str, str]) -> dict[str, Any]:
    rows = []
    for key, source in files.items():
        if key == "readiness_audit" and not source.exists():
            continue
        rel = layout[key]
        rows.append(
            {
                "key": key,
                "path": rel,
                "source": str(source),
                "size_bytes": source.stat().st_size,
                "sha256": sha256_file(source),
            }
        )
    release_hash, release_name = read_checksum(files["release_checksum"])
    return {
        "packet_name": "EC-SQL Spider2 server upload packet",
        "run_id": run_id,
        "release_archive": files["release_archive"].name,
        "release_sha256": release_hash,
        "release_checksum_archive_name": release_name,
        "files": rows,
        "instructions": [
            "Upload this packet zip and its .sha256 file to the server run directory.",
            "Verify the packet checksum, unpack it, then run: bash RUN_PACKET_ON_SERVER.sh background",
            "If the run is interrupted, continue it with: RUN_ID=<RUN_ID> bash RUN_PACKET_ON_SERVER.sh resume",
            "Follow docs/*_server_handoff_commands.md for the exact upload/run/wait/download/acceptance commands.",
            "After the server returns server_<RUN_ID>_result_bundle.zip, verify and import it locally before claiming completion.",
        ],
    }


def build_packet(
    *,
    run_id: str,
    out_dir: Path,
    archive: Path,
    checksum: Path,
    dataset_root: Path,
    manifest: Path,
    host: str,
    remote_dir: str,
    local_return_dir: Path,
) -> tuple[Path, Path, Path]:
    if not archive.exists():
        raise FileNotFoundError(f"missing release archive: {archive}")
    if not checksum.exists():
        raise FileNotFoundError(f"missing release checksum: {checksum}")
    out_dir.mkdir(parents=True, exist_ok=True)
    files = refresh_supporting_files(
        run_id=run_id,
        host=host,
        remote_dir=remote_dir,
        out_dir=out_dir,
        archive=archive,
        checksum=checksum,
        dataset_root=dataset_root,
        manifest=manifest,
        local_return_dir=local_return_dir,
    )
    layout = packet_layout(files)
    payload = build_manifest_payload(run_id, files, layout)

    packet = out_dir / f"{run_id}_server_upload_packet.zip"
    if packet.exists():
        packet.unlink()
    with zipfile.ZipFile(packet, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("UPLOAD_PACKET_MANIFEST.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        for row in payload["files"]:
            source = Path(str(row["source"]))
            zf.write(source, str(row["path"]))
    packet_checksum = packet.with_suffix(".sha256")
    packet_checksum.write_text(f"{sha256_file(packet)}  {packet.name}\n", encoding="utf-8", newline="\n")
    manifest_path = out_dir / f"{run_id}_server_upload_packet_manifest.json"
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return packet, packet_checksum, manifest_path


def verify_packet(packet: Path, checksum: Path | None = None) -> list[str]:
    errors: list[str] = []
    if not packet.exists():
        return [f"missing upload packet: {packet}"]
    if checksum is not None:
        if not checksum.exists():
            errors.append(f"missing checksum: {checksum}")
        else:
            expected_hash, expected_name = read_checksum(checksum)
            actual_hash = sha256_file(packet)
            if expected_hash != actual_hash:
                errors.append(f"packet checksum mismatch: expected {expected_hash}, got {actual_hash}")
            if expected_name != packet.name:
                errors.append(f"checksum archive name mismatch: expected {expected_name}, got {packet.name}")

    try:
        with zipfile.ZipFile(packet, "r") as zf:
            names = {name.replace("\\", "/").strip("/") for name in zf.namelist()}
            if "UPLOAD_PACKET_MANIFEST.json" not in names:
                return errors + ["missing UPLOAD_PACKET_MANIFEST.json"]
            payload = json.loads(zf.read("UPLOAD_PACKET_MANIFEST.json").decode("utf-8"))
            files = payload.get("files") if isinstance(payload, dict) else None
            if not isinstance(files, list) or not files:
                errors.append("packet manifest contains no files")
                return errors
            allowed_keys = {
                "release_archive",
                "release_checksum",
                "handoff_json",
                "handoff_md",
                "submission_json",
                "submission_md",
                "dataset_json",
                "dataset_md",
                "acceptance_contract_json",
                "acceptance_contract_md",
                "run_packet_script",
                "readiness_audit",
            }
            required_keys = {
                "release_archive",
                "release_checksum",
                "handoff_md",
                "submission_md",
                "dataset_md",
                "acceptance_contract_md",
                "run_packet_script",
            }
            keys = {str(row.get("key") or "") for row in files if isinstance(row, dict)}
            unknown_keys = sorted(keys - allowed_keys)
            if unknown_keys:
                errors.append(f"packet contains unexpected manifest key(s): {', '.join(unknown_keys)}")
            missing_keys = sorted(required_keys - keys)
            if missing_keys:
                errors.append(f"packet missing required key(s): {', '.join(missing_keys)}")
            manifest_paths: set[str] = {"UPLOAD_PACKET_MANIFEST.json"}
            for row in files:
                if not isinstance(row, dict):
                    errors.append("packet manifest contains a non-object file row")
                    continue
                rel = str(row.get("path") or "").replace("\\", "/").strip("/")
                if rel:
                    manifest_paths.add(rel)
                    if rel != "RUN_PACKET_ON_SERVER.sh" and not (
                        rel.startswith("release/") or rel.startswith("docs/")
                    ):
                        errors.append(f"packet file outside allowed release/docs/root-script layout: {rel}")
                if not rel or rel not in names:
                    errors.append(f"packet file missing: {rel or '(empty path)'}")
                    continue
                data = zf.read(rel)
                expected_size = int(row.get("size_bytes") or -1)
                expected_sha = str(row.get("sha256") or "")
                if expected_size >= 0 and len(data) != expected_size:
                    errors.append(f"size mismatch for {rel}: expected {expected_size}, got {len(data)}")
                if expected_sha and hashlib.sha256(data).hexdigest() != expected_sha:
                    errors.append(f"sha256 mismatch for {rel}")
            unexpected_names = sorted(name for name in names - manifest_paths if name)
            if unexpected_names:
                errors.append(f"packet contains unmanifested file(s): {', '.join(unexpected_names[:5])}")
            return errors
    except zipfile.BadZipFile:
        return errors + [f"invalid zip archive: {packet}"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or verify a single upload packet for a EC-SQL server run.")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--archive", default=str(DEFAULT_RELEASE))
    parser.add_argument("--checksum", default=str(DEFAULT_CHECKSUM))
    parser.add_argument("--dataset-root", default=str(DEFAULT_DATASET_ROOT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--host", default="user@server.example.com")
    parser.add_argument("--remote-dir", default="~/ecsql_spider2_run")
    parser.add_argument("--local-return-dir", default=str(PROJECT_ROOT / "artifacts" / "server_return"))
    parser.add_argument("--verify", action="store_true", help="Verify an existing upload packet instead of building it.")
    parser.add_argument("--packet", default="", help="Packet path for --verify. Defaults to OUT_DIR/RUN_ID_server_upload_packet.zip.")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    packet = Path(args.packet) if args.packet else out_dir / f"{args.run_id}_server_upload_packet.zip"
    checksum = packet.with_suffix(".sha256")
    if args.verify:
        errors = verify_packet(packet, checksum if checksum.exists() else None)
        if errors:
            print("[server-upload-packet] FAILED", file=sys.stderr)
            for error in errors:
                print(f"- {error}", file=sys.stderr)
            return 2
        print(f"[server-upload-packet] PASS: {packet}")
        return 0

    packet, packet_checksum, manifest_path = build_packet(
        run_id=args.run_id,
        out_dir=out_dir,
        archive=Path(args.archive),
        checksum=Path(args.checksum),
        dataset_root=Path(args.dataset_root),
        manifest=Path(args.manifest),
        host=args.host,
        remote_dir=args.remote_dir,
        local_return_dir=Path(args.local_return_dir),
    )
    errors = verify_packet(packet, packet_checksum)
    if errors:
        print("[server-upload-packet] FAILED after build", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 2
    print(f"[server-upload-packet] packet: {packet}")
    print(f"[server-upload-packet] checksum: {packet_checksum}")
    print(f"[server-upload-packet] manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
