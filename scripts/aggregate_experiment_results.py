from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.baseline_manifest import baseline_metadata


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload if isinstance(payload, dict) else {}


def infer_suite(path: Path, payload: Dict[str, Any]) -> str:
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
    settings = payload.get("settings")
    if isinstance(settings, dict):
        return str(settings.get("suite") or settings.get("setting") or "unknown")
    return "unknown"


def result_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = payload.get("results")
    return rows if isinstance(rows, list) else []


def pct(numer: int, denom: int) -> float:
    return round(100.0 * numer / denom, 2) if denom else 0.0


def boolish(value: Any) -> bool:
    return bool(value) and str(value).lower() not in {"false", "0", "none", "null"}


def metric_from_rows(rows: Sequence[Dict[str, Any]], key: str) -> float:
    return pct(sum(1 for row in rows if boolish(row.get(key))), len(rows))


def summary_rows_for_artifact(path: Path, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    suite = infer_suite(path, payload)
    rows = result_rows(payload)
    summary = payload.get("summary")
    out: List[Dict[str, Any]] = []

    if isinstance(summary, dict) and summary and all(isinstance(v, dict) for v in summary.values()):
        for system, metrics in summary.items():
            system_rows = [row for row in rows if str(row.get("system") or "") == str(system)]
            model = first_nonempty(row.get("model") for row in system_rows) or str(metrics.get("model") or "")
            out.append(
                normalize_summary_row(
                    artifact=path,
                    suite=suite,
                    system=str(system),
                    model=model,
                    metrics=metrics,
                    rows=system_rows,
                )
            )
        return out

    if isinstance(summary, dict):
        system = first_nonempty(row.get("system") for row in rows) or infer_system_from_filename(path)
        model = first_nonempty(row.get("model") for row in rows) or str(summary.get("model") or "")
        out.append(
            normalize_summary_row(
                artifact=path,
                suite=suite,
                system=system,
                model=model,
                metrics=summary,
                rows=rows,
            )
        )
        return out

    system = first_nonempty(row.get("system") for row in rows) or infer_system_from_filename(path)
    model = first_nonempty(row.get("model") for row in rows)
    out.append(
        normalize_summary_row(
            artifact=path,
            suite=suite,
            system=system,
            model=model,
            metrics={},
            rows=rows,
        )
    )
    return out


def normalize_summary_row(
    *,
    artifact: Path,
    suite: str,
    system: str,
    model: str,
    metrics: Dict[str, Any],
    rows: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    cases = int(metrics.get("cases") or len(rows) or 0)
    er = first_number(metrics, "ER", "er", "run_rate", "probe_rate")
    re_rate = first_number(metrics, "RE", "re", "result_exact_rate", "gold_exec_rate")
    ser = first_number(metrics, "SER", "ser", "semantic_pass_rate")
    ser_evaluable = first_number(metrics, "SER_evaluable", "ser_evaluable", "semantic_pass_rate_evaluable")
    gold_evaluable = metrics.get("gold_evaluable", "")
    gold_artifact_issues = metrics.get("gold_artifact_issues", "")
    if er is None and rows:
        if any("exec_ok" in row for row in rows):
            er = metric_from_rows(rows, "exec_ok")
        elif any("run_ok" in row for row in rows):
            er = metric_from_rows(rows, "run_ok")
    if re_rate is None and rows and any("result_exact" in row for row in rows):
        re_rate = metric_from_rows(rows, "result_exact")
    if ser is None and rows and any("semantic_pass" in row for row in rows):
        ser = metric_from_rows(rows, "semantic_pass")
    if ser_evaluable is None and rows and any("gold_evaluable" in row for row in rows):
        evaluable_rows = [row for row in rows if bool(row.get("gold_evaluable"))]
        ser_evaluable = metric_from_rows(evaluable_rows, "semantic_pass") if evaluable_rows else None
    if gold_evaluable == "" and rows and any("gold_evaluable" in row for row in rows):
        gold_evaluable = sum(1 for row in rows if bool(row.get("gold_evaluable")))
    if gold_artifact_issues == "" and rows and any("gold_artifact_issue" in row for row in rows):
        gold_artifact_issues = sum(1 for row in rows if bool(row.get("gold_artifact_issue")))
    display_system = system
    if system in {"dbt_no_llm_edit", "dbt_llm_edit"}:
        inferred = infer_system_from_filename(artifact)
        if inferred:
            display_system = inferred
    metadata = baseline_metadata(display_system, model)
    notes = str(metrics.get("notes") or "")
    if not notes:
        notes = metadata.get("baseline_note", "")
    implementation_type = str(metrics.get("implementation_type") or metadata.get("implementation_type", ""))
    official_reproduction = str(
        metrics.get("official_reproduction")
        if metrics.get("official_reproduction") not in (None, "")
        else metadata.get("official_reproduction", "")
    )
    baseline_reference = str(metrics.get("baseline_reference") or metadata.get("baseline_reference", ""))
    report_label = str(metrics.get("report_label") or metadata.get("report_label", display_system))
    return {
        "artifact": artifact.as_posix(),
        "suite": suite,
        "system": display_system,
        "model": model,
        "cases": cases,
        "ER": "" if er is None else er,
        "RE": "" if re_rate is None else re_rate,
        "SER": "" if ser is None else ser,
        "SER_evaluable": "" if ser_evaluable is None else ser_evaluable,
        "run_ok": metrics.get("run_ok", ""),
        "semantic_pass": metrics.get("semantic_pass", ""),
        "gold_evaluable": gold_evaluable,
        "gold_artifact_issues": gold_artifact_issues,
        "avg_latency_s": metrics.get("avg_latency_s", ""),
        "implementation_type": implementation_type,
        "official_reproduction": official_reproduction,
        "baseline_reference": baseline_reference,
        "report_label": report_label,
        "notes": notes,
    }


def first_number(metrics: Dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = metrics.get(key)
        if isinstance(value, (int, float)):
            return round(float(value), 4)
    return None


def first_nonempty(values: Iterable[Any]) -> str:
    for value in values:
        if value not in (None, ""):
            return str(value)
    return ""


def infer_system_from_filename(path: Path) -> str:
    name = path.stem
    for prefix in (
        "spider2_",
        "sqlite_",
        "dbt_",
        "llm_edit_",
        "experiment_",
    ):
        name = name.replace(prefix, "")
    return name


def case_rows_for_artifact(path: Path, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    suite = infer_suite(path, payload)
    cases: List[Dict[str, Any]] = []
    for row in result_rows(payload):
        if not isinstance(row, dict):
            continue
        system = str(row.get("system") or "")
        if system in {"dbt_no_llm_edit", "dbt_llm_edit"}:
            inferred = infer_system_from_filename(path)
            if inferred:
                system = inferred
        metadata = baseline_metadata(system, str(row.get("model") or ""))
        implementation_type = str(row.get("implementation_type") or metadata.get("implementation_type", ""))
        official_reproduction = str(
            row.get("official_reproduction")
            if row.get("official_reproduction") not in (None, "")
            else metadata.get("official_reproduction", "")
        )
        baseline_reference = str(row.get("baseline_reference") or metadata.get("baseline_reference", ""))
        exec_ok = row.get("exec_ok", row.get("run_ok", ""))
        result_exact = row.get("result_exact", "")
        semantic_pass = row.get("semantic_pass", "")
        gold_evaluable = row.get("gold_evaluable", "")
        gold_artifact_issue = row.get("gold_artifact_issue", "")
        cases.append(
            {
                "artifact": path.as_posix(),
                "suite": suite,
                "instance_id": row.get("instance_id", ""),
                "db": row.get("db", ""),
                "system": system,
                "model": row.get("model", ""),
                "implementation_type": implementation_type,
                "official_reproduction": official_reproduction,
                "baseline_reference": baseline_reference,
                "exec_ok": exec_ok,
                "result_exact": result_exact,
                "semantic_pass": semantic_pass,
                "gold_evaluable": gold_evaluable,
                "gold_artifact_issue": gold_artifact_issue,
                "latency_s": row.get("latency_s", row.get("elapsed_sec", "")),
                "best_round": row.get("best_round", ""),
                "error": compact_error(str(row.get("error") or row.get("exec_error") or "")),
            }
        )
    return cases


def compact_error(text: str, limit: int = 240) -> str:
    compact = " ".join((text or "").split())
    return compact[:limit]


def write_csv(path: Path, rows: Sequence[Dict[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(rows: Sequence[Dict[str, Any]], fields: Sequence[str]) -> str:
    if not rows:
        return "_No rows._\n"
    header = "| " + " | ".join(fields) + " |"
    sep = "| " + " | ".join("---" for _ in fields) + " |"
    body = []
    for row in rows:
        body.append("| " + " | ".join(escape_md(row.get(field, "")) for field in fields) + " |")
    return "\n".join([header, sep, *body]) + "\n"


def escape_md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate BoyueSQL/Spider2 experiment JSON artifacts")
    parser.add_argument(
        "--inputs",
        nargs="+",
        default=["artifacts/spider2*.json"],
        help="JSON artifact paths or glob patterns",
    )
    parser.add_argument("--out-dir", default="artifacts/experiment_summary")
    parser.add_argument("--summary-name", default="summary")
    args = parser.parse_args()

    artifacts = resolve_inputs(args.inputs)
    summary_rows: List[Dict[str, Any]] = []
    case_rows: List[Dict[str, Any]] = []
    failures: List[str] = []
    for path in artifacts:
        try:
            payload = load_json(path)
            summary_rows.extend(summary_rows_for_artifact(path, payload))
            case_rows.extend(case_rows_for_artifact(path, payload))
        except Exception as exc:
            failures.append(f"{path}: {type(exc).__name__}: {exc}")

    out_dir = Path(args.out_dir)
    summary_fields = [
        "suite",
        "system",
        "model",
        "cases",
        "ER",
        "RE",
        "SER",
        "SER_evaluable",
        "run_ok",
        "semantic_pass",
        "gold_evaluable",
        "gold_artifact_issues",
        "avg_latency_s",
        "implementation_type",
        "official_reproduction",
        "baseline_reference",
        "report_label",
        "notes",
        "artifact",
    ]
    case_fields = [
        "suite",
        "instance_id",
        "db",
        "system",
        "model",
        "implementation_type",
        "official_reproduction",
        "baseline_reference",
        "exec_ok",
        "result_exact",
        "semantic_pass",
        "gold_evaluable",
        "gold_artifact_issue",
        "latency_s",
        "best_round",
        "error",
        "artifact",
    ]
    write_csv(out_dir / f"{args.summary_name}.csv", summary_rows, summary_fields)
    write_csv(out_dir / f"{args.summary_name}_cases.csv", case_rows, case_fields)

    md = [
        "# Experiment Summary",
        "",
        markdown_table(summary_rows, summary_fields[:-1]),
        "",
        f"Artifacts parsed: {len(artifacts)}",
        f"Summary rows: {len(summary_rows)}",
        f"Case rows: {len(case_rows)}",
    ]
    if failures:
        md.extend(["", "## Parse Failures", "", *[f"- {item}" for item in failures]])
    (out_dir / f"{args.summary_name}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"summary: {(out_dir / f'{args.summary_name}.csv').resolve()}")
    print(f"cases: {(out_dir / f'{args.summary_name}_cases.csv').resolve()}")
    print(f"markdown: {(out_dir / f'{args.summary_name}.md').resolve()}")
    if failures:
        print("parse failures:", len(failures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
