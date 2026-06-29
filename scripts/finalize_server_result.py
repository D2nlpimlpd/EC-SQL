from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.audit_goal_readiness import DEFAULT_DATASET_ROOT, DEFAULT_MANIFEST
from scripts.import_server_result_bundle import import_bundle


DEFAULT_ABSTRACT_COPY = PROJECT_ROOT / "artifacts" / "boyuesql_spider2_server_result_abstract.tex"
DEFAULT_ROOT_ABSTRACT_COPY = PROJECT_ROOT / "boyuesql_spider2_server_result_abstract.tex"


def run_audit(
    *,
    run_id: str,
    server_run_dir: Path,
    dataset_root: Path,
    manifest: Path,
    out_path: Path,
    strict: bool = True,
) -> tuple[int, str]:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "audit_goal_readiness.py"),
        "--dataset-root",
        str(dataset_root),
        "--manifest",
        str(manifest),
        "--server-run-id",
        run_id,
        "--server-run-dir",
        str(server_run_dir),
        "--out",
        str(out_path),
    ]
    if strict:
        cmd.append("--strict")
    env = os.environ.copy()
    env["BOYUESQL_IN_FINALIZER_AUDIT"] = "1"
    completed = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    if not out_path.exists() and output:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
    return completed.returncode, output


def copy_abstracts(run_id: str, dest_dir: Path, abstract_output: Path, root_abstract_output: Path | None) -> dict[str, str]:
    source = dest_dir / "summary" / f"server_{run_id}_abstract.tex"
    if not source.exists() or source.stat().st_size <= 0:
        raise FileNotFoundError(f"missing imported server abstract: {source}")
    abstract_output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, abstract_output)
    copied = {"source": str(source), "artifact_copy": str(abstract_output)}
    if root_abstract_output is not None:
        shutil.copy2(source, root_abstract_output)
        copied["root_copy"] = str(root_abstract_output)
    return copied


def finalize_server_result(
    *,
    archive: Path,
    checksum: Path | None,
    run_id: str,
    dest_dir: Path | None = None,
    overwrite: bool = False,
    allow_pending: bool = False,
    dataset_root: Path = DEFAULT_DATASET_ROOT,
    manifest: Path = DEFAULT_MANIFEST,
    abstract_output: Path = DEFAULT_ABSTRACT_COPY,
    root_abstract_output: Path | None = DEFAULT_ROOT_ABSTRACT_COPY,
) -> tuple[Path, dict[str, Any]]:
    dest, import_report = import_bundle(
        archive=archive,
        checksum=checksum,
        run_id=run_id,
        dest_dir=dest_dir,
        overwrite=overwrite,
        allow_pending=allow_pending,
    )
    effective_run_id = str(import_report.get("run_id") or run_id)
    copied = copy_abstracts(effective_run_id, dest, abstract_output, root_abstract_output)

    summary_dir = dest / "summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    audit_path = summary_dir / f"server_{effective_run_id}_final_acceptance_audit.md"
    audit_code, audit_output = run_audit(
        run_id=effective_run_id,
        server_run_dir=dest,
        dataset_root=dataset_root,
        manifest=manifest,
        out_path=audit_path,
        strict=not allow_pending,
    )
    if audit_code != 0 and not allow_pending:
        raise RuntimeError(
            "strict readiness audit failed after importing server result; "
            f"see {audit_path}\n{audit_output[-2000:]}"
        )

    report = {
        "run_id": effective_run_id,
        "archive": str(archive),
        "checksum": str(checksum) if checksum else "",
        "dest_dir": str(dest),
        "import_report": import_report,
        "abstracts": copied,
        "audit": {
            "returncode": audit_code,
            "path": str(audit_path),
            "strict": not allow_pending,
        },
        "completion_status": "PASS" if audit_code == 0 else "PENDING",
    }
    report_path = summary_dir / f"server_{effective_run_id}_final_acceptance_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest, report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Final local acceptance for a returned BoyueSQL server run: verify/import "
            "the result bundle, run the strict readiness audit, and copy the "
            "server-result abstract to stable paper artifact paths."
        )
    )
    parser.add_argument("archive", help="Path to server_<RUN_ID>_result_bundle.zip")
    parser.add_argument("--checksum", default="", help="Optional .sha256 path. Defaults to archive with .sha256 if present.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dest-dir", default="", help="Defaults to artifacts/server_runs/<RUN_ID>.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--allow-pending", action="store_true", help="Accept/import a pending diagnostics bundle.")
    parser.add_argument("--dataset-root", default=str(DEFAULT_DATASET_ROOT))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--abstract-output", default=str(DEFAULT_ABSTRACT_COPY))
    parser.add_argument(
        "--no-root-abstract-copy",
        action="store_true",
        help="Do not copy the server-result abstract to the project root.",
    )
    args = parser.parse_args()

    archive = Path(args.archive)
    checksum = Path(args.checksum) if args.checksum else archive.with_suffix(".sha256")
    checksum_arg = checksum if checksum.exists() or args.checksum else None
    try:
        dest, report = finalize_server_result(
            archive=archive,
            checksum=checksum_arg,
            run_id=args.run_id,
            dest_dir=Path(args.dest_dir) if args.dest_dir else None,
            overwrite=args.overwrite,
            allow_pending=args.allow_pending,
            dataset_root=Path(args.dataset_root),
            manifest=Path(args.manifest),
            abstract_output=Path(args.abstract_output),
            root_abstract_output=None if args.no_root_abstract_copy else DEFAULT_ROOT_ABSTRACT_COPY,
        )
    except Exception as exc:
        print(f"[server-finalize] FAILED: {exc}", file=sys.stderr)
        return 1

    print(
        "[server-finalize] "
        f"{report['completion_status']} run_id={report['run_id']} "
        f"dest={dest} abstract={report['abstracts'].get('artifact_copy')}"
    )
    return 0 if report["completion_status"] == "PASS" or args.allow_pending else 1


if __name__ == "__main__":
    raise SystemExit(main())
