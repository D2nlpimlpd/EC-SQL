from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence


def load_json_any(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def infer_suite(path: Path, payload: Any) -> str:
    name = path.name.lower()
    if "dbt_llm_edit" in name:
        return "spider2-dbt-edit"
    if "dbt_experiment" in name or "dbt_existing_project" in name:
        return "spider2-dbt"
    if "dbt_smoke" in name:
        return "spider2-dbt-smoke"
    if "sqlite_experiment" in name or "sqlite_runner" in name or (
        name.startswith("spider2_sqlite_") and "sqlite_smoke" not in name
    ):
        return "spider2-sqlite"
    if "sqlite_smoke" in name:
        return "spider2-sqlite-smoke"
    if isinstance(payload, dict):
        settings = payload.get("settings")
        if isinstance(settings, dict):
            return str(settings.get("suite") or settings.get("setting") or "unknown")
    return "unknown"


def result_rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("results")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def resolve_inputs(patterns: Sequence[str]) -> List[Path]:
    paths: List[Path] = []
    for pattern in patterns:
        matches: List[str] = []
        for candidate in path_pattern_candidates(pattern):
            matches = glob.glob(candidate)
            if matches:
                break
        if not matches:
            for candidate in path_pattern_candidates(pattern):
                direct = Path(candidate)
                if direct.exists():
                    matches = [str(direct)]
                    break
        paths.extend(Path(match) for match in matches)
    return sorted({path.resolve() for path in paths if path.exists() and path.is_file()})


def path_pattern_candidates(pattern: str) -> List[str]:
    candidates = [pattern]
    if os.name == "nt" and len(pattern) > 3 and pattern[0] == "/" and pattern[2] == "/":
        drive = pattern[1]
        if drive.isalpha():
            candidates.append(drive.upper() + ":" + pattern[2:])
    return candidates


def boolish(value: Any) -> bool:
    return bool(value) and str(value).lower() not in {"false", "0", "none", "null"}


def first_nonempty(*values: Any) -> str:
    for value in values:
        if value not in (None, "", [], {}):
            if isinstance(value, (list, tuple)):
                return "; ".join(str(item) for item in value if item not in (None, ""))
            return str(value)
    return ""


def infer_system_from_filename(path: Path) -> str:
    name = path.stem
    for token in (
        "spider2_",
        "sqlite_",
        "dbt_",
        "llm_edit_",
        "experiment_",
        "smoke_",
    ):
        name = name.replace(token, "")
    return name


def row_system(row: Dict[str, Any], path: Path) -> str:
    system = str(row.get("system") or "")
    if system in {"dbt_no_llm_edit", "dbt_llm_edit"}:
        inferred = infer_system_from_filename(path)
        if inferred:
            return inferred
    return system or infer_system_from_filename(path) or ""


def row_model(row: Dict[str, Any]) -> str:
    return str(row.get("model") or "")


def compact(text: str, limit: int = 500) -> str:
    return " ".join((text or "").split())[:limit]


def text_from_nested(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts: List[str] = []
        for key in (
            "error",
            "stderr_tail",
            "stdout_tail",
            "llm_error",
            "notes",
            "message",
            "reason",
        ):
            item = value.get(key)
            if item:
                parts.append(text_from_nested(item))
        return "\n".join(part for part in parts if part)
    if isinstance(value, list):
        return "\n".join(text_from_nested(item) for item in value)
    return ""


def collect_error_text(row: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in (
        "skip_reason",
        "exec_error",
        "error",
        "probe_error",
        "gold_error",
        "start_error",
        "semantic_guard_errors",
    ):
        value = row.get(key)
        if value:
            parts.append(text_from_nested(value))
    for key in ("dbt", "dbt_deps"):
        value = row.get(key)
        if value:
            parts.append(text_from_nested(value))
    for table_result in row.get("table_results") or []:
        if isinstance(table_result, dict) and not table_result.get("match", True):
            table = table_result.get("table") or ""
            pred = table_result.get("pred_error") or ""
            gold = table_result.get("gold_error") or ""
            if pred or gold:
                parts.append(f"{table}: pred={pred} gold={gold}")
            else:
                parts.append(f"{table}: result mismatch")
    for probe in row.get("table_probes") or []:
        if not isinstance(probe, dict):
            continue
        table = probe.get("table") or ""
        for side in ("gold_probe", "start_probe"):
            result = probe.get(side) or {}
            if isinstance(result, dict) and not result.get("ok", True):
                parts.append(f"{table} {side}: {result.get('error', '')}")
    for round_info in row.get("rounds") or []:
        if not isinstance(round_info, dict):
            continue
        if round_info.get("llm_error"):
            parts.append(str(round_info.get("llm_error")))
        if not round_info.get("run_ok", True):
            parts.append(text_from_nested(round_info.get("dbt") or {}))
        for table_result in round_info.get("table_results") or []:
            if isinstance(table_result, dict) and not table_result.get("match", True):
                parts.append(f"{table_result.get('table', '')}: result mismatch")
    sql = str(row.get("sql") or "")
    if re.search(r"\bNULL\s+AS\b", sql, flags=re.IGNORECASE):
        parts.append("SQL contains NULL AS placeholder")
    return "\n".join(part for part in parts if part)


def classify_error(text: str, sql: str = "") -> str:
    haystack = f"{text}\n{sql}".lower()
    if re.search(r"\bnull\s+as\b", haystack):
        return "null_placeholder"
    if "timed out" in haystack or "timeout" in haystack or "readtimeout" in haystack:
        return "timeout"
    if "missing example directory" in haystack or "missing gold" in haystack or "missing predicted" in haystack or "gold sql missing" in haystack:
        return "missing_artifact"
    if "empty sql" in haystack or "no sql generated" in haystack or "no sql was generated" in haystack:
        return "empty_sql"
    if "ambiguous" in haystack:
        return "ambiguous_reference"
    if "result mismatch" in haystack or "no condition table matched" in haystack or "not match" in haystack:
        return "result_mismatch"
    if (
        "no such table" in haystack
        or "ora-00942" in haystack
        or "missing table" in haystack
        or "table with name" in haystack and "does not exist" in haystack
        or "object does not exist" in haystack
        or "not found" in haystack and ("table" in haystack or "relation" in haystack)
    ):
        return "missing_table"
    if "source not found" in haystack or "could not find ref" in haystack or "depends on a node named" in haystack:
        return "missing_ref"
    if (
        "no such column" in haystack
        or "invalid identifier" in haystack
        or "ora-00904" in haystack
        or "referenced column" in haystack
        or "unknown column" in haystack
        or "binder error" in haystack and "column" in haystack
    ):
        return "invalid_column"
    if (
        "conversion error" in haystack
        or "could not convert" in haystack
        or "type mismatch" in haystack
        or "no function matches" in haystack
        or "cannot compare values" in haystack
    ):
        return "type_mismatch"
    if "dbt deps" in haystack or "dependency" in haystack or "package" in haystack and "dbt" in haystack:
        return "dbt_dependency"
    if "compilation error" in haystack or "parsing error" in haystack or "dbt run failed" in haystack and "compile" in haystack:
        return "dbt_compile"
    if "syntax error" in haystack or "parse error" in haystack or re.search(r"\bnear\s+\"?.+\"?\s*:", haystack):
        return "syntax_error"
    if "semantic guard" in haystack or "guard" in haystack and ("requires" in haystack or "missing" in haystack):
        return "semantic_guard"
    if "json" in haystack and ("response" in haystack or "llm" in haystack) or "llm edit failed" in haystack:
        return "model_output"
    return "generic_failure"


def failure_stage(row: Dict[str, Any], suite: str) -> str:
    if row.get("skipped"):
        return "skipped"
    if suite.endswith("smoke"):
        return "smoke"
    if "dbt" in suite:
        if has_gold_artifact_issue(row):
            return "gold_artifact"
        if not boolish(row.get("run_ok")):
            return "dbt_run"
        if not boolish(row.get("semantic_pass")):
            return "semantic"
        return "passed"
    if "exec_ok" in row and not boolish(row.get("exec_ok")):
        return "execution"
    if not boolish(row.get("no_null_placeholder", True)):
        return "semantic"
    if row.get("semantic_guard_errors"):
        return "semantic_guard"
    if "result_exact" in row and not boolish(row.get("result_exact")):
        return "result_mismatch"
    if "semantic_pass" in row and not boolish(row.get("semantic_pass")):
        return "semantic"
    return "passed"


def has_gold_artifact_issue(row: Dict[str, Any]) -> bool:
    if boolish(row.get("gold_artifact_issue")):
        return True
    error = str(row.get("error") or "").lower()
    if "missing gold duckdb" in error:
        return True
    for table in row.get("table_results") or []:
        if boolish(table.get("gold_artifact_issue")) or table.get("gold_ok") is False:
            return True
    return False


def smoke_failures(row: Dict[str, Any], suite: str) -> List[tuple[str, str, str]]:
    out: List[tuple[str, str, str]] = []
    if suite == "spider2-sqlite-smoke":
        if not boolish(row.get("probe_ok")):
            text = str(row.get("probe_error") or "schema probe failed")
            out.append(("schema_probe", classify_error(text), text))
        if not boolish(row.get("gold_exec_ok")):
            text = str(row.get("gold_error") or "gold SQL probe failed")
            out.append(("gold_probe", classify_error(text), text))
        return out
    if suite == "spider2-dbt-smoke":
        if not boolish(row.get("start_schema_ok")):
            text = str(row.get("start_error") or "starter schema failed")
            out.append(("start_schema", classify_error(text), text))
        if not boolish(row.get("gold_schema_ok")):
            text = str(row.get("gold_error") or "gold schema failed")
            out.append(("gold_schema", classify_error(text), text))
        probes = row.get("table_probes") or []
        if probes:
            failed = [
                probe
                for probe in probes
                if isinstance(probe, dict)
                and (
                    not (probe.get("gold_probe") or {}).get("ok", True)
                    or not (probe.get("start_probe") or {}).get("ok", True)
                )
            ]
            if failed:
                text = text_from_nested(failed)
                out.append(("condition_probe", classify_error(text), text))
        return out
    return out


def failure_rows_for_artifact(path: Path, payload: Any) -> List[Dict[str, Any]]:
    suite = infer_suite(path, payload)
    rows: List[Dict[str, Any]] = []
    for row in result_rows(payload):
        system = row_system(row, path)
        model = row_model(row)
        if suite.endswith("smoke"):
            failures = smoke_failures(row, suite)
            for stage, error_class, text in failures:
                rows.append(make_failure_row(path, suite, row, system, model, stage, error_class, text))
            continue
        stage = failure_stage(row, suite)
        if stage == "passed":
            continue
        text = collect_error_text(row)
        sql = str(row.get("sql") or "")
        if not text and stage == "result_mismatch":
            text = "result mismatch"
        elif not text and stage == "semantic":
            text = "semantic pass failed"
        error_class = classify_error(text, sql)
        if stage == "semantic_guard" and error_class == "generic_failure":
            error_class = "semantic_guard"
        if stage == "result_mismatch" and error_class == "generic_failure":
            error_class = "result_mismatch"
        rows.append(make_failure_row(path, suite, row, system, model, stage, error_class, text))
    return rows


def make_failure_row(
    path: Path,
    suite: str,
    row: Dict[str, Any],
    system: str,
    model: str,
    stage: str,
    error_class: str,
    text: str,
) -> Dict[str, Any]:
    return {
        "artifact": path.as_posix(),
        "suite": suite,
        "instance_id": row.get("instance_id", ""),
        "db": row.get("db", ""),
        "system": system,
        "model": model,
        "stage": stage,
        "error_class": error_class,
        "question": row.get("question", row.get("instruction", "")),
        "error": compact(text),
        "sql": compact(str(row.get("sql") or ""), 500),
        "best_round": row.get("best_round", ""),
        "latency_s": row.get("latency_s", row.get("elapsed_sec", "")),
    }


def summarize_failures(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Counter[tuple[str, str, str, str, str]] = Counter()
    for row in rows:
        counts[
            (
                str(row["suite"]),
                str(row["system"]),
                str(row["model"]),
                str(row["stage"]),
                str(row["error_class"]),
            )
        ] += 1
    summary: List[Dict[str, Any]] = []
    for (suite, system, model, stage, error_class), count in counts.most_common():
        summary.append(
            {
                "suite": suite,
                "system": system,
                "model": model,
                "stage": stage,
                "error_class": error_class,
                "count": count,
            }
        )
    return summary


def write_csv(path: Path, rows: Sequence[Dict[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def escape_md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def markdown_table(rows: Sequence[Dict[str, Any]], fields: Sequence[str], limit: int = 25) -> str:
    if not rows:
        return "_No rows._\n"
    selected = list(rows[:limit])
    header = "| " + " | ".join(fields) + " |"
    sep = "| " + " | ".join("---" for _ in fields) + " |"
    body = [
        "| " + " | ".join(escape_md(row.get(field, "")) for field in fields) + " |"
        for row in selected
    ]
    if len(rows) > limit:
        body.append(f"| ... | +{len(rows) - limit} more rows |" + " |" * (len(fields) - 2))
    return "\n".join([header, sep, *body]) + "\n"


def recommendations(summary_rows: Sequence[Dict[str, Any]]) -> List[str]:
    stages = {str(row.get("stage")) for row in summary_rows}
    classes = {
        str(row.get("error_class"))
        for row in summary_rows
        if str(row.get("stage")) != "gold_artifact"
    }
    recs: List[str] = []
    if "gold_artifact" in stages:
        recs.append(
            "Gold-artifact failures: verify the Spider2 gold database or condition-table metadata before treating the case as a method failure."
        )
    if "invalid_column" in classes:
        recs.append(
            "Invalid-column failures: strengthen column ownership evidence in schema-KG retrieval and include owner-table repair hints before regeneration."
        )
    if "missing_table" in classes or "missing_ref" in classes:
        recs.append(
            "Missing-table/ref failures: expand relation closure and add deterministic DBT source/ref placeholders only when the live/starter catalog proves the dependency."
        )
    if "syntax_error" in classes or "type_mismatch" in classes:
        recs.append(
            "Syntax/type failures: add deterministic dialect-level rewrites before invoking another LLM repair round."
        )
    if "result_mismatch" in classes or "semantic_guard" in classes:
        recs.append(
            "Semantic failures: improve result-grounded templates, required table/column coverage checks, and few-shot examples for structurally similar tasks."
        )
    if "null_placeholder" in classes:
        recs.append(
            "NULL placeholders: keep schema-only fallback as an execution-safety path, but never count it as semantic success."
        )
    if "timeout" in classes or "model_output" in classes or "empty_sql" in classes:
        recs.append(
            "Model-output failures: increase LLM timeout/num_predict, prefer Ollama chat mode, and add stricter SQL/JSON extraction guards."
        )
    if not recs:
        recs.append("No recurring failure class was detected in the parsed artifacts.")
    return recs


def write_markdown(
    path: Path,
    *,
    artifacts: Sequence[Path],
    summary_rows: Sequence[Dict[str, Any]],
    failure_rows: Sequence[Dict[str, Any]],
    parse_failures: Sequence[str],
) -> None:
    summary_fields = ["suite", "system", "model", "stage", "error_class", "count"]
    case_fields = ["suite", "instance_id", "system", "model", "stage", "error_class", "error"]
    lines = [
        "# Failure Diagnostics",
        "",
        f"Artifacts parsed: {len(artifacts)}",
        f"Failure rows: {len(failure_rows)}",
        "",
        "## Failure Summary",
        "",
        markdown_table(summary_rows, summary_fields, limit=40),
        "",
        "## Recommended Next Fixes",
        "",
        *[f"- {item}" for item in recommendations(summary_rows)],
        "",
        "## Failure Cases",
        "",
        markdown_table(failure_rows, case_fields, limit=40),
    ]
    if parse_failures:
        lines.extend(["", "## Parse Failures", "", *[f"- {item}" for item in parse_failures]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose EC-SQL/Spider2 experiment failures")
    parser.add_argument(
        "--inputs",
        nargs="+",
        default=["artifacts/spider2*.json"],
        help="JSON artifact paths or glob patterns",
    )
    parser.add_argument("--out-dir", default="artifacts/experiment_summary")
    parser.add_argument("--name", default="failure_diagnostics")
    args = parser.parse_args()

    artifacts = resolve_inputs(args.inputs)
    failure_rows: List[Dict[str, Any]] = []
    parse_failures: List[str] = []
    for path in artifacts:
        try:
            failure_rows.extend(failure_rows_for_artifact(path, load_json_any(path)))
        except Exception as exc:
            parse_failures.append(f"{path}: {type(exc).__name__}: {exc}")

    summary_rows = summarize_failures(failure_rows)
    out_dir = Path(args.out_dir)
    summary_fields = ["suite", "system", "model", "stage", "error_class", "count"]
    case_fields = [
        "artifact",
        "suite",
        "instance_id",
        "db",
        "system",
        "model",
        "stage",
        "error_class",
        "question",
        "error",
        "sql",
        "best_round",
        "latency_s",
    ]
    write_csv(out_dir / f"{args.name}.csv", summary_rows, summary_fields)
    write_csv(out_dir / f"{args.name}_cases.csv", failure_rows, case_fields)
    write_markdown(
        out_dir / f"{args.name}.md",
        artifacts=artifacts,
        summary_rows=summary_rows,
        failure_rows=failure_rows,
        parse_failures=parse_failures,
    )
    print(f"failure summary: {(out_dir / f'{args.name}.csv').resolve()}")
    print(f"failure cases: {(out_dir / f'{args.name}_cases.csv').resolve()}")
    print(f"markdown: {(out_dir / f'{args.name}.md').resolve()}")
    if parse_failures:
        print(f"parse failures: {len(parse_failures)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
