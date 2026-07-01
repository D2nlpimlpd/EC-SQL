from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_server_evidence_report import best_row, build_report, run_paths


def read_evidence(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"missing evidence CSV: {path}")
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


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


def pct(value: Any) -> str:
    return f"{number(value):.2f}\\%"


def model_text(row: dict[str, str] | None) -> str:
    if not row:
        return ""
    model = row.get("model") or "deterministic"
    return f"\\texttt{{{latex_escape(model)}}}" if model != "deterministic" else "deterministic"


def latex_escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\textbackslash{}")
        .replace("&", "\\&")
        .replace("%", "\\%")
        .replace("_", "\\_")
        .replace("#", "\\#")
    )


def row_ser(row: dict[str, str] | None) -> str:
    if not row:
        return "0.00\\%"
    return pct(row.get("SER") or row.get("SER_evaluable"))


def render_abstract(run_id: str, rows: list[dict[str, str]]) -> str:
    sqlite = best_row(rows, categories={"sqlite_ecsql_full"})
    baseline = best_row(rows, categories={"sqlite_sota_baseline"})
    dbt = best_row(rows, categories={"dbt_ecsql_full"})
    sqlite_ablation_count = sum(1 for row in rows if row.get("category") == "sqlite_ablation")
    dbt_ablation_count = sum(1 for row in rows if row.get("category") == "dbt_ablation")
    baseline_count = sum(1 for row in rows if row.get("category") == "sqlite_sota_baseline")
    baseline_models = {
        row.get("model", "")
        for row in rows
        if row.get("category") == "sqlite_sota_baseline" and row.get("model")
    }
    sqlite_cases = integer(sqlite.get("cases") if sqlite else 0)
    dbt_cases = integer(dbt.get("cases") if dbt else 0)
    sqlite_model = model_text(sqlite)
    best_baseline_label = latex_escape((baseline or {}).get("report_label") or (baseline or {}).get("system") or "baseline")
    best_baseline_model = model_text(baseline)
    run_label = latex_escape(run_id)
    return f"""\\begin{{abstract}}
Enterprise text-to-SQL systems must generalize across heterogeneous schemas,
execution engines, and analytical project structures while remaining usable
under local-resource and privacy constraints. We present \\emph{{EC-SQL}}, a
generalized evidence-grounded text-to-SQL framework that removes
deployment-specific schema assumptions and treats SQL generation as
evidence-constrained program synthesis. EC-SQL constructs schema evidence
from database dictionaries and project metadata, retrieves bounded
table--column--relation context through a customized RagAnything/LightRAG
schema knowledge graph, validates generated identifiers against active
catalogs, probes candidate SQL before acceptance, and repairs dialect-specific
failures with typed DBMS feedback. For DBT-style analytical projects, the
framework further performs deterministic model synthesis, dependency
validation, dialect-aware type and syntax repair, custom-schema-aware fallback
generation, and result-set fingerprinting for large outputs.

We evaluate EC-SQL on Spider2 using the validated server run
\\texttt{{{run_label}}}. The completed matrix includes {sqlite_cases} locally
executable Spider2-Lite SQLite gold cases, {dbt_cases} Spider2-DBT tasks,
{sqlite_ablation_count} SQLite ablations, {dbt_ablation_count} DBT ablations,
and {baseline_count} SOTA-style baseline rows across {len(baseline_models)}
baseline models. On the SQLite gold subset, EC-SQL with {sqlite_model}
achieves {pct(sqlite.get('ER') if sqlite else 0)} execution rate,
{pct(sqlite.get('RE') if sqlite else 0)} exact result-match rate, and
{row_ser(sqlite)} semantic pass rate. On the DBT subset, the deterministic
EC-SQL pipeline achieves {pct(dbt.get('ER') if dbt else 0)} execution rate
and {row_ser(dbt)} semantic pass rate over the reported gold-evaluable cases.
The strongest SQLite baseline in this run, {best_baseline_label} with
{best_baseline_model}, reaches {row_ser(baseline)} semantic pass rate. These
results show that robust enterprise text-to-SQL depends not only on stronger
language models, but also on schema-grounded retrieval, live-catalog binding,
deterministic synthesis, dialect repair, dependency management, and
result-level semantic evidence. We additionally provide a Linux-ready server
package with one-click environment installation, Spider2 dataset preparation,
benchmark execution, service startup, ablation control, result-bundle
validation, and extensible hooks for full server-side SOTA comparisons.
\\end{{abstract}}
"""


def build_abstract(
    run_id: str,
    out_dir: str = "",
    output: str = "",
    allow_pending: bool = False,
) -> Path:
    check, paths = build_report(run_id, out_dir, allow_pending=allow_pending)
    if check.status != "PASS" and not allow_pending:
        raise RuntimeError(check.evidence)
    rows = read_evidence(paths.evidence_csv)
    target = Path(output) if output else run_paths(run_id, out_dir).run_dir / "summary" / f"server_{run_id}_abstract.tex"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_abstract(run_id, rows), encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a paper abstract from a validated server evidence matrix.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--allow-pending", action="store_true")
    args = parser.parse_args()
    try:
        path = build_abstract(args.run_id, args.out_dir, args.output, args.allow_pending)
    except Exception as exc:
        print(f"[server-abstract] ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"[server-abstract] abstract: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
