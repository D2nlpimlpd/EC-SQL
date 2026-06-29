from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.check_linux_shell_scripts import check_shell_scripts, find_bash
from scripts.check_ollama_models import fetch_models, split_models


REQUIRED_PROJECT_FILES = [
    "boyuesql_service.py",
    "requirements.txt",
    "requirements-oracle.txt",
    "requirements-raganything.txt",
    "requirements-server.txt",
    "constraints-server.txt",
    "scripts/setup_linux.sh",
    "scripts/one_click_linux.sh",
    "scripts/start_linux.sh",
    "scripts/pull_ollama_models.py",
    "scripts/download_hf_models.py",
    "scripts/plan_server_matrix.py",
    "scripts/launch_server_benchmark.sh",
    "scripts/server_run_status.py",
    "scripts/validate_server_matrix.py",
    "scripts/build_server_evidence_report.py",
    "scripts/build_server_abstract.py",
    "scripts/run_full_server_benchmark.sh",
    "scripts/run_server_experiments.sh",
    "scripts/audit_goal_readiness.py",
    "scripts/check_linux_shell_scripts.py",
    "third_party/raganything-1.3.1/raganything/sql_dictionary.py",
]


def status_line(kind: str, name: str, detail: str) -> str:
    return f"[{kind}] {name}: {detail}"


def existing_parent(path: Path) -> Path:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return probe


def manifest_summary(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            key = row.get("setting") or row.get("engine") or "unknown"
            engine = row.get("engine") or "unknown"
            counts[f"{key}:{engine}"] = counts.get(f"{key}:{engine}", 0) + 1
    return counts


def format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "empty"
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))


def check_required_files(project_root: Path) -> tuple[list[str], list[str]]:
    ok: list[str] = []
    issues: list[str] = []
    for rel in REQUIRED_PROJECT_FILES:
        path = project_root / rel
        if path.exists():
            ok.append(status_line("ok", rel, "found"))
        else:
            issues.append(status_line("error", rel, "missing"))
    return ok, issues


def check_command(name: str, executable: str) -> tuple[str, bool]:
    path = shutil.which(executable)
    if path:
        return status_line("ok", name, path), True
    return status_line("error", name, f"{executable} executable not found"), False


def check_bash_command() -> tuple[str, bool]:
    path = find_bash()
    if path:
        return status_line("ok", "bash", str(path)), True
    return status_line("error", "bash", "usable GNU/Git bash executable not found"), False


def check_python_venv() -> tuple[str, bool]:
    try:
        import venv  # noqa: F401
    except Exception as exc:
        return status_line("error", "python_venv", f"missing venv module: {exc}"), False
    return status_line("ok", "python_venv", "available"), True


def check_disk(path: Path, min_free_gb: float) -> tuple[str, bool]:
    parent = existing_parent(path)
    usage = shutil.disk_usage(parent)
    free_gb = usage.free / (1024**3)
    ok = free_gb >= min_free_gb
    kind = "ok" if ok else "error"
    return status_line(kind, "disk", f"{free_gb:.1f} GiB free at {parent}"), ok


def check_ollama(base_url: str, models: Iterable[str], timeout: float, warn_only: bool) -> tuple[list[str], list[str]]:
    required = split_models(models)
    if not required:
        return [status_line("ok", "ollama", "no models requested")], []
    try:
        available = fetch_models(base_url, timeout)
    except RuntimeError as exc:
        issue = status_line("warn" if warn_only else "error", "ollama", str(exc))
        return [], [issue]
    missing = [model for model in required if model not in available]
    ok = [
        status_line("ok", "ollama_endpoint", base_url.rstrip("/")),
        status_line("ok", "ollama_available", ", ".join(sorted(available)) if available else "(none)"),
    ]
    if missing:
        issues = [status_line("warn" if warn_only else "error", "ollama_missing", ", ".join(missing))]
        return ok, issues
    ok.append(status_line("ok", "ollama_required", ", ".join(required)))
    return ok, []


def check_shell_syntax(project_root: Path) -> tuple[list[str], list[str]]:
    bash_path, errors = check_shell_scripts(project_root=project_root)
    if errors:
        return [], [status_line("error", "shell_syntax", "; ".join(errors[:3]))]
    return [status_line("ok", "shell_syntax", f"bash={bash_path}")], []


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight checks for BoyueSQL Linux/server deployment.")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--spider-root", default=os.environ.get("SPIDER_ROOT") or os.environ.get("SPIDER2_ROOT") or "/data/text2sql_datasets/Spider2")
    parser.add_argument("--manifest", default=os.environ.get("MANIFEST") or str(PROJECT_ROOT / "artifacts" / "spider2_manifest.csv"))
    parser.add_argument("--min-free-gb", type=float, default=5.0)
    parser.add_argument("--require-dataset", action="store_true", help="Fail if Spider2 or the manifest is missing.")
    parser.add_argument("--ollama-base-url", default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    parser.add_argument("--model", action="append", default=[], help="Required Ollama model name or comma-separated model list.")
    parser.add_argument("--skip-ollama", action="store_true")
    parser.add_argument("--skip-shell-syntax", action="store_true")
    parser.add_argument("--ollama-timeout", type=float, default=10.0)
    parser.add_argument("--warn-only", action="store_true", help="Report errors but exit successfully.")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    spider_root = Path(args.spider_root)
    manifest = Path(args.manifest)
    output: list[str] = []
    issues: list[str] = []

    output.append(status_line("ok", "python", sys.version.split()[0]))
    if sys.version_info < (3, 10):
        issues.append(status_line("error", "python_version", "Python >= 3.10 is required"))

    git_line, git_ok = check_command("git", "git")
    output.append(git_line)
    if not git_ok:
        issues.append(git_line)

    bash_line, bash_ok = check_bash_command()
    output.append(bash_line)
    if not bash_ok:
        issues.append(bash_line)

    venv_line, venv_ok = check_python_venv()
    output.append(venv_line)
    if not venv_ok:
        issues.append(venv_line)

    file_ok, file_issues = check_required_files(project_root)
    output.extend(file_ok)
    issues.extend(file_issues)

    if not args.skip_shell_syntax:
        shell_ok, shell_issues = check_shell_syntax(project_root)
        output.extend(shell_ok)
        issues.extend(shell_issues)

    disk_line, disk_ok = check_disk(spider_root, args.min_free_gb)
    output.append(disk_line)
    if not disk_ok:
        issues.append(disk_line)

    if spider_root.exists():
        output.append(status_line("ok", "spider_root", str(spider_root)))
    else:
        target = issues if args.require_dataset else output
        kind = "error" if args.require_dataset else "warn"
        target.append(status_line(kind, "spider_root", f"missing: {spider_root}"))

    if manifest.exists():
        counts = manifest_summary(manifest)
        output.append(status_line("ok", "manifest", f"{manifest} ({format_counts(counts)})"))
    else:
        target = issues if args.require_dataset else output
        kind = "error" if args.require_dataset else "warn"
        target.append(status_line(kind, "manifest", f"missing: {manifest}"))

    if not args.skip_ollama:
        ollama_ok, ollama_issues = check_ollama(args.ollama_base_url, args.model, args.ollama_timeout, args.warn_only)
        output.extend(ollama_ok)
        issues.extend(ollama_issues)

    print("\n".join(output))
    if issues:
        print("\nIssues:", file=sys.stderr)
        print("\n".join(issues), file=sys.stderr)
        return 0 if args.warn_only else 2
    print(status_line("ok", "preflight", "all checks passed"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
