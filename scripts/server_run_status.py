from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def tail_text(path: Path, lines: int) -> str:
    if not path.exists() or lines <= 0:
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


def planned_artifact_status(out_dir: Path, limit: int = 20) -> tuple[int, int, list[str]]:
    plan = out_dir / "expected_artifacts.csv"
    if not plan.exists():
        return 0, 0, []
    with plan.open("r", encoding="utf-8", newline="") as f:
        rows = [row for row in csv.DictReader(f) if str(row.get("enabled") or "") == "1"]
    missing: list[str] = []
    present = 0
    for row in rows:
        artifact = str(row.get("artifact") or "").strip()
        if not artifact:
            continue
        path = out_dir / artifact
        if path.exists() and path.stat().st_size > 0:
            present += 1
        else:
            stage = row.get("stage") or "unknown"
            missing.append(f"{stage}: {artifact}")
    return len(rows), present, missing[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description="Show status for a EC-SQL server benchmark run.")
    parser.add_argument("--run-id", default=os.environ.get("RUN_ID", ""))
    parser.add_argument("--out-dir", default=os.environ.get("OUT_DIR", ""))
    parser.add_argument("--tail", type=int, default=40, help="Log lines to print.")
    args = parser.parse_args()

    if args.out_dir:
        out_dir = Path(args.out_dir)
    elif args.run_id:
        out_dir = PROJECT_ROOT / "artifacts" / "server_runs" / args.run_id
    else:
        raise SystemExit("Provide --run-id or --out-dir")

    pid_file = out_dir / "server_job.pid"
    log_file = out_dir / "server_job.log"
    summary_dir = out_dir / "summary"
    json_files = sorted(out_dir.glob("spider2*.json"))
    run_config = out_dir / "run_config.env"
    run_marker = out_dir / "server_job.marker"
    planned = out_dir / "planned_steps.txt"
    expected = out_dir / "expected_artifacts.csv"

    print(f"[status] out_dir: {out_dir}")
    if pid_file.exists():
        raw = pid_file.read_text(encoding="utf-8", errors="ignore").strip()
        try:
            pid = int(raw)
            print(f"[status] pid: {pid} ({'running' if pid_alive(pid) else 'not running'})")
        except ValueError:
            print(f"[status] pid_file: invalid ({raw})")
    else:
        print("[status] pid: none")

    print(f"[status] json artifacts: {len(json_files)}")
    for path in json_files[-10:]:
        print(f"[status] artifact: {path.name} ({path.stat().st_size} bytes)")

    expected_total, expected_present, expected_missing = planned_artifact_status(out_dir)
    if expected.exists():
        print(f"[status] expected_artifacts: {expected}")
        print(
            f"[status] expected progress: {expected_present}/{expected_total} "
            f"enabled JSON artifacts present"
        )
        if expected_missing:
            print("[status] missing expected artifacts:")
            for item in expected_missing:
                print(f"[status] missing: {item}")
    else:
        print("[status] expected_artifacts: none; run one_click_linux.sh plan")

    if run_config.exists():
        print(f"[status] run_config: {run_config}")
    if run_marker.exists():
        print(f"[status] run_marker: {run_marker}")
        marker_text = tail_text(run_marker, 20)
        for line in marker_text.splitlines():
            print(f"[status] marker: {line}")
    if planned.exists():
        print(f"[status] planned_steps: {planned}")
    if summary_dir.exists():
        summaries = sorted(summary_dir.glob("server_*.md"))
        print(f"[status] summaries: {len(summaries)}")
        for path in summaries[-5:]:
            print(f"[status] summary: {path}")
        bundles = sorted(summary_dir.glob("server_*_result_bundle.zip"))
        print(f"[status] result bundles: {len(bundles)}")
        for path in bundles[-3:]:
            checksum = path.with_suffix(".sha256")
            suffix = f", checksum={checksum}" if checksum.exists() else ""
            print(f"[status] result bundle: {path} ({path.stat().st_size} bytes{suffix})")
    else:
        print("[status] summaries: none")

    if log_file.exists():
        print(f"[status] log: {log_file}")
        print("[status] log tail:")
        print(tail_text(log_file, args.tail))
    else:
        print("[status] log: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
