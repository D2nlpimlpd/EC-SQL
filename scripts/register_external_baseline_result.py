from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def optional_float(value: str | None) -> float | str:
    if value in (None, ""):
        return ""
    text = str(value).strip().rstrip("%")
    return round(float(text), 4)


def optional_int(value: str | None) -> int | str:
    if value in (None, ""):
        return ""
    return int(float(str(value).strip()))


def slugify(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "baseline"


def build_payload(args: argparse.Namespace) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {
        "model": args.model,
        "cases": args.cases,
        "implementation_type": args.implementation_type,
        "official_reproduction": args.official_reproduction,
        "baseline_reference": args.baseline_reference,
        "report_label": args.report_label or args.system,
    }
    for out_key, attr in (
        ("ER", "er"),
        ("RE", "re"),
        ("SER", "ser"),
        ("SER_evaluable", "ser_evaluable"),
        ("avg_latency_s", "avg_latency_s"),
    ):
        value = optional_float(getattr(args, attr))
        if value != "":
            metrics[out_key] = value
    for out_key, attr in (
        ("run_ok", "run_ok"),
        ("semantic_pass", "semantic_pass"),
        ("gold_evaluable", "gold_evaluable"),
        ("gold_artifact_issues", "gold_artifact_issues"),
    ):
        value = optional_int(getattr(args, attr))
        if value != "":
            metrics[out_key] = value
    notes = " ".join(part for part in [args.notes, args.source_command] if part)
    if notes:
        metrics["notes"] = notes
    return {
        "settings": {
            "suite": args.suite,
            "registered_external_baseline": True,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_repo": args.source_repo,
            "source_commit": args.source_commit,
            "source_command": args.source_command,
            "source_artifact": args.source_artifact,
        },
        "summary": {args.system: metrics},
        "results": [],
    }


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", required=True, help="Output JSON artifact path.")
    parser.add_argument("--suite", required=True, help="Suite name, e.g. spider2-sqlite or spider2-dbt.")
    parser.add_argument("--system", required=True, help="System id, e.g. mac_sql_official.")
    parser.add_argument("--model", default="", help="Model used by the external baseline.")
    parser.add_argument("--cases", type=int, required=True, help="Number of evaluated cases.")
    parser.add_argument("--er", default="", help="Execution rate percentage.")
    parser.add_argument("--re", default="", help="Result exact-match rate percentage.")
    parser.add_argument("--ser", default="", help="Semantic pass rate percentage.")
    parser.add_argument("--ser-evaluable", default="", help="Semantic pass rate over evaluable cases.")
    parser.add_argument("--run-ok", default="", help="Raw executable-success count.")
    parser.add_argument("--semantic-pass", default="", help="Raw semantic-pass count.")
    parser.add_argument("--gold-evaluable", default="", help="Raw gold-evaluable count.")
    parser.add_argument("--gold-artifact-issues", default="", help="Raw gold artifact issue count.")
    parser.add_argument("--avg-latency-s", default="", help="Average latency in seconds.")
    parser.add_argument("--baseline-reference", required=True, help="Reference baseline name, e.g. MAC-SQL.")
    parser.add_argument("--report-label", default="", help="Reader-facing label for reports.")
    parser.add_argument("--implementation-type", default="official_external_reproduction")
    parser.add_argument("--official-reproduction", default="True")
    parser.add_argument("--source-repo", default="", help="Official repository URL or local path.")
    parser.add_argument("--source-commit", default="", help="Commit hash or release tag used.")
    parser.add_argument("--source-command", default="", help="Command used to run the external baseline.")
    parser.add_argument("--source-artifact", default="", help="Path/URI of the original baseline output.")
    parser.add_argument("--notes", default="", help="Additional caveats or scope notes.")


def write_payload(payload: Dict[str, Any], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Register an externally executed official baseline result as a "
            "EC-SQL-compatible experiment artifact. The script does not run "
            "the baseline; it records already verified metrics with explicit "
            "scope metadata for aggregation and paper evidence."
        )
    )
    add_arguments(parser)
    args = parser.parse_args()

    payload = build_payload(args)
    out = Path(args.out)
    write_payload(payload, out)
    print(f"[external-baseline] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
