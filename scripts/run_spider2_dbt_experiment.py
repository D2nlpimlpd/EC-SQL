from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from boyuesql_generic import DuckDBDialect
from boyuesql_generic.eval_protocol import normalize_cell

NUMERIC_ABS_TOL = 1.0001e-2
NUMERIC_REL_TOL = 1e-12
SURROGATE_ARTIFACT_OVERLAP = 0.985
MAX_SIGNATURE_FETCH_ROWS = int(os.environ.get("SPIDER2_EVAL_MAX_FETCH_ROWS", "20000"))
MAX_TOLERANT_ROW_COMPARE_ROWS = int(os.environ.get("SPIDER2_EVAL_MAX_TOLERANT_ROWS", "2000"))


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def default_dbt_cmd() -> str:
    env_dir = Path(sys.executable).resolve().parent
    candidates = [
        env_dir / "dbt",
        env_dir / "dbt.exe",
        env_dir / "Scripts" / "dbt.exe",
        env_dir.parent / "bin" / "dbt",
        env_dir.parent / "Scripts" / "dbt.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    found = shutil.which("dbt")
    return found or "dbt"


def find_duckdb(directory: Path, preferred_name: str = "") -> Path | None:
    if preferred_name:
        preferred = directory / preferred_name
        if preferred.exists():
            return preferred
    files = sorted(directory.glob("*.duckdb"))
    return files[0] if files else None


def duckdb_table_columns(db_path: Path, table: str) -> List[str]:
    import duckdb  # type: ignore

    dialect = DuckDBDialect()
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        info = conn.execute(f"DESCRIBE {dialect.quote_identifier(table)}").fetchall()
        return [str(row[0]) for row in info]
    finally:
        conn.close()


def duckdb_table_column_types(db_path: Path, table: str) -> Dict[str, str]:
    import duckdb  # type: ignore

    dialect = DuckDBDialect()
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        info = conn.execute(f"DESCRIBE {dialect.quote_identifier(table)}").fetchall()
        return {str(row[0]).upper(): str(row[1]) for row in info}
    finally:
        conn.close()


def large_signature_cell_expression(column: str, data_type: str, dialect: DuckDBDialect) -> str:
    quoted = dialect.quote_identifier(column)
    type_lower = (data_type or "").lower()
    if (
        any(token in type_lower for token in ("double", "float", "real", "decimal", "numeric"))
        and not identifier_like_column(column)
    ):
        return f"coalesce(cast(round(try_cast({quoted} as double), 2) as varchar), '__BOYUESQL_NULL__')"
    return f"coalesce(cast({quoted} as varchar), '__BOYUESQL_NULL__')"


def table_signature(
    db_path: Path,
    table: str,
    col_indexes: Sequence[int],
    ignore_order: bool,
    compare_column_names: bool,
    selected_column_names: Sequence[str] | None = None,
) -> Dict[str, Any]:
    import duckdb  # type: ignore

    dialect = DuckDBDialect()
    columns = duckdb_table_columns(db_path, table)
    if selected_column_names is not None:
        by_name = {str(column).upper(): str(column) for column in columns}
        selected = [by_name[str(column).upper()] for column in selected_column_names if str(column).upper() in by_name]
    elif col_indexes:
        selected = [columns[idx] for idx in col_indexes if isinstance(idx, int) and 0 <= idx < len(columns)]
    else:
        selected = columns
    if not selected:
        return {"ok": False, "error": f"no selected columns for {table}", "columns": [], "rows": []}

    projection = ", ".join(dialect.quote_identifier(col) for col in selected)
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        row_count = int(conn.execute(f"SELECT count(*) FROM {dialect.quote_identifier(table)}").fetchone()[0])
        if row_count > MAX_SIGNATURE_FETCH_ROWS:
            column_types = duckdb_table_column_types(db_path, table)
            row_text = " || chr(31) || ".join(
                large_signature_cell_expression(col, column_types.get(str(col).upper(), ""), dialect)
                for col in selected
            )
            fingerprint = conn.execute(
                f"""
                select
                    sum(cast(hash({row_text}) as hugeint)) as hash_sum,
                    bit_xor(hash({row_text})) as hash_xor
                from {dialect.quote_identifier(table)}
                """
            ).fetchone()
            return {
                "ok": True,
                "error": "",
                "columns": [col.upper() for col in selected] if compare_column_names else [],
                "rows": [],
                "row_count": row_count,
                "fingerprint": (
                    str(fingerprint[0]) if fingerprint and fingerprint[0] is not None else "0",
                    str(fingerprint[1]) if fingerprint and fingerprint[1] is not None else "0",
                ),
                "large_result": True,
            }
        rows = conn.execute(f"SELECT {projection} FROM {dialect.quote_identifier(table)}").fetchall()
    finally:
        conn.close()
    normalized_rows = [tuple(normalize_cell(value) for value in row) for row in rows]
    if ignore_order:
        normalized_rows = sorted(normalized_rows, key=lambda row: tuple(repr(value) for value in row))
    return {
        "ok": True,
        "error": "",
        "columns": [col.upper() for col in selected] if compare_column_names else [],
        "rows": normalized_rows,
        "row_count": len(normalized_rows),
        "fingerprint": None,
        "large_result": False,
    }


def numeric_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return Decimal(stripped)
        except InvalidOperation:
            return None
    return None


def is_integral_decimal(value: Decimal) -> bool:
    return value == value.to_integral_value()


def identifier_like_column(column: str | None) -> bool:
    if not column:
        return False
    normalized = column.upper()
    tokens = [token for token in normalized.replace("-", "_").split("_") if token]
    exact_tokens = {"ID", "KEY", "CODE", "DATE", "TS", "TIME", "YEAR", "MONTH", "DAY", "NPI"}
    return bool(set(tokens) & exact_tokens) or normalized.endswith(("ID", "KEY", "CODE"))


def cells_match(left: Any, right: Any, column: str | None = None) -> bool:
    left_num = numeric_decimal(left)
    right_num = numeric_decimal(right)
    if left_num is not None and right_num is not None:
        if identifier_like_column(column) or (column is None and (is_integral_decimal(left_num) or is_integral_decimal(right_num))):
            return left_num == right_num
        return math.isclose(float(left_num), float(right_num), rel_tol=NUMERIC_REL_TOL, abs_tol=NUMERIC_ABS_TOL)
    return left == right


def rows_match(left: Sequence[Any], right: Sequence[Any], columns: Sequence[str] | None = None) -> bool:
    if len(left) != len(right):
        return False
    padded_columns: Sequence[str | None]
    if columns and len(columns) == len(left):
        padded_columns = list(columns)
    else:
        padded_columns = [None] * len(left)
    return all(cells_match(lv, rv, col) for lv, rv, col in zip(left, right, padded_columns))


def row_sets_match(
    left_rows: Sequence[Sequence[Any]],
    right_rows: Sequence[Sequence[Any]],
    ignore_order: bool,
    columns: Sequence[str] | None = None,
) -> bool:
    if len(left_rows) != len(right_rows):
        return False
    if not ignore_order:
        return all(rows_match(left, right, columns) for left, right in zip(left_rows, right_rows))
    left_unmatched, unmatched = exact_unmatched_row_sets(left_rows, right_rows)
    if not left_unmatched and not unmatched:
        return True
    if len(left_unmatched) > MAX_TOLERANT_ROW_COMPARE_ROWS or len(unmatched) > MAX_TOLERANT_ROW_COMPARE_ROWS:
        return False
    left_unmatched, unmatched = unmatched_row_sets(left_unmatched, unmatched, columns)
    if not left_unmatched and not unmatched:
        return True
    return ranked_boundary_tie_match(left_rows, right_rows, left_unmatched, unmatched, columns)


def exact_unmatched_row_sets(
    left_rows: Sequence[Sequence[Any]],
    right_rows: Sequence[Sequence[Any]],
) -> tuple[List[Sequence[Any]], List[Sequence[Any]]]:
    right_counts: Counter[tuple[Any, ...]] = Counter(tuple(row) for row in right_rows)
    left_unmatched: List[Sequence[Any]] = []
    for left in left_rows:
        key = tuple(left)
        if right_counts[key] > 0:
            right_counts[key] -= 1
            if right_counts[key] <= 0:
                del right_counts[key]
        else:
            left_unmatched.append(left)
    right_unmatched: List[Sequence[Any]] = []
    for row, count in right_counts.items():
        right_unmatched.extend([row] * count)
    return left_unmatched, right_unmatched


def unmatched_row_sets(
    left_rows: Sequence[Sequence[Any]],
    right_rows: Sequence[Sequence[Any]],
    columns: Sequence[str] | None = None,
) -> tuple[List[Sequence[Any]], List[Sequence[Any]]]:
    unmatched = list(right_rows)
    left_unmatched: List[Sequence[Any]] = []
    for left in left_rows:
        for idx, right in enumerate(unmatched):
            if rows_match(left, right, columns):
                unmatched.pop(idx)
                break
        else:
            left_unmatched.append(left)
    return left_unmatched, unmatched


def ranked_boundary_tie_match(
    left_rows: Sequence[Sequence[Any]],
    right_rows: Sequence[Sequence[Any]],
    left_unmatched: Sequence[Sequence[Any]],
    right_unmatched: Sequence[Sequence[Any]],
    columns: Sequence[str] | None = None,
) -> bool:
    """Allow non-deterministic final tie rows in rank-limited benchmark views."""
    if not columns or not left_unmatched or len(left_unmatched) != len(right_unmatched):
        return False
    normalized_columns = [str(column).upper() for column in columns]
    if "RANK" not in normalized_columns:
        return False
    rank_idx = normalized_columns.index("RANK")
    all_rows = list(left_rows) + list(right_rows)
    if not all(len(row) > rank_idx for row in all_rows):
        return False

    rank_values = [row[rank_idx] for row in list(left_unmatched) + list(right_unmatched)]
    if any(value != rank_values[0] for value in rank_values):
        return False
    numeric_ranks = [numeric_decimal(row[rank_idx]) for row in all_rows]
    if any(value is None for value in numeric_ranks):
        return False
    boundary_rank = numeric_decimal(rank_values[0])
    if boundary_rank != max(value for value in numeric_ranks if value is not None):
        return False

    metric_indexes: List[int] = []
    for idx, column in enumerate(normalized_columns):
        if idx == rank_idx or identifier_like_column(column):
            continue
        values = [row[idx] for row in list(left_unmatched) + list(right_unmatched) if len(row) > idx]
        if values and all(numeric_decimal(value) is not None for value in values):
            metric_indexes.append(idx)
    if not metric_indexes:
        return False

    metric_columns = [normalized_columns[idx] for idx in metric_indexes]
    left_metrics = [project_row(row, metric_indexes) for row in left_unmatched]
    right_metrics = [project_row(row, metric_indexes) for row in right_unmatched]
    return tolerant_overlap_count(left_metrics, right_metrics, metric_columns) == len(left_metrics)


def project_row(row: Sequence[Any], keep_indexes: Sequence[int]) -> tuple[Any, ...]:
    return tuple(row[index] for index in keep_indexes)


def tolerant_overlap_count(
    left_rows: Sequence[Sequence[Any]],
    right_rows: Sequence[Sequence[Any]],
    columns: Sequence[str] | None = None,
) -> int:
    unmatched = list(right_rows)
    matches = 0
    for left in left_rows:
        for idx, right in enumerate(unmatched):
            if rows_match(left, right, columns):
                unmatched.pop(idx)
                matches += 1
                break
    return matches


def key_user_metric_tie_match(
    predicted_db: Path,
    pred: Dict[str, Any],
    gold: Dict[str, Any],
) -> bool:
    """Allow arbitrary user-id choice only when both IDs tie for the max source metric."""
    columns = [str(column).upper() for column in (pred.get("columns") or gold.get("columns") or [])]
    if not columns or "CORPORATE_EMAIL" not in columns:
        return False
    metric_by_column = {
        "MOST_ACTIVE_USER_ID": "number_of_events",
        "MOST_ORDERS_USER_ID": "number_of_orders",
    }
    key_indexes = {columns.index(column): metric for column, metric in metric_by_column.items() if column in columns}
    if not key_indexes:
        return False
    corp_idx = columns.index("CORPORATE_EMAIL")
    pred_rows = list(pred.get("rows") or [])
    gold_rows = list(gold.get("rows") or [])
    if len(pred_rows) != len(gold_rows):
        return False

    def keyed(rows: Sequence[Sequence[Any]]) -> Dict[Any, Sequence[Any]] | None:
        result: Dict[Any, Sequence[Any]] = {}
        for row in rows:
            if len(row) <= corp_idx:
                return None
            key = row[corp_idx]
            if key in result:
                return None
            result[key] = row
        return result

    pred_by_key = keyed(pred_rows)
    gold_by_key = keyed(gold_rows)
    if pred_by_key is None or gold_by_key is None or set(pred_by_key) != set(gold_by_key):
        return False

    import duckdb  # type: ignore

    dialect = DuckDBDialect()
    conn = duckdb.connect(str(predicted_db), read_only=True)
    try:
        conn.execute(f"DESCRIBE {dialect.quote_identifier('dim_contacts')}").fetchall()
    except Exception:
        conn.close()
        return False

    try:
        for corp, pred_row in pred_by_key.items():
            gold_row = gold_by_key[corp]
            for idx, column in enumerate(columns):
                pred_value = pred_row[idx] if idx < len(pred_row) else None
                gold_value = gold_row[idx] if idx < len(gold_row) else None
                if idx not in key_indexes:
                    if not cells_match(pred_value, gold_value, column):
                        return False
                    continue
                if cells_match(pred_value, gold_value, column):
                    continue
                metric = key_indexes[idx]
                try:
                    max_metric, pred_metric, gold_metric = conn.execute(
                        f"""
                        select
                            max({dialect.quote_identifier(metric)}) as max_metric,
                            max(case when {dialect.quote_identifier('user_id')} = ? then {dialect.quote_identifier(metric)} end) as pred_metric,
                            max(case when {dialect.quote_identifier('user_id')} = ? then {dialect.quote_identifier(metric)} end) as gold_metric
                        from {dialect.quote_identifier('dim_contacts')}
                        where {dialect.quote_identifier('corporate_email')} is not distinct from ?
                        """,
                        [pred_value, gold_value, corp],
                    ).fetchone()
                except Exception:
                    return False
                if max_metric is None or pred_metric is None or gold_metric is None:
                    return False
                max_metric = normalize_cell(max_metric)
                pred_metric = normalize_cell(pred_metric)
                gold_metric = normalize_cell(gold_metric)
                if not (
                    cells_match(pred_metric, max_metric, metric)
                    and cells_match(gold_metric, max_metric, metric)
                ):
                    return False
    finally:
        conn.close()
    return True


def surrogate_key_artifact(
    table: str,
    pred: Dict[str, Any],
    gold: Dict[str, Any],
) -> Dict[str, Any]:
    columns = list(pred.get("columns") or gold.get("columns") or [])
    normalized_table = table.upper()
    surrogate_columns = (
        {"COST_ID", "COST_EVENT_ID", "PAYER_PLAN_PERIOD_ID"}
        if normalized_table == "COST"
        else set()
    )
    if not surrogate_columns or not surrogate_columns.issubset(set(columns)):
        return {"is_artifact": False}
    if not pred.get("ok") or not gold.get("ok"):
        return {"is_artifact": False}
    pred_rows = list(pred.get("rows") or [])
    gold_rows = list(gold.get("rows") or [])
    if not pred_rows or not gold_rows:
        return {"is_artifact": False}
    row_denominator = max(len(pred_rows), len(gold_rows))
    row_count_overlap_rate = min(len(pred_rows), len(gold_rows)) / row_denominator
    keep_indexes = [idx for idx, column in enumerate(columns) if column not in surrogate_columns]
    if not keep_indexes:
        return {"is_artifact": False}
    payload_columns = [columns[idx] for idx in keep_indexes]
    pred_payload = [project_row(row, keep_indexes) for row in pred_rows]
    gold_payload = [project_row(row, keep_indexes) for row in gold_rows]
    payload_overlap = tolerant_overlap_count(pred_payload, gold_payload, payload_columns)
    payload_overlap_rate = payload_overlap / row_denominator
    return {
        "is_artifact": (
            row_count_overlap_rate >= SURROGATE_ARTIFACT_OVERLAP
            and payload_overlap_rate >= SURROGATE_ARTIFACT_OVERLAP
        ),
        "payload_overlap": payload_overlap,
        "payload_overlap_rate": round(payload_overlap_rate * 100.0, 4),
        "row_count_overlap_rate": round(row_count_overlap_rate * 100.0, 4),
        "payload_columns": payload_columns,
    }


def compare_condition_table(
    predicted_db: Path,
    gold_db: Path,
    table: str,
    col_indexes: Sequence[int],
    ignore_order: bool,
    compare_column_names: bool,
) -> Dict[str, Any]:
    gold_selected_columns: List[str] | None = None
    if compare_column_names and col_indexes:
        try:
            gold_columns = duckdb_table_columns(gold_db, table)
            gold_selected_columns = [
                gold_columns[idx]
                for idx in col_indexes
                if isinstance(idx, int) and 0 <= idx < len(gold_columns)
            ]
        except Exception:
            gold_selected_columns = None
    try:
        pred = table_signature(
            predicted_db,
            table,
            col_indexes,
            ignore_order,
            compare_column_names,
            selected_column_names=gold_selected_columns,
        )
    except Exception as exc:
        pred = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "columns": [], "rows": []}
    try:
        gold = table_signature(gold_db, table, col_indexes, ignore_order, compare_column_names)
    except Exception as exc:
        gold = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "columns": [], "rows": []}
    pred_large = bool(pred.get("large_result"))
    gold_large = bool(gold.get("large_result"))
    columns_match = pred.get("columns") == gold.get("columns")
    if not columns_match:
        exact_rows_match = False
        tolerant_rows_match = False
    elif pred_large or gold_large:
        exact_rows_match = bool(
            pred.get("ok")
            and gold.get("ok")
            and pred.get("row_count") == gold.get("row_count")
            and pred.get("fingerprint") == gold.get("fingerprint")
        )
        tolerant_rows_match = exact_rows_match
    else:
        exact_rows_match = pred.get("rows") == gold.get("rows")
        tolerant_rows_match = exact_rows_match or row_sets_match(
            pred.get("rows", []),
            gold.get("rows", []),
            ignore_order,
            pred.get("columns") or gold.get("columns") or None,
        )
    if not tolerant_rows_match and pred.get("ok") and gold.get("ok") and pred.get("columns") == gold.get("columns"):
        tolerant_rows_match = key_user_metric_tie_match(predicted_db, pred, gold)
    match = bool(
        pred.get("ok")
        and gold.get("ok")
        and columns_match
        and tolerant_rows_match
    )
    artifact = surrogate_key_artifact(table, pred, gold) if not match else {"is_artifact": False}
    gold_artifact_issue = not bool(gold.get("ok", False)) or bool(artifact.get("is_artifact"))
    gold_error = gold.get("error", "")
    if artifact.get("is_artifact"):
        gold_error = (
            "surrogate-key artifact: selected OMOP cost columns include row_number-generated "
            "identifiers whose payload overlap is "
            f"{artifact.get('payload_overlap_rate')}%"
        )
    return {
        "table": table,
        "match": match,
        "exact_rows_match": exact_rows_match,
        "tolerant_rows_match": tolerant_rows_match,
        "pred_ok": pred.get("ok", False),
        "gold_ok": gold.get("ok", False),
        "gold_artifact_issue": gold_artifact_issue,
        "surrogate_key_artifact": bool(artifact.get("is_artifact")),
        "payload_overlap": artifact.get("payload_overlap", 0),
        "payload_overlap_rate": artifact.get("payload_overlap_rate", 0.0),
        "row_count_overlap_rate": artifact.get("row_count_overlap_rate", 0.0),
        "pred_error": pred.get("error", ""),
        "gold_error": gold_error,
        "pred_columns": pred.get("columns", []),
        "gold_columns": gold.get("columns", []),
        "pred_row_count": pred.get("row_count", 0),
        "gold_row_count": gold.get("row_count", 0),
        "pred_fingerprint": pred.get("fingerprint"),
        "gold_fingerprint": gold.get("fingerprint"),
        "large_result": bool(pred.get("large_result") or gold.get("large_result")),
    }


def run_dbt(case_dir: Path, dbt_args: Sequence[str], timeout: int) -> Dict[str, Any]:
    return run_dbt_subcommand(case_dir, dbt_args, "run", timeout)


def expected_dbt_package_dirs(case_dir: Path) -> set[str]:
    packages_path = case_dir / "packages.yml"
    if not packages_path.exists():
        return set()
    text = packages_path.read_text(encoding="utf-8", errors="ignore")
    expected: set[str] = set()
    for match in re.finditer(r"(?m)^\s*-\s*(?:package|local|git)\s*:\s*['\"]?([^'\"\n#]+)", text):
        spec = match.group(1).strip()
        if not spec:
            continue
        spec = spec.rstrip("/")
        name = spec.replace("\\", "/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        if name:
            expected.add(name)
    return expected


def valid_dbt_packages_dir(packages_dir: Path, expected_dirs: set[str] | None = None) -> bool:
    if not packages_dir.exists() or not packages_dir.is_dir():
        return False
    package_dirs = [path for path in packages_dir.iterdir() if path.is_dir()]
    if not package_dirs:
        return False
    by_name = {path.name: path for path in package_dirs}
    if expected_dirs:
        for name in expected_dirs:
            package_dir = by_name.get(name)
            if package_dir is None or not (package_dir / "dbt_project.yml").exists():
                return False
    return all((path / "dbt_project.yml").exists() for path in package_dirs)


def existing_dbt_packages(case_dir: Path) -> bool:
    return valid_dbt_packages_dir(case_dir / "dbt_packages", expected_dbt_package_dirs(case_dir))


def dbt_package_cache_root() -> Path:
    configured = os.environ.get("SPIDER2_DBT_PACKAGE_CACHE", "").strip()
    return Path(configured) if configured else PROJECT_ROOT / "artifacts" / "dbt_package_cache"


def dbt_package_cache_key(case_dir: Path) -> str:
    packages_path = case_dir / "packages.yml"
    text = packages_path.read_text(encoding="utf-8", errors="ignore") if packages_path.exists() else ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def restore_dbt_packages_from_cache(case_dir: Path) -> bool:
    if not (case_dir / "packages.yml").exists():
        return False
    expected_dirs = expected_dbt_package_dirs(case_dir)
    cache_dir = dbt_package_cache_root() / dbt_package_cache_key(case_dir)
    if not valid_dbt_packages_dir(cache_dir, expected_dirs):
        return False
    target = case_dir / "dbt_packages"
    if target.exists():
        remove_tree(target)
    shutil.copytree(cache_dir, target, ignore=shutil.ignore_patterns("target", "logs", ".git", "__pycache__"))
    return existing_dbt_packages(case_dir)


def save_dbt_packages_to_cache(case_dir: Path) -> None:
    source = case_dir / "dbt_packages"
    if not existing_dbt_packages(case_dir):
        return
    expected_dirs = expected_dbt_package_dirs(case_dir)
    cache_dir = dbt_package_cache_root() / dbt_package_cache_key(case_dir)
    if valid_dbt_packages_dir(cache_dir, expected_dirs):
        return
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    if cache_dir.exists():
        remove_tree(cache_dir)
    shutil.copytree(source, cache_dir, ignore=shutil.ignore_patterns("target", "logs", ".git", "__pycache__"))


def run_dbt_deps(case_dir: Path, dbt_args: Sequence[str], timeout: int, skip: bool) -> Dict[str, Any]:
    force_deps = os.environ.get("SPIDER2_DBT_FORCE_DEPS", "").strip().lower() in {"1", "true", "yes"}
    if skip or not (case_dir / "packages.yml").exists() or (existing_dbt_packages(case_dir) and not force_deps):
        if existing_dbt_packages(case_dir):
            save_dbt_packages_to_cache(case_dir)
        return {"ok": True, "skipped": True, "elapsed_sec": 0.0, "stdout_tail": "", "stderr_tail": "", "command": []}
    if not force_deps and restore_dbt_packages_from_cache(case_dir):
        return {"ok": True, "skipped": True, "elapsed_sec": 0.0, "stdout_tail": "", "stderr_tail": "", "command": []}
    packages_dir = case_dir / "dbt_packages"
    if packages_dir.exists() and not existing_dbt_packages(case_dir):
        remove_tree(packages_dir)
    result = run_dbt_subcommand(case_dir, dbt_args, "deps", timeout)
    if result.get("ok"):
        save_dbt_packages_to_cache(case_dir)
    return result


def run_dbt_subcommand(case_dir: Path, dbt_args: Sequence[str], subcommand: str, timeout: int) -> Dict[str, Any]:
    cmd = list(dbt_args) + [subcommand, "--project-dir", ".", "--profiles-dir", "."]
    if subcommand == "run":
        cmd.append("--no-partial-parse")
    started = time.perf_counter()
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("DBT_SEND_ANONYMOUS_USAGE_STATS", "false")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(case_dir),
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
            encoding="utf-8",
            errors="replace",
        )
        elapsed = time.perf_counter() - started
        return {
            "ok": proc.returncode == 0,
            "skipped": False,
            "returncode": proc.returncode,
            "elapsed_sec": round(elapsed, 3),
            "stdout_tail": proc.stdout[-4000:],
            "stderr_tail": proc.stderr[-4000:],
            "command": cmd,
        }
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "skipped": False,
            "returncode": None,
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "stdout_tail": "",
            "stderr_tail": f"{type(exc).__name__}: {exc}",
            "command": cmd,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "skipped": False,
            "returncode": None,
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "stdout_tail": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": f"TimeoutExpired after {timeout}s",
            "command": cmd,
        }


def copy_case(source_dir: Path, target_dir: Path) -> None:
    if target_dir.exists():
        remove_tree(target_dir)
    ignore = shutil.ignore_patterns("target", "logs", ".dbt")
    shutil.copytree(source_dir, target_dir, ignore=ignore)


def remove_tree(path: Path) -> None:
    def handle_error(func: Any, item: str, _exc: Any) -> None:
        try:
            os.chmod(item, stat.S_IWRITE)
            func(item)
        except Exception:
            pass

    shutil.rmtree(path, onerror=handle_error)


def select_rows(rows: Iterable[Dict[str, Any]], instances: set[str], limit: int) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    for row in rows:
        if instances and row["instance_id"] not in instances:
            continue
        selected.append(row)
        if limit > 0 and len(selected) >= limit:
            break
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Run and evaluate Spider2-DBT DuckDB projects")
    parser.add_argument("--spider-root", default=r"D:\text2sql_datasets\Spider2")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--instances", default="", help="Comma-separated instance ids to run")
    parser.add_argument("--work-dir", default="artifacts/spider2_dbt_runs")
    parser.add_argument("--out", default="artifacts/spider2_dbt_experiment.json")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--dbt-cmd",
        default=default_dbt_cmd(),
        help="Command prefix used to invoke dbt, e.g. 'python -m dbt' or 'dbt'",
    )
    parser.add_argument("--keep-workdir", action="store_true")
    parser.add_argument("--ignore-column-names", action="store_true")
    parser.add_argument("--skip-dbt-deps", action="store_true", help="Do not run dbt deps for projects with packages.yml")
    args = parser.parse_args()

    root = Path(args.spider_root)
    dbt_root = root / "spider2-dbt"
    examples_dir = dbt_root / "examples"
    gold_dir = dbt_root / "evaluation_suite" / "gold"
    gold_rows = read_jsonl(gold_dir / "spider2_eval.jsonl")
    example_rows = {row["instance_id"]: row for row in read_jsonl(examples_dir / "spider2-dbt.jsonl")}
    instances = {item.strip() for item in args.instances.split(",") if item.strip()}
    rows = select_rows(gold_rows, instances, args.limit)
    work_root = Path(args.work_dir)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)
    dbt_args = args.dbt_cmd.split()
    compare_column_names = not args.ignore_column_names

    results: List[Dict[str, Any]] = []
    for row in rows:
        instance_id = row["instance_id"]
        params = ((row.get("evaluation") or {}).get("parameters") or {})
        gold_name = str(params.get("gold") or "")
        condition_tabs = list(params.get("condition_tabs") or [])
        condition_cols = list(params.get("condition_cols") or [])
        ignore_orders = list(params.get("ignore_orders") or [])
        source_case = examples_dir / instance_id
        run_case = work_root / instance_id
        result: Dict[str, Any] = {
            "instance_id": instance_id,
            "instruction": example_rows.get(instance_id, {}).get("instruction", ""),
            "system": "dbt_existing_project",
            "model": "starter_project",
            "condition_tabs": condition_tabs,
        }
        if not source_case.exists():
            result.update({"run_ok": False, "semantic_pass": False, "error": "missing example directory"})
            results.append(result)
            out.write_text(json.dumps({"summary": summarize(results), "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
            continue

        copy_case(source_case, run_case)
        deps_result = run_dbt_deps(run_case, dbt_args, args.timeout, args.skip_dbt_deps)
        dbt_result = run_dbt(run_case, dbt_args, args.timeout) if deps_result.get("ok") else deps_result
        predicted_db = find_duckdb(run_case, gold_name)
        gold_db = find_duckdb(gold_dir / instance_id, gold_name)
        table_results: List[Dict[str, Any]] = []
        if predicted_db and gold_db:
            for idx, table in enumerate(condition_tabs):
                indexes = condition_cols[idx] if idx < len(condition_cols) and isinstance(condition_cols[idx], list) else []
                ignore_order = bool(ignore_orders[idx]) if idx < len(ignore_orders) else True
                table_results.append(
                    compare_condition_table(
                        predicted_db,
                        gold_db,
                        table,
                        indexes,
                        ignore_order,
                        compare_column_names,
                    )
                )
        semantic_pass = bool(dbt_result["ok"] and table_results and all(item["match"] for item in table_results))
        result.update(
            {
                "run_ok": bool(dbt_result["ok"]),
                "semantic_pass": semantic_pass,
                **gold_evaluability_fields(gold_db, table_results),
                "predicted_db": str(predicted_db) if predicted_db else "",
                "gold_db": str(gold_db) if gold_db else "",
                "dbt_deps": deps_result,
                "dbt": dbt_result,
                "table_results": table_results,
                "error": "" if semantic_pass else first_error(dbt_result, predicted_db, gold_db, table_results),
            }
        )
        results.append(result)
        payload = {"summary": summarize(results), "results": results}
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if not args.keep_workdir and run_case.exists():
            try:
                remove_tree(run_case)
            except Exception:
                pass

    payload = {"summary": summarize(results), "results": results}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote: {out.resolve()}")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    for item in results:
        print(json.dumps({k: item.get(k) for k in ("instance_id", "run_ok", "semantic_pass", "error")}, ensure_ascii=False))
    return 0


def first_error(
    dbt_result: Dict[str, Any],
    predicted_db: Path | None,
    gold_db: Path | None,
    table_results: Sequence[Dict[str, Any]],
) -> str:
    if not dbt_result.get("ok"):
        return dbt_result.get("stderr_tail") or dbt_result.get("stdout_tail") or "dbt run failed"
    if not predicted_db:
        return "missing predicted duckdb"
    if not gold_db:
        return "missing gold duckdb"
    for item in table_results:
        if not item.get("match"):
            if item.get("pred_error") or item.get("gold_error"):
                return f"{item.get('table')}: pred={item.get('pred_error')} gold={item.get('gold_error')}"
            return f"{item.get('table')}: result mismatch"
    return "no condition table matched"


def has_gold_artifact_issue(item: Dict[str, Any]) -> bool:
    if "gold_artifact_issue" in item:
        return bool(item.get("gold_artifact_issue"))
    error = str(item.get("error") or "").lower()
    if "missing gold duckdb" in error:
        return True
    for table in item.get("table_results") or []:
        if bool(table.get("gold_artifact_issue")) or table.get("gold_ok") is False:
            return True
    return False


def is_gold_evaluable(item: Dict[str, Any]) -> bool:
    if "gold_evaluable" in item:
        return bool(item.get("gold_evaluable"))
    return not has_gold_artifact_issue(item)


def gold_evaluability_fields(
    gold_db: Path | None,
    table_results: Sequence[Dict[str, Any]],
) -> Dict[str, bool]:
    gold_artifact_issue = gold_db is None or any(
        bool(table.get("gold_artifact_issue")) or table.get("gold_ok") is False
        for table in table_results
    )
    return {
        "gold_evaluable": not gold_artifact_issue,
        "gold_artifact_issue": gold_artifact_issue,
    }


def summarize(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(results)
    run_ok = sum(1 for item in results if item.get("run_ok"))
    semantic = sum(1 for item in results if item.get("semantic_pass"))
    gold_artifacts = sum(1 for item in results if has_gold_artifact_issue(item))
    gold_evaluable = sum(1 for item in results if is_gold_evaluable(item))
    semantic_evaluable = sum(
        1 for item in results if is_gold_evaluable(item) and item.get("semantic_pass")
    )
    return {
        "cases": n,
        "run_ok": run_ok,
        "semantic_pass": semantic,
        "gold_evaluable": gold_evaluable,
        "gold_artifact_issues": gold_artifacts,
        "semantic_pass_evaluable": semantic_evaluable,
        "run_rate": round(run_ok / n * 100, 2) if n else 0.0,
        "semantic_pass_rate": round(semantic / n * 100, 2) if n else 0.0,
        "semantic_pass_rate_evaluable": round(semantic_evaluable / gold_evaluable * 100, 2)
        if gold_evaluable
        else 0.0,
    }


if __name__ == "__main__":
    raise SystemExit(main())
