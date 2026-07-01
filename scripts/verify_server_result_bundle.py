from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.audit_goal_readiness import integer, is_sqlite_sota_baseline_row


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_checksum(path: Path) -> tuple[str, str]:
    parts = path.read_text(encoding="utf-8").strip().split()
    if len(parts) < 2:
        raise ValueError(f"invalid checksum file: {path}")
    return parts[0], parts[1]


def normalized_names(zf: zipfile.ZipFile) -> set[str]:
    return {name.replace("\\", "/").strip("/") for name in zf.namelist()}


def find_manifest_name(zf: zipfile.ZipFile, run_id: str = "") -> str | None:
    names = normalized_names(zf)
    if run_id:
        expected = f"summary/server_{run_id}_result_bundle_manifest.json"
        return expected if expected in names else None
    matches = sorted(
        name
        for name in names
        if name.startswith("summary/server_") and name.endswith("_result_bundle_manifest.json")
    )
    return matches[0] if len(matches) == 1 else None


def read_json(zf: zipfile.ZipFile, name: str) -> dict[str, Any]:
    payload = json.loads(zf.read(name).decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def read_csv_rows(zf: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    text = zf.read(name).decode("utf-8", errors="replace")
    return list(csv.DictReader(text.splitlines()))


def parse_key_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw or raw.lstrip().startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def archive_path_candidates(raw: str) -> list[str]:
    raw = raw.replace("\\", "/").strip("/")
    candidates = [raw]
    name = Path(raw).name
    if name and name not in candidates:
        candidates.append(name)
    return candidates


def archive_contains_artifact(names: set[str], raw: str) -> bool:
    for candidate in archive_path_candidates(raw):
        if candidate in names:
            return True
    return False


def verify_manifest_files(zf: zipfile.ZipFile, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    names = normalized_names(zf)
    files = manifest.get("files") or []
    if not isinstance(files, list) or not files:
        return ["bundle manifest contains no file rows"]
    for row in files:
        if not isinstance(row, dict):
            errors.append("bundle manifest contains a non-object file row")
            continue
        rel = str(row.get("path") or "").replace("\\", "/").strip("/")
        if not rel:
            errors.append("bundle manifest contains an empty file path")
            continue
        if rel not in names:
            errors.append(f"manifest file missing from archive: {rel}")
            continue
        data = zf.read(rel)
        expected_size = integer(row.get("size_bytes"), -1)
        expected_sha = str(row.get("sha256") or "")
        if expected_size >= 0 and len(data) != expected_size:
            errors.append(f"size mismatch for {rel}: expected {expected_size}, got {len(data)}")
        if expected_sha and sha256_bytes(data) != expected_sha:
            errors.append(f"sha256 mismatch for {rel}")
    return errors


def verify_expected_artifacts(zf: zipfile.ZipFile, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    names = normalized_names(zf)
    missing = manifest.get("missing_expected_artifacts") or []
    if missing:
        errors.append(f"manifest reports missing expected artifact(s): {', '.join(map(str, missing))}")
    if "expected_artifacts.csv" not in names:
        errors.append("missing expected_artifacts.csv")
        return errors
    for row in read_csv_rows(zf, "expected_artifacts.csv"):
        if str(row.get("enabled") or "") != "1":
            continue
        artifact = str(row.get("artifact") or "").strip()
        if artifact and not archive_contains_artifact(names, artifact):
            errors.append(f"enabled expected artifact absent from archive: {artifact}")
    return errors


def verify_summary_matrix(zf: zipfile.ZipFile, run_id: str) -> list[str]:
    errors: list[str] = []
    names = normalized_names(zf)
    required_reports = [
        f"summary/server_{run_id}.csv",
        f"summary/server_{run_id}_cases.csv",
        f"summary/server_{run_id}.md",
        f"summary/server_{run_id}_failures.md",
        f"summary/server_{run_id}_evidence.csv",
        f"summary/server_{run_id}_evidence.md",
        f"summary/server_{run_id}_results_snippet.tex",
        f"summary/server_{run_id}_abstract.tex",
        f"summary/server_{run_id}_dataset_scale_report.json",
        f"summary/server_{run_id}_dataset_scale_report.md",
        f"summary/server_{run_id}_acceptance_contract.json",
        f"summary/server_{run_id}_acceptance_contract.md",
        f"summary/server_{run_id}_completion_certificate.json",
    ]
    for name in required_reports:
        if name not in names:
            errors.append(f"missing required report: {name}")
    summary_name = f"summary/server_{run_id}.csv"
    if summary_name not in names:
        return errors

    rows = [row for row in read_csv_rows(zf, summary_name) if integer(row.get("cases")) > 0]
    if not rows:
        errors.append(f"empty server summary: {summary_name}")
        return errors

    missing_artifact_rows = [
        str(row.get("artifact") or "(empty artifact)")
        for row in rows
        if not archive_contains_artifact(names, str(row.get("artifact") or ""))
    ]
    if missing_artifact_rows:
        errors.append(f"summary references artifact(s) absent from archive: {', '.join(missing_artifact_rows[:5])}")

    sqlite_rows = [row for row in rows if row.get("suite") == "spider2-sqlite"]
    dbt_rows = [row for row in rows if row.get("suite") == "spider2-dbt"]
    sqlite_full = [row for row in sqlite_rows if row.get("system") == "ecsql" and integer(row.get("cases")) >= 20]
    sqlite_ablations = {
        str(row.get("system") or "")
        for row in sqlite_rows
        if str(row.get("system") or "").startswith("no_") and integer(row.get("cases")) >= 20
    }
    sqlite_baselines = {
        str(row.get("system") or "")
        for row in sqlite_rows
        if is_sqlite_sota_baseline_row(row)
    }
    baseline_models = {
        str(row.get("model") or "")
        for row in sqlite_rows
        if is_sqlite_sota_baseline_row(row) and str(row.get("model") or "")
    }
    dbt_baseline = [
        row
        for row in dbt_rows
        if "existing_project" in str(row.get("system") or "") and integer(row.get("cases")) >= 68
    ]
    dbt_full = [
        row
        for row in dbt_rows
        if "ecsql_deterministic_full" in str(row.get("system") or "") and integer(row.get("cases")) >= 68
    ]
    dbt_ablations = {
        str(row.get("system") or "")
        for row in dbt_rows
        if "ablation" in str(row.get("system") or "") and integer(row.get("cases")) >= 68
    }
    if not sqlite_full:
        errors.append("missing SQLite EC-SQL full row with >=20 gold cases")
    if len(sqlite_ablations) < 3:
        errors.append(f"missing SQLite ablation coverage: found {len(sqlite_ablations)}, need >=3")
    if len(sqlite_baselines) < 4:
        errors.append(f"missing SOTA-style SQLite baseline coverage: found {len(sqlite_baselines)}, need >=4")
    if len(baseline_models) < 3:
        errors.append(f"missing baseline model coverage: found {len(baseline_models)}, need >=3")
    if not dbt_baseline:
        errors.append("missing DBT starter-project baseline with 68 cases")
    if not dbt_full:
        errors.append("missing DBT EC-SQL deterministic full with 68 cases")
    if len(dbt_ablations) < 5:
        errors.append(f"missing DBT ablation coverage: found {len(dbt_ablations)}, need >=5")
    return errors


def verify_server_result_abstract(zf: zipfile.ZipFile, run_id: str) -> list[str]:
    name = f"summary/server_{run_id}_abstract.tex"
    names = normalized_names(zf)
    if name not in names:
        return [f"missing server-result abstract: {name}"]
    text = zf.read(name).decode("utf-8", errors="replace")
    required_terms = [
        r"\begin{abstract}",
        r"\end{abstract}",
        "EC-SQL",
        "Spider2",
        "validated server run",
        "SOTA-style baseline",
        "semantic pass rate",
    ]
    missing = [term for term in required_terms if term not in text]
    escaped_run_id = run_id.replace("_", r"\_")
    if run_id not in text and escaped_run_id not in text:
        missing.append(f"run id {run_id}")
    return [f"server-result abstract missing terms: {', '.join(missing)}"] if missing else []


def verify_completion_certificate(zf: zipfile.ZipFile, run_id: str, allow_pending: bool = False) -> list[str]:
    name = f"summary/server_{run_id}_completion_certificate.json"
    names = normalized_names(zf)
    if name not in names:
        return [f"missing completion certificate: {name}"]
    cert = read_json(zf, name)
    errors: list[str] = []
    cert_run_id = str(cert.get("run_id") or "")
    if cert_run_id != run_id:
        errors.append(f"completion certificate run_id mismatch: expected {run_id}, got {cert_run_id}")
    completion = str(cert.get("completion_status") or "")
    if completion != "PASS" and not allow_pending:
        errors.append(f"completion certificate status is not PASS: {completion}")
    expected = cert.get("expected_artifacts_check") or {}
    expected_status = expected.get("status") if isinstance(expected, dict) else None
    if expected_status != "PASS" and not allow_pending:
        errors.append(f"completion certificate expected-artifacts status is not PASS: {expected_status}")
    marker_check = cert.get("launch_marker_check") or {}
    marker_status = marker_check.get("status") if isinstance(marker_check, dict) else None
    if marker_status != "PASS" and not allow_pending:
        errors.append(f"completion certificate launch-marker status is not PASS: {marker_status}")
    matrix = cert.get("matrix_check") or {}
    matrix_status = matrix.get("status") if isinstance(matrix, dict) else None
    if matrix_status != "PASS" and not allow_pending:
        errors.append(f"completion certificate matrix status is not PASS: {matrix_status}")
    abstract = cert.get("server_result_abstract_check") or {}
    abstract_status = abstract.get("status") if isinstance(abstract, dict) else None
    if abstract_status != "PASS" and not allow_pending:
        errors.append(f"completion certificate abstract status is not PASS: {abstract_status}")
    missing = cert.get("missing_expected_artifacts") or []
    if missing and not allow_pending:
        errors.append(f"completion certificate reports missing expected artifact(s): {', '.join(map(str, missing[:10]))}")
    names = normalized_names(zf)
    background_files = {"launch.env", "server_job.pid"} & names
    marker_name = "server_job.marker"
    if background_files and marker_name not in names:
        errors.append("background launch files exist but server_job.marker is absent")
    if marker_name in names:
        marker = parse_key_values(zf.read(marker_name).decode("utf-8", errors="replace"))
        if marker.get("RUN_ID") != run_id:
            errors.append(f"launch marker RUN_ID mismatch: expected {run_id}, got {marker.get('RUN_ID')}")
        for key in ("RUN_MARKER_ID", "RUN_STARTED_AT_EPOCH"):
            if not marker.get(key):
                errors.append(f"launch marker missing key: {key}")
        cert_marker = cert.get("launch_marker") or {}
        if isinstance(cert_marker, dict) and cert_marker:
            for key in ("RUN_ID", "RUN_MARKER_ID", "RUN_STARTED_AT_EPOCH"):
                if str(cert_marker.get(key) or "") != str(marker.get(key) or ""):
                    errors.append(f"completion certificate launch marker mismatch for {key}")
    return errors


def verify_bundle(
    archive: Path,
    checksum: Path | None = None,
    run_id: str = "",
    allow_pending: bool = False,
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    if not archive.exists():
        return [f"missing bundle archive: {archive}"], {}
    if checksum is not None:
        if not checksum.exists():
            errors.append(f"missing checksum: {checksum}")
        else:
            try:
                expected_hash, expected_name = read_checksum(checksum)
                actual_hash = sha256_file(archive)
                if expected_hash != actual_hash:
                    errors.append(f"bundle checksum mismatch: expected {expected_hash}, got {actual_hash}")
                if expected_name != archive.name:
                    errors.append(f"checksum archive name mismatch: expected {expected_name}, got {archive.name}")
            except Exception as exc:
                errors.append(str(exc))

    try:
        with zipfile.ZipFile(archive, "r") as zf:
            manifest_name = find_manifest_name(zf, run_id)
            if not manifest_name:
                suffix = f" for run_id={run_id}" if run_id else ""
                return errors + [f"missing or ambiguous result bundle manifest{suffix}"], {}
            manifest = read_json(zf, manifest_name)
            manifest_run_id = str(manifest.get("run_id") or "")
            if run_id and manifest_run_id != run_id:
                errors.append(f"run_id mismatch: expected {run_id}, got {manifest_run_id}")
            effective_run_id = run_id or manifest_run_id
            if not effective_run_id:
                errors.append("bundle manifest has no run_id")
                effective_run_id = "UNKNOWN"

            validation = manifest.get("validation") or {}
            status = validation.get("status") if isinstance(validation, dict) else None
            if status != "PASS" and not allow_pending:
                errors.append(f"bundle validation status is not PASS: {status}")
            abstract_validation = manifest.get("abstract_validation") or {}
            abstract_status = abstract_validation.get("status") if isinstance(abstract_validation, dict) else None
            if abstract_status != "PASS" and not allow_pending:
                errors.append(f"bundle abstract validation status is not PASS: {abstract_status}")
            expected_validation = manifest.get("expected_artifacts_validation") or {}
            expected_status = expected_validation.get("status") if isinstance(expected_validation, dict) else None
            if expected_status != "PASS" and not allow_pending:
                errors.append(f"bundle expected-artifacts validation status is not PASS: {expected_status}")
            marker_validation = manifest.get("launch_marker_validation") or {}
            marker_status = marker_validation.get("status") if isinstance(marker_validation, dict) else None
            if marker_status != "PASS" and not allow_pending:
                errors.append(f"bundle launch-marker validation status is not PASS: {marker_status}")
            completion_status = str(manifest.get("completion_status") or "")
            if completion_status != "PASS" and not allow_pending:
                errors.append(f"bundle completion status is not PASS: {completion_status}")

            errors.extend(verify_manifest_files(zf, manifest))
            if not allow_pending or status == "PASS":
                errors.extend(verify_expected_artifacts(zf, manifest))
                errors.extend(verify_summary_matrix(zf, effective_run_id))
                errors.extend(verify_server_result_abstract(zf, effective_run_id))
                errors.extend(verify_completion_certificate(zf, effective_run_id, allow_pending))
            return errors, manifest
    except zipfile.BadZipFile:
        return errors + [f"invalid zip archive: {archive}"], {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a returned EC-SQL server result bundle.")
    parser.add_argument("archive", help="Path to server_<RUN_ID>_result_bundle.zip")
    parser.add_argument("--checksum", default="", help="Optional .sha256 path. Defaults to archive with .sha256 suffix if present.")
    parser.add_argument("--run-id", default="", help="Expected RUN_ID.")
    parser.add_argument("--allow-pending", action="store_true", help="Accept a bundle whose internal validation is pending.")
    args = parser.parse_args()
    archive = Path(args.archive)
    checksum = Path(args.checksum) if args.checksum else archive.with_suffix(".sha256")
    checksum_arg = checksum if checksum.exists() or args.checksum else None
    errors, manifest = verify_bundle(archive, checksum_arg, args.run_id, args.allow_pending)
    if errors:
        print("[server-bundle-verify] FAILED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    run_id = manifest.get("run_id") or args.run_id
    file_count = len(manifest.get("files") or [])
    print(f"[server-bundle-verify] PASS run_id={run_id}, files={file_count}, archive={archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
