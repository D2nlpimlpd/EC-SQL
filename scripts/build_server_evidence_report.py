from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.audit_goal_readiness import Check, check_server_matrix
from scripts.baseline_manifest import baseline_metadata


BASELINE_SYSTEMS = {
    "direct",
    "din_sql_style",
    "dail_sql_style",
    "self_debug_style",
    "mac_sql_style",
    "chess_style",
}


@dataclass(frozen=True)
class Paths:
    run_dir: Path
    summary_csv: Path
    evidence_csv: Path
    evidence_md: Path
    snippet_tex: Path


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def integer(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def fmt_pct(value: Any) -> str:
    if value in (None, ""):
        return ""
    return f"{number(value):.2f}"


def run_paths(run_id: str, out_dir: str = "") -> Paths:
    run_dir = Path(out_dir) if out_dir else PROJECT_ROOT / "artifacts" / "server_runs" / run_id
    summary_dir = run_dir / "summary"
    return Paths(
        run_dir=run_dir,
        summary_csv=summary_dir / f"server_{run_id}.csv",
        evidence_csv=summary_dir / f"server_{run_id}_evidence.csv",
        evidence_md=summary_dir / f"server_{run_id}_evidence.md",
        snippet_tex=summary_dir / f"server_{run_id}_results_snippet.tex",
    )


def read_summary(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"missing server summary: {path}")
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def category(row: dict[str, str]) -> str:
    suite = row.get("suite", "")
    system = row.get("system", "")
    implementation_type = row.get("implementation_type", "")
    official = str(row.get("official_reproduction", "")).lower() == "true"
    if suite == "spider2-sqlite" and system == "boyuesql":
        return "sqlite_boyuesql_full"
    if suite == "spider2-sqlite" and system.startswith("no_"):
        return "sqlite_ablation"
    if suite == "spider2-sqlite" and (
        system in BASELINE_SYSTEMS
        or implementation_type == "official_external_reproduction"
        or official
    ):
        return "sqlite_sota_baseline"
    if suite == "spider2-dbt" and "existing_project" in system:
        return "dbt_starter_baseline"
    if suite == "spider2-dbt" and "boyuesql_deterministic_full" in system:
        return "dbt_boyuesql_full"
    if suite == "spider2-dbt" and "ablation" in system:
        return "dbt_ablation"
    return "other"


def evidence_rows(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    for row in rows:
        cat = category(row)
        if cat == "other" or integer(row.get("cases")) <= 0:
            continue
        metadata = baseline_metadata(row.get("system", ""), row.get("model", ""))
        selected.append(
            {
                "category": cat,
                "suite": row.get("suite", ""),
                "system": row.get("system", ""),
                "model": row.get("model", ""),
                "implementation_type": row.get("implementation_type", "") or metadata.get("implementation_type", ""),
                "official_reproduction": row.get("official_reproduction", "")
                or metadata.get("official_reproduction", ""),
                "baseline_reference": row.get("baseline_reference", "") or metadata.get("baseline_reference", ""),
                "report_label": row.get("report_label", "") or metadata.get("report_label", row.get("system", "")),
                "cases": row.get("cases", ""),
                "ER": row.get("ER", ""),
                "RE": row.get("RE", ""),
                "SER": row.get("SER", ""),
                "SER_evaluable": row.get("SER_evaluable", ""),
                "run_ok": row.get("run_ok", ""),
                "semantic_pass": row.get("semantic_pass", ""),
                "gold_evaluable": row.get("gold_evaluable", ""),
                "avg_latency_s": row.get("avg_latency_s", ""),
                "baseline_note": row.get("notes", "") or metadata.get("baseline_note", ""),
                "artifact": row.get("artifact", ""),
            }
        )
    return sorted(selected, key=lambda r: (r["category"], r["suite"], r["system"], r["model"]))


def best_row(rows: Iterable[dict[str, str]], *, categories: set[str]) -> dict[str, str] | None:
    candidates = [row for row in rows if row.get("category") in categories]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda row: (
            number(row.get("SER") or row.get("SER_evaluable")),
            number(row.get("RE")),
            number(row.get("ER")),
            integer(row.get("cases")),
        ),
    )


def markdown_table(rows: list[dict[str, str]], fields: list[str]) -> str:
    if not rows:
        return "_No rows._\n"
    lines = [
        "| " + " | ".join(fields) + " |",
        "| " + " | ".join("---" for _ in fields) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(escape_md(row.get(field, "")) for field in fields) + " |")
    return "\n".join(lines) + "\n"


def escape_md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fields = [
        "category",
        "suite",
        "system",
        "model",
        "implementation_type",
        "official_reproduction",
        "baseline_reference",
        "report_label",
        "cases",
        "ER",
        "RE",
        "SER",
        "SER_evaluable",
        "run_ok",
        "semantic_pass",
        "gold_evaluable",
        "avg_latency_s",
        "baseline_note",
        "artifact",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def metric_sentence(row: dict[str, str] | None, label: str) -> str:
    if not row:
        return f"{label}: not available"
    ser_value = row.get("SER") or row.get("SER_evaluable")
    return (
        f"{label}: {row.get('report_label') or row.get('system', '')} "
        f"with {row.get('model', '') or 'deterministic'} "
        f"on {row.get('suite', '')} ({row.get('cases', '')} cases) achieved "
        f"ER={fmt_pct(row.get('ER'))}%, RE={fmt_pct(row.get('RE'))}%, "
        f"SER={fmt_pct(ser_value)}%."
    )


def latex_escape(text: str) -> str:
    return (
        text.replace("\\", "\\textbackslash{}")
        .replace("&", "\\&")
        .replace("%", "\\%")
        .replace("_", "\\_")
        .replace("#", "\\#")
    )


def latex_snippet(check: Check, rows: list[dict[str, str]]) -> str:
    best_sqlite = best_row(rows, categories={"sqlite_boyuesql_full"})
    best_baseline = best_row(rows, categories={"sqlite_sota_baseline"})
    best_dbt = best_row(rows, categories={"dbt_boyuesql_full"})
    sqlite_ablation_count = sum(1 for row in rows if row.get("category") == "sqlite_ablation")
    dbt_ablation_count = sum(1 for row in rows if row.get("category") == "dbt_ablation")
    lines = [
        "% Auto-generated by scripts/build_server_evidence_report.py",
        "\\paragraph{Server-side Spider2 evidence.}",
        latex_escape(metric_sentence(best_sqlite, "Best BoyueSQL SQLite result")),
        latex_escape(metric_sentence(best_baseline, "Best SQLite baseline")),
        latex_escape(metric_sentence(best_dbt, "Best DBT result")),
        latex_escape(
            f"The validated server matrix contains {sqlite_ablation_count} SQLite ablations "
            f"and {dbt_ablation_count} DBT ablations. Validation status: {check.status}."
        ),
        latex_escape(
            "SQLite comparison rows labelled as SOTA-style baselines are local "
            "prompt-style proxies unless official_reproduction is true in the "
            "generated evidence CSV."
        ),
        "",
    ]
    return "\n".join(lines)


def markdown_report(run_id: str, check: Check, rows: list[dict[str, str]]) -> str:
    fields = [
        "category",
        "suite",
        "system",
        "model",
        "implementation_type",
        "official_reproduction",
        "cases",
        "ER",
        "RE",
        "SER",
        "SER_evaluable",
        "avg_latency_s",
    ]
    best_sqlite = best_row(rows, categories={"sqlite_boyuesql_full"})
    best_baseline = best_row(rows, categories={"sqlite_sota_baseline"})
    best_dbt = best_row(rows, categories={"dbt_boyuesql_full"})
    lines = [
        f"# Server Evidence Report: {run_id}",
        "",
        f"Validation: **{check.status}**",
        "",
        check.evidence,
        "",
        "## Key Results",
        "",
        f"- {metric_sentence(best_sqlite, 'Best BoyueSQL SQLite result')}",
        f"- {metric_sentence(best_baseline, 'Best SQLite baseline')}",
        f"- {metric_sentence(best_dbt, 'Best DBT result')}",
        "",
        "SQLite rows marked as SOTA-style baselines are local prompt-style proxies "
        "unless `official_reproduction` is `true`; this prevents the server report "
        "from overstating official baseline reproduction.",
        "",
        "## Evidence Rows",
        "",
        markdown_table(rows, fields),
    ]
    return "\n".join(lines)


def build_report(run_id: str, out_dir: str = "", allow_pending: bool = False) -> tuple[Check, Paths]:
    paths = run_paths(run_id, out_dir)
    rows = evidence_rows(read_summary(paths.summary_csv))
    check = check_server_matrix(run_id)
    write_csv(paths.evidence_csv, rows)
    paths.evidence_md.write_text(markdown_report(run_id, check, rows), encoding="utf-8")
    paths.snippet_tex.write_text(latex_snippet(check, rows), encoding="utf-8")
    if check.status != "PASS" and not allow_pending:
        raise RuntimeError(check.evidence)
    return check, paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Build paper-ready evidence artifacts from a completed server run.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out-dir", default="", help="Optional server run directory override.")
    parser.add_argument("--allow-pending", action="store_true", help="Write partial evidence even when validation is pending.")
    args = parser.parse_args()

    try:
        check, paths = build_report(args.run_id, args.out_dir, args.allow_pending)
    except Exception as exc:
        print(f"[server-evidence] ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"[server-evidence] validation: {check.status}")
    print(f"[server-evidence] csv: {paths.evidence_csv}")
    print(f"[server-evidence] markdown: {paths.evidence_md}")
    print(f"[server-evidence] latex: {paths.snippet_tex}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
