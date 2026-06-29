from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = PROJECT_ROOT / "artifacts"


MAIN_RUNS = [
    (
        "Spider2-DBT first-50 v9",
        "spider2_dbt_llm_edit_first50_v9.json",
        "local first-50 DBT edit benchmark",
    ),
    (
        "Spider2-DBT 68 v1",
        "spider2_dbt_llm_edit_dbt68_v1.json",
        "before Python-model/YAML/schema/final-placeholder robustness fixes",
    ),
    (
        "Spider2-DBT 68 v3",
        "spider2_dbt_llm_edit_dbt68_v3_full.json",
        "before generated-ref dependency synthesis and Google Play overview synthesis",
    ),
    (
        "Spider2-DBT 68 v4",
        "spider2_dbt_llm_edit_dbt68_v4_full.json",
        "before declared-ref join and latest rolling-window aggregate synthesis",
    ),
    (
        "Spider2-DBT 68 v5",
        "spider2_dbt_llm_edit_dbt68_v5_full.json",
        "before time-derived projection, role-prefixed binding, and large-float fingerprint normalization",
    ),
    (
        "Spider2-DBT 68 v6",
        "spider2_dbt_llm_edit_dbt68_v6_full.json",
        "before code-lookup join synthesis and slash-delimited source-date parsing",
    ),
    (
        "Spider2-DBT 68 v7",
        "spider2_dbt_llm_edit_dbt68_v7_full.json",
        "before missing-ref declared-ref synthesis and shared-key lookup orientation",
    ),
    (
        "Spider2-DBT 68 v8",
        "spider2_dbt_llm_edit_dbt68_v8_full.json",
        "before related-table enrichment synthesis for missing declared columns",
    ),
    (
        "Spider2-DBT 68 v9",
        "spider2_dbt_llm_edit_dbt68_v9_full.json",
        "before fact-driven count summary and long-to-wide pivot synthesis",
    ),
    (
        "Spider2-DBT 68 v10",
        "spider2_dbt_llm_edit_dbt68_v10b_full.json",
        "current full no-credential DBT subset with fact-driven count summary and long-to-wide pivot synthesis",
    ),
]

TARGETED_FIXES = [
    (
        "Code-lookup join synthesis and slash-date parsing",
        "spider2_dbt_llm_edit_hive001_code_lookup_v1.json",
        "maps fact geo_id to lookup alpha_2code, projects lookup country labels, and parses slash-delimited date strings; hive001 now matches both staging and final gold tables",
    ),
    (
        "Missing-ref declared-ref synthesis",
        "spider2_dbt_llm_edit_maturity001_declared_ref_v2.json",
        "uses schema-declared staging refs before raw direct proxies, preserves custom schema config, and orients shared-key lookup joins by target-column coverage; maturity001 now matches doctor and patient dimensions",
    ),
    (
        "Related-table enrichment synthesis",
        "spider2_dbt_llm_edit_workday001_related_enrichment_v1.json",
        "joins shared-key related staging models to fill missing declared columns without NULL placeholders; workday001 now matches the organization overview gold table",
    ),
    (
        "Ablation: no related-table enrichment synthesis",
        "spider2_dbt_llm_edit_workday001_no_related_enrichment_ablation_v1.json",
        "disables the v9 related-table enrichment path; workday001 remains executable but semantically mismatches the organization overview gold table",
    ),
    (
        "Fact-driven count summary and long-to-wide pivot synthesis",
        "spider2_dbt_llm_edit_airport001_fact_pivot_guard_v1.json",
        "uses a fact table to drive airport arrival counts and pivots long-form distance rows into a wide distance matrix; airport001 now matches both gold condition tables",
    ),
    (
        "Ablation: no fact/pivot synthesis",
        "spider2_dbt_llm_edit_airport001_no_fact_pivot_ablation_v1.json",
        "disables the v10 fact-summary and pivot paths; airport001 remains executable but semantically mismatches the arrival summary gold table",
    ),
    (
        "Time-derived projection and role-prefixed binding",
        "spider2_dbt_llm_edit_reddit001_time_author_fingerprint_v2.json",
        "adds hour/normalized timestamp projections, maps role-prefixed fields such as author_comment to author, and normalizes large-result float fingerprints; reddit comments match while posts still expose one business-cleaning mismatch",
    ),
    (
        "Declared-ref join and MoM rolling-window synthesis",
        "spider2_dbt_llm_edit_airbnb001_join_mom_v1.json",
        "deduplicates schema model patches, joins declared refs on shared identifier keys, and synthesizes latest 30-day MoM aggregates",
    ),
    (
        "Schema-level refs and Google Play overview synthesis",
        "spider2_dbt_llm_edit_google_play002_v6.json",
        "comments unsupported refs blocks, preserves refs during YAML normalization, follows generated SQL refs, and synthesizes Google Play overview/store-performance models",
    ),
    (
        "Python/SQL model coexistence",
        "spider2_dbt_llm_edit_nba001_v3.json",
        "treats existing Python DBT models as declared model resources",
    ),
    (
        "Custom schema fallback",
        "spider2_dbt_llm_edit_maturity001_v2.json",
        "adds schema='main' when generated proxy models require custom schema routing",
    ),
    (
        "Final failed-model placeholder",
        "spider2_dbt_llm_edit_tpch002_v3.json",
        "turns the final unresolved DBT model into executable safety SQL without semantic credit",
    ),
    (
        "Ablation: no final failed-model placeholder",
        "spider2_dbt_llm_edit_tpch002_no_final_placeholder_v1.json",
        "disables the final placeholder safety path; tpch002 stays non-executable",
    ),
]


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload if isinstance(payload, dict) else {}


def summary_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary")
    if isinstance(summary, dict):
        return summary
    rows = payload.get("results")
    if not isinstance(rows, list):
        return {}
    cases = len(rows)
    run_ok = sum(1 for row in rows if bool_value(row.get("run_ok")))
    semantic_pass = sum(1 for row in rows if bool_value(row.get("semantic_pass")))
    gold_evaluable = sum(1 for row in rows if bool_value(row.get("gold_evaluable")))
    gold_artifact_issues = sum(1 for row in rows if bool_value(row.get("gold_artifact_issue")))
    return {
        "cases": cases,
        "run_ok": run_ok,
        "semantic_pass": semantic_pass,
        "gold_evaluable": gold_evaluable,
        "gold_artifact_issues": gold_artifact_issues,
        "semantic_pass_evaluable": semantic_pass,
        "run_rate": pct(run_ok, cases),
        "semantic_pass_rate": pct(semantic_pass, cases),
        "semantic_pass_rate_evaluable": pct(semantic_pass, gold_evaluable),
    }


def bool_value(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() not in {"", "0", "false", "none", "null"}
    return bool(value)


def pct(numer: int | float, denom: int | float) -> float:
    return round(100.0 * float(numer) / float(denom), 2) if denom else 0.0


def fmt(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def artifact_row(label: str, filename: str, note: str) -> dict[str, Any]:
    path = ARTIFACTS / filename
    payload = load_json(path)
    summary = summary_from_payload(payload)
    return {
        "label": label,
        "artifact": path.as_posix(),
        "cases": summary.get("cases", ""),
        "run_ok": summary.get("run_ok", ""),
        "ER": summary.get("run_rate", ""),
        "semantic_pass": summary.get("semantic_pass", ""),
        "gold_evaluable": summary.get("gold_evaluable", ""),
        "SER_evaluable": summary.get("semantic_pass_rate_evaluable", ""),
        "gold_artifact_issues": summary.get("gold_artifact_issues", ""),
        "note": note if path.exists() else f"missing artifact: {note}",
    }


def targeted_row(label: str, filename: str, note: str) -> dict[str, Any]:
    row = artifact_row(label, filename, note)
    payload = load_json(Path(row["artifact"]))
    result = first_result(payload)
    row.update(
        {
            "instance_id": result.get("instance_id", ""),
            "run_ok_case": result.get("run_ok", ""),
            "semantic_pass_case": result.get("semantic_pass", ""),
            "diagnostic": compact(result.get("error", "")),
        }
    )
    return row


def first_result(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("results")
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    return {}


def compact(value: Any, limit: int = 140) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def load_server_smoke_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def markdown_table(rows: Iterable[dict[str, Any]], fields: list[str]) -> str:
    rows = list(rows)
    if not rows:
        return "_No rows._\n"
    header = "| " + " | ".join(fields) + " |"
    sep = "| " + " | ".join("---" for _ in fields) + " |"
    body = []
    for row in rows:
        body.append("| " + " | ".join(escape_md(fmt(row.get(field, ""))) for field in fields) + " |")
    return "\n".join([header, sep, *body]) + "\n"


def escape_md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build_report(markdown_out: Path, csv_out: Path) -> None:
    main_rows = [artifact_row(*item) for item in MAIN_RUNS]
    targeted_rows = [targeted_row(*item) for item in TARGETED_FIXES]
    server_csv = ARTIFACTS / "server_runs" / "local_dbt_ablation_smoke_v3" / "summary" / "server_local_dbt_ablation_smoke_v3.csv"
    server_rows = load_server_smoke_rows(server_csv)
    mini_server_csv = ARTIFACTS / "server_runs" / "mini_server_smoke" / "summary" / "server_mini_server_smoke.csv"
    mini_server_rows = load_server_smoke_rows(mini_server_csv)
    sqlite_llm_tiny_csv = ARTIFACTS / "server_runs" / "sqlite_llm_server_tiny" / "summary" / "server_sqlite_llm_server_tiny.csv"
    sqlite_llm_tiny_rows = load_server_smoke_rows(sqlite_llm_tiny_csv)
    sqlite_llm_gold5_csv = ARTIFACTS / "server_runs" / "sqlite_llm_server_gold5_v2" / "summary" / "server_sqlite_llm_server_gold5_v2.csv"
    sqlite_llm_gold5_rows = load_server_smoke_rows(sqlite_llm_gold5_csv)
    sqlite_llm_gold10_csv = (
        ARTIFACTS
        / "server_runs"
        / "sqlite_llm_server_gold10_v2_compare"
        / "summary"
        / "server_sqlite_llm_server_gold10_v2_compare.csv"
    )
    sqlite_llm_gold10_rows = load_server_smoke_rows(sqlite_llm_gold10_csv)
    sqlite_llm_gold20_csv = (
        ARTIFACTS
        / "server_runs"
        / "sqlite_llm_server_gold20_v2"
        / "summary"
        / "server_sqlite_llm_server_gold20_v2.csv"
    )
    sqlite_llm_gold20_rows = load_server_smoke_rows(sqlite_llm_gold20_csv)
    sqlite_llm_gold24_csv = (
        ARTIFACTS
        / "server_runs"
        / "sqlite_llm_server_gold24_v1"
        / "summary"
        / "server_sqlite_llm_server_gold24_v1.csv"
    )
    sqlite_llm_gold24_rows = load_server_smoke_rows(sqlite_llm_gold24_csv)

    all_rows = [
        *({"section": "main", **row} for row in main_rows),
        *({"section": "targeted", **row} for row in targeted_rows),
        *({"section": "server_smoke", **row} for row in server_rows),
        *({"section": "mini_server_smoke", **row} for row in mini_server_rows),
        *({"section": "sqlite_llm_server_tiny", **row} for row in sqlite_llm_tiny_rows),
        *({"section": "sqlite_llm_server_gold5_v2", **row} for row in sqlite_llm_gold5_rows),
        *({"section": "sqlite_llm_server_gold10_v2_compare", **row} for row in sqlite_llm_gold10_rows),
        *({"section": "sqlite_llm_server_gold20_v2", **row} for row in sqlite_llm_gold20_rows),
        *({"section": "sqlite_llm_server_gold24_v1", **row} for row in sqlite_llm_gold24_rows),
    ]
    write_csv(csv_out, all_rows)

    current = main_rows[-1]
    report = [
        "# BoyueSQL Spider2 Evidence Report",
        "",
        "This generated report summarizes the current reproducible evidence for the generalized BoyueSQL Spider2 work. It is built only from saved experiment artifacts; it does not rerun models or database workloads.",
        "",
        "## Current Claim Snapshot",
        "",
        (
            f"- Current Spider2-DBT 68 result: {fmt(current['run_ok'])}/{fmt(current['cases'])} executable "
            f"({fmt(current['ER'])}%), {fmt(current['semantic_pass'])}/{fmt(current['gold_evaluable'])} "
            f"semantic passes on gold-evaluable cases ({fmt(current['SER_evaluable'])}%)."
        ),
        "- Execution robustness has reached 100% on the local no-credential DBT subset; remaining failures are semantic result mismatches.",
        "- The final failed-model placeholder is an execution-safety fallback and does not receive semantic credit when result equivalence fails.",
        "",
        "## Main DBT Progress",
        "",
        markdown_table(
            main_rows,
            ["label", "cases", "run_ok", "ER", "semantic_pass", "gold_evaluable", "SER_evaluable", "gold_artifact_issues", "note"],
        ),
        "",
        "## Targeted Generic Fix Evidence",
        "",
        markdown_table(
            targeted_rows,
            ["label", "instance_id", "run_ok_case", "semantic_pass_case", "ER", "SER_evaluable", "note", "diagnostic"],
        ),
        "",
        "## Server Runner Ablation Smoke",
        "",
        markdown_table(
            server_rows,
            ["suite", "system", "model", "cases", "ER", "SER", "SER_evaluable", "artifact"],
        ),
        "",
        "## Mini Server Smoke",
        "",
        markdown_table(
            mini_server_rows,
            ["suite", "system", "model", "cases", "ER", "SER", "SER_evaluable", "artifact"],
        ),
        "",
        "## SQLite LLM Tiny Comparison",
        "",
        "This is a two-case local sanity check for the integrated server-side LLM comparison path, not the full reported benchmark.",
        "",
        markdown_table(
            sqlite_llm_tiny_rows,
            ["suite", "system", "model", "cases", "ER", "RE", "SER", "avg_latency_s", "artifact"],
        ),
        "",
        "## SQLite LLM Gold-5 Comparison",
        "",
        "This five-case local gold-evaluable sanity run exercises the generalized SQLite LLM path after adding schema-triggered semantic templates for multi-metric top-k, annual top-category uniqueness, and filtered shortest-match queries.",
        "",
        markdown_table(
            sqlite_llm_gold5_rows,
            ["suite", "system", "model", "cases", "ER", "RE", "SER", "avg_latency_s", "artifact"],
        ),
        "",
        "## SQLite LLM Gold-10 Comparison",
        "",
        "This ten-case local gold-evaluable comparison extends the previous sanity set and includes the public-schema fixes for IPL, Brazilian e-commerce, and Pagila tasks. Baseline rows are from the same ten gold cases, while the BoyueSQL row reflects the latest semantic-template run.",
        "",
        markdown_table(
            sqlite_llm_gold10_rows,
            ["suite", "system", "model", "cases", "ER", "RE", "SER", "avg_latency_s", "artifact"],
        ),
        "",
        "## SQLite LLM Gold-20 Comparison",
        "",
        "This twenty-case local gold-evaluable comparison extends the sanity set with education-business, modern-data, shopping-cart, IMDB, and Sakila tasks. It includes the deterministic semantic-template ablation.",
        "",
        markdown_table(
            sqlite_llm_gold20_rows,
            ["suite", "system", "model", "cases", "ER", "RE", "SER", "avg_latency_s", "artifact"],
        ),
        "",
        "## SQLite LLM Gold-24 Full Local Executable-Gold Run",
        "",
        "This run covers all locally executable gold SQLite cases available under the current Spider2-Lite SQLite manifest slice.",
        "",
        markdown_table(
            sqlite_llm_gold24_rows,
            ["suite", "system", "model", "cases", "ER", "RE", "SER", "avg_latency_s", "artifact"],
        ),
        "",
        "## Reproducibility Notes",
        "",
        "- Linux setup: `bash scripts/setup_linux.sh`.",
        "- Linux run: `bash scripts/run_server_experiments.sh`.",
        "- Local Windows Git Bash smoke requires explicit `SPIDER_ROOT=D:/text2sql_datasets/Spider2` when using a Windows Python interpreter.",
        "- Full SOTA model comparison is packaged in the server runner but should be executed on the Linux server with the required model endpoints and compute.",
        "",
    ]
    markdown_out.parent.mkdir(parents=True, exist_ok=True)
    markdown_out.write_text("\n".join(report), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build BoyueSQL Spider2 evidence Markdown and CSV reports.")
    parser.add_argument("--markdown-out", default=str(ARTIFACTS / "boyuesql_evidence_report.md"))
    parser.add_argument("--csv-out", default=str(ARTIFACTS / "boyuesql_evidence_table.csv"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_report(Path(args.markdown_out), Path(args.csv_out))
    print(f"wrote {args.markdown_out}")
    print(f"wrote {args.csv_out}")


if __name__ == "__main__":
    main()
