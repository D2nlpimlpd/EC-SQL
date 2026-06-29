from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.audit_goal_readiness import Check, check_server_matrix, check_server_result_abstract


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_dir_for(run_id: str, out_dir: str = "") -> Path:
    return Path(out_dir) if out_dir else PROJECT_ROOT / "artifacts" / "server_runs" / run_id


def expected_artifact_names(run_dir: Path) -> list[str]:
    path = run_dir / "expected_artifacts.csv"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return [row["artifact"] for row in rows if row.get("enabled") == "1" and row.get("artifact")]


def candidate_files(run_dir: Path, run_id: str) -> list[Path]:
    summary = run_dir / "summary"
    patterns = [
        "expected_artifacts.csv",
        "server_matrix_plan.md",
        "server_matrix_plan.json",
        "server_matrix_coverage.md",
        "server_matrix_coverage.json",
        "run_config.env",
        "planned_steps.txt",
        "python_version.txt",
        "launch.env",
        "server_job.pid",
        "server_job.marker",
        "server_job.log",
        "external_baselines_template.csv",
        "*.json",
        "*_registered.json",
        "spider2_external_baseline_*.json",
    ]
    files: list[Path] = []
    for pattern in patterns:
        files.extend(path for path in run_dir.glob(pattern) if path.is_file())
    if summary.exists():
        for pattern in (
            f"server_{run_id}.csv",
            f"server_{run_id}_cases.csv",
            f"server_{run_id}.md",
            f"server_{run_id}_failures.csv",
            f"server_{run_id}_failures_cases.csv",
            f"server_{run_id}_failures.md",
            f"server_{run_id}_evidence.csv",
            f"server_{run_id}_evidence.md",
            f"server_{run_id}_results_snippet.tex",
            f"server_{run_id}_abstract.tex",
            f"server_{run_id}_dataset_scale_report.json",
            f"server_{run_id}_dataset_scale_report.md",
            f"server_{run_id}_acceptance_contract.json",
            f"server_{run_id}_acceptance_contract.md",
            f"server_{run_id}_diagnostics.json",
            f"server_{run_id}_diagnostics.md",
            f"server_{run_id}_completion_certificate.json",
        ):
            files.extend(path for path in summary.glob(pattern) if path.is_file())
    return sorted({path.resolve() for path in files})


def relative_to_run(path: Path, run_dir: Path) -> str:
    return path.resolve().relative_to(run_dir.resolve()).as_posix()


def missing_expected(run_dir: Path) -> list[str]:
    missing: list[str] = []
    for name in expected_artifact_names(run_dir):
        path = run_dir / name
        if not path.exists() or path.stat().st_size == 0:
            missing.append(name)
    return missing


def completion_status(checks: Iterable[Check]) -> str:
    statuses = {check.status for check in checks}
    if "FAIL" in statuses:
        return "FAIL"
    if "PENDING" in statuses:
        return "PENDING"
    return "PASS"


def check_expected_artifacts(run_id: str, run_dir: Path) -> Check:
    missing = missing_expected(run_dir)
    if missing:
        return Check(
            "Server expected artifacts complete",
            "FAIL",
            f"missing expected artifact(s) for {run_id}: {', '.join(missing[:10])}",
        )
    expected = expected_artifact_names(run_dir)
    if not expected:
        return Check(
            "Server expected artifacts complete",
            "PENDING",
            f"no enabled expected artifacts found for {run_id}",
        )
    return Check(
        "Server expected artifacts complete",
        "PASS",
        f"{len(expected)} enabled expected artifact(s) present for {run_id}",
    )


def read_key_value_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not raw or raw.lstrip().startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def launch_marker_payload(run_dir: Path) -> dict[str, str]:
    return read_key_value_file(run_dir / "server_job.marker")


def check_launch_marker(run_id: str, run_dir: Path) -> Check:
    marker_path = run_dir / "server_job.marker"
    launch_indicators = [run_dir / "launch.env", run_dir / "server_job.pid"]
    if not marker_path.exists():
        if any(path.exists() for path in launch_indicators):
            return Check(
                "Server launch marker",
                "FAIL",
                f"background launch artifact exists but marker is missing: {marker_path}",
            )
        return Check(
            "Server launch marker",
            "PASS",
            "no background launch marker required for foreground/direct run",
        )

    marker = launch_marker_payload(run_dir)
    missing = [key for key in ("RUN_ID", "RUN_MARKER_ID", "RUN_STARTED_AT_EPOCH") if not marker.get(key)]
    if missing:
        return Check("Server launch marker", "FAIL", f"marker missing key(s): {', '.join(missing)}")
    if marker.get("RUN_ID") != run_id:
        return Check(
            "Server launch marker",
            "FAIL",
            f"marker RUN_ID mismatch: expected {run_id}, got {marker.get('RUN_ID')}",
        )
    return Check(
        "Server launch marker",
        "PASS",
        f"marker={marker.get('RUN_MARKER_ID')} started_at_epoch={marker.get('RUN_STARTED_AT_EPOCH')}",
    )


def write_completion_certificate(
    run_id: str,
    run_dir: Path,
    matrix_check: Check,
    abstract_check: Check,
    expected_check: Check,
    marker_check: Check,
) -> Path:
    summary_dir = run_dir / "summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "completion_status": completion_status([matrix_check, abstract_check, expected_check, marker_check]),
        "matrix_check": asdict(matrix_check),
        "server_result_abstract_check": asdict(abstract_check),
        "expected_artifacts_check": asdict(expected_check),
        "launch_marker_check": asdict(marker_check),
        "launch_marker": launch_marker_payload(run_dir),
        "missing_expected_artifacts": missing_expected(run_dir),
    }
    path = summary_dir / f"server_{run_id}_completion_certificate.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def bundle_manifest(
    run_id: str,
    run_dir: Path,
    files: Iterable[Path],
    matrix_check: Check,
    abstract_check: Check,
    expected_check: Check,
    marker_check: Check,
) -> dict[str, object]:
    file_rows = []
    for path in files:
        file_rows.append(
            {
                "path": relative_to_run(path, run_dir),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "validation": asdict(matrix_check),
        "abstract_validation": asdict(abstract_check),
        "expected_artifacts_validation": asdict(expected_check),
        "launch_marker_validation": asdict(marker_check),
        "launch_marker": launch_marker_payload(run_dir),
        "completion_status": completion_status([matrix_check, abstract_check, expected_check, marker_check]),
        "missing_expected_artifacts": missing_expected(run_dir),
        "files": file_rows,
    }


def build_bundle(run_id: str, out_dir: str = "", allow_pending: bool = False) -> tuple[Path, Path, Check]:
    run_dir = run_dir_for(run_id, out_dir)
    if not run_dir.exists():
        raise FileNotFoundError(f"missing server run directory: {run_dir}")
    check = check_server_matrix(run_id, run_dir)
    abstract_check = check_server_result_abstract(run_id, run_dir)
    expected_check = check_expected_artifacts(run_id, run_dir)
    marker_check = check_launch_marker(run_id, run_dir)
    if not allow_pending:
        for required_check in (check, abstract_check, expected_check, marker_check):
            if required_check.status != "PASS":
                raise RuntimeError(required_check.evidence)
    summary_dir = run_dir / "summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    write_completion_certificate(run_id, run_dir, check, abstract_check, expected_check, marker_check)
    files = candidate_files(run_dir, run_id)
    manifest = bundle_manifest(run_id, run_dir, files, check, abstract_check, expected_check, marker_check)
    manifest_path = summary_dir / f"server_{run_id}_result_bundle_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    archive = summary_dir / f"server_{run_id}_result_bundle.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(manifest_path, manifest_path.relative_to(run_dir))
        for path in files:
            if path == archive or path == manifest_path:
                continue
            zf.write(path, path.relative_to(run_dir))
    checksum = archive.with_suffix(".sha256")
    checksum.write_text(f"{sha256(archive)}  {archive.name}\n", encoding="utf-8", newline="\n")
    return archive, checksum, check


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a returnable result bundle for a completed server run.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out-dir", default="", help="Optional server run directory override.")
    parser.add_argument("--allow-pending", action="store_true", help="Bundle partial results even if validation is pending.")
    args = parser.parse_args()
    try:
        archive, checksum, check = build_bundle(args.run_id, args.out_dir, args.allow_pending)
    except Exception as exc:
        print(f"[server-bundle] ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"[server-bundle] validation: {check.status}")
    print(f"[server-bundle] archive: {archive}")
    print(f"[server-bundle] checksum: {checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
