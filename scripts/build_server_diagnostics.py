from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.audit_goal_readiness import check_server_matrix
from scripts.server_run_status import pid_alive, planned_artifact_status, tail_text


def run_dir_for(run_id: str, out_dir: str = "") -> Path:
    return Path(out_dir) if out_dir else PROJECT_ROOT / "artifacts" / "server_runs" / run_id


def safe_text(path: Path, max_chars: int = 12000) -> str:
    if not path.exists() or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    return text[: max_chars // 2] + "\n...[truncated]...\n" + text[-max_chars // 2 :]


def file_row(path: Path, run_dir: Path) -> dict[str, Any]:
    stat = path.stat()
    try:
        rel = path.relative_to(run_dir).as_posix()
    except ValueError:
        rel = str(path)
    return {
        "path": rel,
        "size_bytes": stat.st_size,
        "mtime_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def expected_artifact_rows(run_dir: Path) -> list[dict[str, str]]:
    expected = run_dir / "expected_artifacts.csv"
    if not expected.exists():
        return []
    with expected.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def collect_diagnostics(run_id: str, run_dir: Path, tail_lines: int) -> dict[str, Any]:
    summary_dir = run_dir / "summary"
    pid_file = run_dir / "server_job.pid"
    log_file = run_dir / "server_job.log"
    pid_raw = pid_file.read_text(encoding="utf-8", errors="ignore").strip() if pid_file.exists() else ""
    pid_value: int | None = None
    pid_running = False
    if pid_raw:
        try:
            pid_value = int(pid_raw)
            pid_running = pid_alive(pid_value)
        except ValueError:
            pid_value = None

    expected_total, expected_present, expected_missing = planned_artifact_status(run_dir, limit=200)
    matrix_check = check_server_matrix(run_id, run_dir)
    json_files = sorted(
        [path for path in run_dir.glob("*.json") if path.is_file()]
        + [path for path in run_dir.glob("*_registered.json") if path.is_file()]
        + [path for path in run_dir.glob("spider2_external_baseline_*.json") if path.is_file()]
    )
    summary_files = sorted(summary_dir.glob("*")) if summary_dir.exists() else []
    config_files = [
        path
        for path in [
            run_dir / "run_config.env",
            run_dir / "launch.env",
            run_dir / "planned_steps.txt",
            run_dir / "expected_artifacts.csv",
            PROJECT_ROOT / "artifacts" / "spider2_manifest.csv",
        ]
        if path.exists()
    ]

    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "matrix_check": {
            "requirement": matrix_check.requirement,
            "status": matrix_check.status,
            "evidence": matrix_check.evidence,
        },
        "pid": {
            "raw": pid_raw,
            "value": pid_value,
            "running": pid_running,
            "pid_file": str(pid_file) if pid_file.exists() else "",
        },
        "log": {
            "path": str(log_file) if log_file.exists() else "",
            "tail_lines": tail_lines,
            "tail": tail_text(log_file, tail_lines),
        },
        "expected_artifacts": {
            "path": str(run_dir / "expected_artifacts.csv") if (run_dir / "expected_artifacts.csv").exists() else "",
            "enabled_total": expected_total,
            "present": expected_present,
            "missing": expected_missing,
            "rows": expected_artifact_rows(run_dir),
        },
        "artifacts": {
            "json_files": [file_row(path, run_dir) for path in json_files],
            "summary_files": [file_row(path, run_dir) for path in summary_files if path.is_file()],
            "config_files": [file_row(path, run_dir) for path in config_files],
        },
        "config_excerpt": {path.name: safe_text(path) for path in config_files},
    }


def markdown(payload: dict[str, Any]) -> str:
    matrix = payload["matrix_check"]
    pid = payload["pid"]
    expected = payload["expected_artifacts"]
    lines = [
        f"# Server Diagnostics: {payload['run_id']}",
        "",
        f"- Generated at UTC: `{payload['generated_at_utc']}`",
        f"- Run directory: `{payload['run_dir']}`",
        f"- Matrix status: `{matrix['status']}`",
        f"- Matrix evidence: {matrix['evidence']}",
        f"- PID: `{pid['raw'] or 'none'}` ({'running' if pid['running'] else 'not running'})",
        f"- Expected artifacts: `{expected['present']}/{expected['enabled_total']}` present",
        "",
        "## Missing Enabled Artifacts",
        "",
    ]
    missing = expected.get("missing") or []
    if missing:
        lines.extend(f"- {item}" for item in missing)
    else:
        lines.append("- None reported.")
    lines.extend(["", "## JSON Artifacts", ""])
    json_files = payload["artifacts"]["json_files"]
    if json_files:
        lines.extend(f"- `{row['path']}` ({row['size_bytes']} bytes)" for row in json_files)
    else:
        lines.append("- None.")
    lines.extend(["", "## Summary Files", ""])
    summary_files = payload["artifacts"]["summary_files"]
    if summary_files:
        lines.extend(f"- `{row['path']}` ({row['size_bytes']} bytes)" for row in summary_files)
    else:
        lines.append("- None.")
    lines.extend(["", "## Log Tail", "", "```text", payload["log"]["tail"] or "(no log)", "```", ""])
    return "\n".join(lines)


def write_diagnostics(run_id: str, out_dir: str = "", tail_lines: int = 200) -> tuple[Path, Path]:
    run_dir = run_dir_for(run_id, out_dir)
    if not run_dir.exists():
        raise FileNotFoundError(f"missing server run directory: {run_dir}")
    summary_dir = run_dir / "summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    payload = collect_diagnostics(run_id, run_dir, tail_lines)
    json_path = summary_dir / f"server_{run_id}_diagnostics.json"
    md_path = summary_dir / f"server_{run_id}_diagnostics.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(markdown(payload), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Write diagnostics for a BoyueSQL server run.")
    parser.add_argument("--run-id", default=os.environ.get("RUN_ID", ""), help="Server RUN_ID.")
    parser.add_argument("--out-dir", default=os.environ.get("OUT_DIR", ""), help="Optional server run directory.")
    parser.add_argument("--tail", type=int, default=200, help="Log lines to include.")
    args = parser.parse_args()
    if not args.run_id and not args.out_dir:
        raise SystemExit("Provide --run-id or --out-dir")
    run_id = args.run_id or Path(args.out_dir).name
    json_path, md_path = write_diagnostics(run_id, args.out_dir, args.tail)
    print(f"[server-diagnostics] json: {json_path}")
    print(f"[server-diagnostics] markdown: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
