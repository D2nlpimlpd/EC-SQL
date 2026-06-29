from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.audit_goal_readiness import (
    DEFAULT_ABSTRACT,
    DEFAULT_CHECKSUM,
    DEFAULT_DATASET_ROOT,
    DEFAULT_DBT68,
    DEFAULT_MANIFEST,
    DEFAULT_RELEASE,
    DEFAULT_SQLITE24,
    Check,
    check_abstract,
    check_acceptance_contract,
    check_dataset_scale_report,
    check_dbt68,
    check_handoff_dry_run,
    check_manifest,
    check_one_click,
    check_release,
    check_release_smoke,
    check_sqlite24,
)
from scripts.plan_server_matrix import expected_artifacts, full_profile_config
from scripts.verify_server_release import sha256


def read_checksum(path: Path) -> dict[str, str]:
    if not path.exists():
        return {"sha256": "", "archive_name": ""}
    parts = path.read_text(encoding="utf-8").strip().split()
    return {
        "sha256": parts[0] if parts else "",
        "archive_name": parts[1] if len(parts) > 1 else "",
    }


def command_plan(run_id: str) -> list[str]:
    return [
        "python3 -m zipfile -e boyuesql_spider2_server.zip .  # or: unzip boyuesql_spider2_server.zip",
        "cd boyuesql_spider2_server",
        "bash scripts/one_click_linux.sh preflight",
        "# Before upload/launch from the local machine: python scripts/run_server_handoff.py --host user@server --stage remote-preflight --run-id "
        f"{run_id} --execute",
        "bash scripts/one_click_linux.sh setup",
        "bash scripts/one_click_linux.sh models",
        "bash scripts/one_click_linux.sh dataset-report",
        f"RUN_ID={run_id} bash scripts/one_click_linux.sh contract",
        f"# Recommended automated handoff: python scripts/run_server_handoff.py --host user@server --stage supervise --run-id {run_id} --execute",
        f"# One-command foreground alternative: RUN_ID={run_id} bash scripts/one_click_linux.sh paper-run",
        f"# One-command background alternative: RUN_ID={run_id} bash scripts/one_click_linux.sh paper-launch",
        f"RUN_ID={run_id} bash scripts/one_click_linux.sh plan",
        "bash scripts/one_click_linux.sh dry-run",
        f"RUN_ID={run_id} bash scripts/one_click_linux.sh launch",
        f"RUN_ID={run_id} bash scripts/one_click_linux.sh status",
        f"RUN_ID={run_id} bash scripts/one_click_linux.sh validate",
        f"RUN_ID={run_id} bash scripts/one_click_linux.sh evidence",
        f"RUN_ID={run_id} bash scripts/one_click_linux.sh abstract",
        f"RUN_ID={run_id} bash scripts/one_click_linux.sh bundle",
        f"# If the run fails or times out before validation: RUN_ID={run_id} bash scripts/one_click_linux.sh diagnostics",
        f"AUDIT_STRICT=1 SERVER_RUN_ID={run_id} bash scripts/one_click_linux.sh audit",
        (
            "# After copying the result bundle back locally: "
            f"python scripts/verify_server_result_bundle.py server_{run_id}_result_bundle.zip "
            f"--checksum server_{run_id}_result_bundle.sha256 --run-id {run_id}"
        ),
        (
            "# Then import the verified bundle into artifacts/server_runs/<RUN_ID>: "
            f"python scripts/import_server_result_bundle.py server_{run_id}_result_bundle.zip "
            f"--checksum server_{run_id}_result_bundle.sha256 --run-id {run_id} --overwrite"
        ),
        (
            "# For an incomplete diagnostic bundle, add --allow-pending to both "
            "verify_server_result_bundle.py and import_server_result_bundle.py."
        ),
    ]


def checks_for_current_state(
    *,
    run_id: str,
    dataset_root: Path,
    manifest: Path,
    archive: Path,
    checksum: Path,
    sqlite24: Path,
    dbt68: Path,
    abstract: Path,
) -> list[Check]:
    def safe(requirement: str, fn: Any) -> Check:
        try:
            return fn()
        except Exception as exc:
            return Check(requirement, "FAIL", f"{type(exc).__name__}: {exc}")

    return [
        safe("Spider2 broad benchmark available", lambda: check_manifest(manifest, dataset_root)),
        safe("Spider2 dataset scale report", lambda: check_dataset_scale_report(dataset_root, manifest)),
        safe("Server acceptance contract", lambda: check_acceptance_contract(run_id)),
        safe("SQLite local gold evidence", lambda: check_sqlite24(sqlite24)),
        safe("DBT 68-task evidence", lambda: check_dbt68(dbt68)),
        safe("Clean Linux server release", lambda: check_release(archive, checksum)),
        safe("Server release smoke test", lambda: check_release_smoke(archive, checksum)),
        safe("One-click Linux entrypoint", check_one_click),
        safe("Server handoff dry run", lambda: check_handoff_dry_run(run_id)),
        safe("Paper abstract generated", lambda: check_abstract(abstract)),
    ]


def build_manifest(
    *,
    run_id: str,
    out_dir: Path,
    archive: Path,
    checksum: Path,
    dataset_root: Path,
    manifest: Path,
    sqlite24: Path,
    dbt68: Path,
    abstract: Path,
) -> dict[str, Any]:
    config = full_profile_config(run_id, PROJECT_ROOT / "artifacts" / "server_runs" / run_id)
    artifacts = expected_artifacts(config)
    checksum_info = read_checksum(checksum)
    checks = checks_for_current_state(
        run_id=run_id,
        dataset_root=dataset_root,
        manifest=manifest,
        archive=archive,
        checksum=checksum,
        sqlite24=sqlite24,
        dbt68=dbt68,
        abstract=abstract,
    )
    return {
        "submission": {
            "name": "BoyueSQL Spider2 Linux server run",
            "run_id": run_id,
            "release_archive": str(archive),
            "release_archive_name": archive.name,
            "release_size_bytes": archive.stat().st_size if archive.exists() else 0,
            "release_sha256": checksum_info.get("sha256") or (sha256(archive) if archive.exists() else ""),
            "checksum_file": str(checksum),
            "dataset_root_local": str(dataset_root),
            "manifest": str(manifest),
            "abstract": str(abstract),
        },
        "pre_server_checks": [asdict(check) for check in checks],
        "server_commands": command_plan(run_id),
        "expected_artifacts": artifacts,
        "acceptance_rule": (
            "The goal remains incomplete until one_click_linux.sh validate, evidence, "
            "abstract, bundle, and AUDIT_STRICT=1 ... audit pass for the completed server RUN_ID."
        ),
    }


def markdown(payload: dict[str, Any]) -> str:
    sub = payload["submission"]
    lines = [
        f"# Server Submission Manifest: {sub['run_id']}",
        "",
        "## Upload Files",
        "",
        f"- `{sub['release_archive']}`",
        f"- `{sub['checksum_file']}`",
        "",
        "## Release Integrity",
        "",
        f"- Archive: `{sub['release_archive_name']}`",
        f"- SHA-256: `{sub['release_sha256']}`",
        f"- Size: {sub['release_size_bytes']} bytes",
        "",
        "## Current Local Evidence",
        "",
        "| Requirement | Status | Evidence |",
        "| --- | --- | --- |",
    ]
    for check in payload["pre_server_checks"]:
        lines.append(f"| {check['requirement']} | {check['status']} | {escape_md(check['evidence'])} |")
    lines.extend(
        [
            "",
            "## Server Commands",
            "",
            "```bash",
            *payload["server_commands"],
            "```",
            "",
            "## Expected Artifacts",
            "",
            "| Enabled | Stage | Artifact | Suite | Systems | Models | Expected Cases |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in payload["expected_artifacts"]:
        lines.append(
            "| "
            + " | ".join(
                escape_md(row[field])
                for field in ["enabled", "stage", "artifact", "suite", "systems", "models", "expected_cases"]
            )
            + " |"
        )
    lines.extend(["", f"**Acceptance rule:** {payload['acceptance_rule']}", ""])
    return "\n".join(lines)


def escape_md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def write_submission_manifest(
    *,
    run_id: str,
    out_dir: Path,
    archive: Path,
    checksum: Path,
    dataset_root: Path,
    manifest: Path,
    sqlite24: Path,
    dbt68: Path,
    abstract: Path,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_manifest(
        run_id=run_id,
        out_dir=out_dir,
        archive=archive,
        checksum=checksum,
        dataset_root=dataset_root,
        manifest=manifest,
        sqlite24=sqlite24,
        dbt68=dbt68,
        abstract=abstract,
    )
    json_path = out_dir / f"{run_id}_server_submission_manifest.json"
    md_path = out_dir / f"{run_id}_server_submission_manifest.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(markdown(payload), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a server upload/submission manifest for BoyueSQL.")
    parser.add_argument("--run-id", default="server_full_spider2")
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "artifacts" / "server_release"))
    parser.add_argument("--archive", default=str(DEFAULT_RELEASE))
    parser.add_argument("--checksum", default=str(DEFAULT_CHECKSUM))
    parser.add_argument("--dataset-root", default=str(DEFAULT_DATASET_ROOT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--sqlite24", default=str(DEFAULT_SQLITE24))
    parser.add_argument("--dbt68", default=str(DEFAULT_DBT68))
    parser.add_argument("--abstract", default=str(DEFAULT_ABSTRACT))
    args = parser.parse_args()
    json_path, md_path = write_submission_manifest(
        run_id=args.run_id,
        out_dir=Path(args.out_dir),
        archive=Path(args.archive),
        checksum=Path(args.checksum),
        dataset_root=Path(args.dataset_root),
        manifest=Path(args.manifest),
        sqlite24=Path(args.sqlite24),
        dbt68=Path(args.dbt68),
        abstract=Path(args.abstract),
    )
    print(f"[server-submission] json: {json_path}")
    print(f"[server-submission] markdown: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
