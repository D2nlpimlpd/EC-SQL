from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.audit_goal_readiness import check_server_matrix, check_server_result_abstract
from scripts.verify_server_result_bundle import find_manifest_name, read_json, verify_bundle


def _safe_member_name(raw_name: str) -> str:
    name = raw_name.replace("\\", "/").strip("/")
    path = PurePosixPath(name)
    if not name or path.is_absolute():
        raise ValueError(f"unsafe empty/absolute archive path: {raw_name}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe archive path traversal: {raw_name}")
    if any(":" in part for part in path.parts):
        raise ValueError(f"unsafe archive path contains colon: {raw_name}")
    return path.as_posix()


def _assert_under_directory(path: Path, directory: Path) -> None:
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError as exc:
        raise ValueError(f"unsafe extraction target outside destination: {path}") from exc


def _read_bundle_manifest(archive: Path, run_id: str = "") -> dict[str, Any]:
    with zipfile.ZipFile(archive, "r") as zf:
        manifest_name = find_manifest_name(zf, run_id)
        if not manifest_name:
            suffix = f" for run_id={run_id}" if run_id else ""
            raise ValueError(f"missing or ambiguous result bundle manifest{suffix}")
        return read_json(zf, manifest_name)


def _preflight_destination(dest_dir: Path, members: list[str], overwrite: bool) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    existing = [dest_dir / member for member in members if (dest_dir / member).exists()]
    if existing and not overwrite:
        sample = ", ".join(str(path) for path in existing[:5])
        raise FileExistsError(
            "destination already contains bundle file(s); rerun with --overwrite "
            f"to replace them: {sample}"
        )


def _extract_safely(archive: Path, dest_dir: Path, overwrite: bool = False) -> list[str]:
    imported: list[str] = []
    with zipfile.ZipFile(archive, "r") as zf:
        members: list[tuple[zipfile.ZipInfo, str]] = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            safe_name = _safe_member_name(info.filename)
            target = dest_dir / safe_name
            _assert_under_directory(target, dest_dir)
            members.append((info, safe_name))

        _preflight_destination(dest_dir, [name for _, name in members], overwrite)
        for info, safe_name in members:
            target = dest_dir / safe_name
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            imported.append(safe_name)
    return imported


def import_bundle(
    archive: Path,
    checksum: Path | None = None,
    run_id: str = "",
    dest_dir: Path | None = None,
    overwrite: bool = False,
    allow_pending: bool = False,
) -> tuple[Path, dict[str, Any]]:
    errors, verified_manifest = verify_bundle(archive, checksum, run_id, allow_pending=allow_pending)
    if errors:
        joined = "\n".join(f"- {error}" for error in errors)
        raise RuntimeError(f"server result bundle verification failed before import:\n{joined}")

    manifest = verified_manifest or _read_bundle_manifest(archive, run_id)
    effective_run_id = run_id or str(manifest.get("run_id") or "")
    if not effective_run_id:
        raise ValueError("bundle manifest does not contain a run_id")

    dest = dest_dir or PROJECT_ROOT / "artifacts" / "server_runs" / effective_run_id
    imported = _extract_safely(archive, dest, overwrite=overwrite)
    matrix_check = check_server_matrix(effective_run_id, dest)
    abstract_check = check_server_result_abstract(effective_run_id, dest)
    if not allow_pending:
        if matrix_check.status != "PASS":
            raise RuntimeError(
                "imported server run does not satisfy the required matrix coverage: "
                f"{matrix_check.status}: {matrix_check.evidence}"
            )
        if abstract_check.status != "PASS":
            raise RuntimeError(
                "imported server run does not include a validated server-result abstract: "
                f"{abstract_check.status}: {abstract_check.evidence}"
            )

    report = {
        "archive": str(archive),
        "checksum": str(checksum) if checksum else "",
        "run_id": effective_run_id,
        "dest_dir": str(dest),
        "imported_file_count": len(imported),
        "validation": manifest.get("validation") or {},
        "matrix_check": asdict(matrix_check),
        "server_result_abstract_check": asdict(abstract_check),
    }
    summary_dir = dest / "summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    report_path = summary_dir / f"server_{effective_run_id}_import_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest, report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify and import a returned BoyueSQL server result bundle into "
            "artifacts/server_runs/<RUN_ID>/ for local paper/audit use."
        )
    )
    parser.add_argument("archive", help="Path to server_<RUN_ID>_result_bundle.zip")
    parser.add_argument(
        "--checksum",
        default="",
        help="Optional .sha256 path. Defaults to archive with .sha256 suffix if present.",
    )
    parser.add_argument("--run-id", default="", help="Expected RUN_ID. Inferred from the bundle if omitted.")
    parser.add_argument(
        "--dest-dir",
        default="",
        help="Destination run directory. Defaults to artifacts/server_runs/<RUN_ID>.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace existing bundle files in the destination.")
    parser.add_argument("--allow-pending", action="store_true", help="Import a pending bundle for inspection.")
    args = parser.parse_args()

    archive = Path(args.archive)
    checksum = Path(args.checksum) if args.checksum else archive.with_suffix(".sha256")
    checksum_arg = checksum if checksum.exists() or args.checksum else None
    dest_arg = Path(args.dest_dir) if args.dest_dir else None
    try:
        dest, report = import_bundle(
            archive=archive,
            checksum=checksum_arg,
            run_id=args.run_id,
            dest_dir=dest_arg,
            overwrite=args.overwrite,
            allow_pending=args.allow_pending,
        )
    except Exception as exc:
        print(f"[server-bundle-import] FAILED: {exc}", file=sys.stderr)
        return 1

    matrix = report["matrix_check"]
    print(
        "[server-bundle-import] PASS "
        f"run_id={report['run_id']}, files={report['imported_file_count']}, "
        f"matrix={matrix['status']}, dest={dest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
