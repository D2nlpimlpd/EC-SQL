from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.audit_goal_readiness import Check, check_server_matrix


def as_json(check: Check) -> str:
    return json.dumps(
        {
            "requirement": check.requirement,
            "status": check.status,
            "evidence": check.evidence,
        },
        ensure_ascii=False,
        indent=2,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate that a server run contains the full EC-SQL/SOTA/"
            "ablation matrix required before claiming the goal complete."
        )
    )
    parser.add_argument("--run-id", required=True, help="Server RUN_ID under artifacts/server_runs/.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument(
        "--allow-pending",
        action="store_true",
        help="Print pending status but exit 0. Use only for dashboards, not final acceptance.",
    )
    args = parser.parse_args()

    check = check_server_matrix(args.run_id)
    if args.json:
        print(as_json(check))
    else:
        print(f"[server-matrix] {check.status}: {check.requirement}")
        print(f"[server-matrix] {check.evidence}")
    if check.status == "PASS" or args.allow_pending:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
