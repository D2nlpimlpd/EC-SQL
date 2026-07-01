from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ecsql_generic.datasets import (
    ensure_spider2,
    ensure_spider2_dbt_databases,
    ensure_spider2_localdb,
    inspect_spider2,
    iter_status_lines,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download or inspect Spider2 dataset metadata")
    parser.add_argument(
        "--root",
        default=r"D:\text2sql_datasets",
        help="Dataset root directory. The Spider2 repo will live under ROOT/Spider2.",
    )
    parser.add_argument("--force", action="store_true", help="Delete and reclone Spider2")
    parser.add_argument("--dry-run", action="store_true", help="Print target paths without cloning")
    parser.add_argument("--inspect-only", action="store_true", help="Only inspect an existing checkout")
    parser.add_argument(
        "--localdb",
        action="store_true",
        help="Also download the optional Spider2-Lite SQLite local DB archive",
    )
    parser.add_argument(
        "--dbt",
        action="store_true",
        help="Also download and install the optional Spider2-DBT DuckDB archives",
    )
    parser.add_argument(
        "--no-dbt-setup",
        action="store_true",
        help="Download DBT archives but do not run spider2-dbt/setup.py",
    )
    args = parser.parse_args()

    if not args.inspect_only:
        repo = ensure_spider2(args.root, force=args.force, dry_run=args.dry_run)
    else:
        repo = Path(args.root)
    if args.localdb:
        ensure_spider2_localdb(repo, force=args.force, dry_run=args.dry_run)
    if args.dbt:
        ensure_spider2_dbt_databases(
            repo,
            force=args.force,
            dry_run=args.dry_run,
            run_setup=not args.no_dbt_setup,
        )
    status = inspect_spider2(repo if repo.name.lower() == "spider2" else Path(args.root))
    for line in iter_status_lines(status):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
