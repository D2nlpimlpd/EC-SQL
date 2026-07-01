from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.register_external_baseline_result import build_payload, slugify, write_payload


FIELDS = [
    "suite",
    "system",
    "model",
    "cases",
    "er",
    "re",
    "ser",
    "ser_evaluable",
    "run_ok",
    "semantic_pass",
    "gold_evaluable",
    "gold_artifact_issues",
    "avg_latency_s",
    "baseline_reference",
    "report_label",
    "implementation_type",
    "official_reproduction",
    "source_repo",
    "source_commit",
    "source_command",
    "source_artifact",
    "notes",
    "out",
]


def clean_row(row: dict[str, str]) -> dict[str, str]:
    return {str(key or "").strip(): str(value or "").strip() for key, value in row.items()}


def row_to_namespace(row: dict[str, str], out_dir: Path) -> SimpleNamespace:
    required = ["suite", "system", "cases", "baseline_reference"]
    missing = [field for field in required if not row.get(field)]
    if missing:
        raise ValueError(f"missing required field(s) {missing} for row: {row}")
    model_slug = slugify(row.get("model", "deterministic"))
    default_name = f"spider2_external_baseline_{slugify(row['system'])}_{model_slug}.json"
    out = row.get("out") or str(out_dir / default_name)
    return SimpleNamespace(
        out=out,
        suite=row["suite"],
        system=row["system"],
        model=row.get("model", ""),
        cases=int(float(row["cases"])),
        er=row.get("er", ""),
        re=row.get("re", ""),
        ser=row.get("ser", ""),
        ser_evaluable=row.get("ser_evaluable", ""),
        run_ok=row.get("run_ok", ""),
        semantic_pass=row.get("semantic_pass", ""),
        gold_evaluable=row.get("gold_evaluable", ""),
        gold_artifact_issues=row.get("gold_artifact_issues", ""),
        avg_latency_s=row.get("avg_latency_s", ""),
        baseline_reference=row["baseline_reference"],
        report_label=row.get("report_label", ""),
        implementation_type=row.get("implementation_type", "official_external_reproduction")
        or "official_external_reproduction",
        official_reproduction=row.get("official_reproduction", "True") or "True",
        source_repo=row.get("source_repo", ""),
        source_commit=row.get("source_commit", ""),
        source_command=row.get("source_command", ""),
        source_artifact=row.get("source_artifact", ""),
        notes=row.get("notes", ""),
    )


def register_csv(path: Path, out_dir: Path) -> list[Path]:
    written: list[Path] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            row = clean_row(raw)
            if not any(row.values()):
                continue
            args = row_to_namespace(row, out_dir)
            payload = build_payload(args)
            out = Path(args.out)
            if not out.is_absolute():
                out = out_dir / out
            write_payload(payload, out)
            written.append(out)
    return written


def write_template(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "suite": "spider2-sqlite",
                "system": "mac_sql_official",
                "model": "gpt-4.1",
                "cases": "135",
                "er": "88.15",
                "re": "72.59",
                "ser": "70.37",
                "baseline_reference": "MAC-SQL official",
                "report_label": "MAC-SQL official reproduction",
                "implementation_type": "official_external_reproduction",
                "official_reproduction": "True",
                "source_repo": "https://github.com/wbbeyourself/MAC-SQL",
                "source_commit": "<commit-or-tag>",
                "source_command": "bash run.sh ...",
                "source_artifact": "/path/to/original/results.json",
                "notes": "Replace this row with verified external metrics.",
            }
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Bulk-register externally executed official baseline metrics from "
            "a CSV file as EC-SQL-compatible JSON artifacts."
        )
    )
    parser.add_argument("--csv", help="Input CSV with external baseline metrics.")
    parser.add_argument("--out-dir", required=True, help="Directory for generated JSON artifacts.")
    parser.add_argument("--write-template", help="Write a template CSV and exit.")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    if args.write_template:
        write_template(Path(args.write_template))
        print(f"[external-baselines] template: {args.write_template}")
        return 0
    if not args.csv:
        parser.error("--csv is required unless --write-template is used")
    written = register_csv(Path(args.csv), out_dir)
    for path in written:
        print(f"[external-baselines] wrote {path}")
    print(f"[external-baselines] registered {len(written)} artifact(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
