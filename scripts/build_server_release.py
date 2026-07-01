from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import time
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

INCLUDE_FILES = [
    ".env.example",
    ".dockerignore",
    ".gitignore",
    "Dockerfile",
    "docker-compose.yml",
    "requirements.txt",
    "requirements-oracle.txt",
    "requirements-raganything.txt",
    "requirements-server.txt",
    "constraints-server.txt",
    "readme.md",
    "README_MAIN.md",
    "SERVER_RUNBOOK.md",
    "SERVER_MODEL_GUIDE.md",
    "GENERALIZATION_PLAN.md",
    "RAGANYTHING_SCHEMA_KG_PROOF.md",
    "ecsql_spider2_abstract.tex",
    "ecsql_service.py",
]

INCLUDE_DIRS = [
    "ecsql_generic",
    "scripts",
    "tests",
    "baselines",
    "third_party/raganything-1.3.1",
]

EXCLUDE_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
    ".venv",
    "node_modules",
}

EXCLUDE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".log",
    ".tmp",
}


def should_copy(path: Path) -> bool:
    if any(part in EXCLUDE_DIR_NAMES for part in path.parts):
        return False
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return False
    return True


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_tree(src: Path, dst: Path) -> int:
    count = 0
    for file in src.rglob("*"):
        if not file.is_file() or not should_copy(file.relative_to(src)):
            continue
        rel = file.relative_to(src)
        copy_file(file, dst / rel)
        count += 1
    return count


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unlink_with_retry(path: Path, attempts: int = 8, delay: float = 1.0) -> None:
    if not path.exists():
        return
    for attempt in range(1, attempts + 1):
        try:
            path.unlink()
            return
        except PermissionError:
            if attempt == attempts:
                raise
            time.sleep(delay)


def build_release(out_dir: Path, name: str) -> Path:
    staging = out_dir / name
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for item in INCLUDE_FILES:
        src = PROJECT_ROOT / item
        if src.exists() and src.is_file():
            copy_file(src, staging / item)
            copied.append(item)

    for item in INCLUDE_DIRS:
        src = PROJECT_ROOT / item
        if src.exists() and src.is_dir():
            count = copy_tree(src, staging / item)
            copied.append(f"{item}/ ({count} files)")

    manifest = staging / "SERVER_RELEASE_MANIFEST.txt"
    manifest.write_text(
        "\n".join(
            [
                "EC-SQL clean server release",
                "",
                "Included allowlist:",
                *[f"- {item}" for item in copied],
                "",
                "Excluded by design:",
                "- legacy demo entrypoints",
                "- legacy UI assets",
                "- local PDFs, figures, notebooks, generated paper artifacts, and logs",
                "- dataset files and benchmark outputs; download/regenerate them with bash scripts/one_click_linux.sh setup",
                "",
                "Server setup:",
                "1. extract this package with unzip, or: python3 -m zipfile -e ecsql_spider2_server.zip .",
                "2. bash scripts/one_click_linux.sh preflight",
                "3. bash scripts/one_click_linux.sh setup   # Python env + Spider2 + Ollama/HF model downloads",
                "4. bash scripts/one_click_linux.sh models",
                "5. bash scripts/one_click_linux.sh dataset-report",
                "6. RUN_ID=<server_run_id> bash scripts/one_click_linux.sh contract",
                "7. RUN_ID=<server_run_id> bash scripts/one_click_linux.sh paper-run      # foreground full matrix + evidence + abstract + bundle + audit",
                "8. RUN_ID=<server_run_id> bash scripts/one_click_linux.sh paper-launch   # background full matrix + evidence + abstract + bundle + audit",
                "9. RUN_ID=<server_run_id> bash scripts/one_click_linux.sh plan",
                "10. bash scripts/one_click_linux.sh dry-run",
                "11. RUN_ID=<server_run_id> bash scripts/one_click_linux.sh launch",
                "12. RUN_ID=<server_run_id> bash scripts/one_click_linux.sh status",
                "13. RUN_ID=<server_run_id> bash scripts/one_click_linux.sh validate",
                "14. RUN_ID=<server_run_id> bash scripts/one_click_linux.sh evidence",
                "15. RUN_ID=<server_run_id> bash scripts/one_click_linux.sh abstract",
                "16. RUN_ID=<server_run_id> bash scripts/one_click_linux.sh bundle",
                "17. RUN_ID=<server_run_id> bash scripts/one_click_linux.sh upload-packet",
                "18. RUN_ID=<existing_run_id> bash scripts/one_click_linux.sh resume   # foreground resume after interruption",
                "19. RUN_ID=<existing_run_id> bash scripts/one_click_linux.sh summarize",
                "20. AUDIT_STRICT=1 SERVER_RUN_ID=<server_run_id> bash scripts/one_click_linux.sh audit",
                "21. cp .env.example .env and edit model/database settings",
                "22. bash scripts/one_click_linux.sh service",
                "23. After copying summary/server_<server_run_id>_result_bundle.zip back locally, run scripts/finalize_server_result.py",
                "24. The finalizer verifies/imports the bundle, runs the strict audit, and copies the server-result abstract to a stable paper artifact path",
                "25. Local package smoke test: python scripts/smoke_test_server_release.py",
                "26. Server upload packet: python scripts/build_server_upload_packet.py --run-id <server_run_id>",
                "27. Manual packet launch after extraction: bash RUN_PACKET_ON_SERVER.sh background",
                "28. Manual packet follow-up: bash RUN_PACKET_ON_SERVER.sh wait && bash RUN_PACKET_ON_SERVER.sh bundle && bash RUN_PACKET_ON_SERVER.sh audit",
                "29. Upload packet smoke test: python scripts/smoke_test_server_upload_packet.py --packet artifacts/server_release/<server_run_id>_server_upload_packet.zip",
                "30. Result acceptance flow smoke test: python scripts/smoke_test_server_acceptance_flow.py",
                "31. Command sheet: python scripts/build_server_handoff_commands.py --host user@server --remote-dir ~/ecsql_spider2_run",
                "32. Remote preflight: python scripts/run_server_handoff.py --host user@server --stage remote-preflight --execute",
                "33. Dry-run/executor: python scripts/run_server_handoff.py --host user@server --stage submit",
                "34. One-command supervised server run: python scripts/run_server_handoff.py --host user@server --stage supervise --execute",
                "35. Background follow-up: python scripts/run_server_handoff.py --host user@server --stage wait && python scripts/run_server_handoff.py --host user@server --stage collect",
                "",
            ]
        ),
        encoding="utf-8",
    )

    archive = out_dir / f"{name}.zip"
    unlink_with_retry(archive)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file in staging.rglob("*"):
            if file.is_file():
                zf.write(file, file.relative_to(out_dir))
    checksum = out_dir / f"{name}.sha256"
    checksum.write_text(f"{sha256(archive)}  {archive.name}\n", encoding="utf-8", newline="\n")
    try:
        from scripts.audit_goal_readiness import DEFAULT_DATASET_ROOT
        from scripts.build_server_submission_manifest import write_submission_manifest

        write_submission_manifest(
            run_id="server_full_spider2",
            out_dir=out_dir,
            archive=archive,
            checksum=checksum,
            dataset_root=DEFAULT_DATASET_ROOT,
            manifest=PROJECT_ROOT / "artifacts" / "spider2_manifest.csv",
            sqlite24=PROJECT_ROOT
            / "artifacts"
            / "server_runs"
            / "sqlite_llm_server_gold24_v1"
            / "spider2_sqlite_ecsql_ablation_qwen2.5-coder_7b.json",
            dbt68=PROJECT_ROOT / "artifacts" / "spider2_dbt_llm_edit_dbt68_v10b_full.json",
            abstract=PROJECT_ROOT / "artifacts" / "ecsql_spider2_abstract.tex",
        )
    except Exception as exc:
        print(f"warning: server submission manifest was not generated: {exc}")
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a clean EC-SQL server release package.")
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "artifacts" / "server_release"))
    parser.add_argument("--name", default="ecsql_spider2_server")
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = build_release(out_dir, args.name)
    print(f"release: {archive}")
    print(f"checksum: {archive.with_suffix('.sha256')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
