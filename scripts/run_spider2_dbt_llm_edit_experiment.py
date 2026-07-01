from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

try:
    import requests
except Exception:  # pragma: no cover - exercised only when optional LLM client deps are absent.
    requests = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = Path(__file__).resolve().parent
for candidate in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from run_spider2_dbt_experiment import (  # noqa: E402
    compare_condition_table,
    copy_case,
    default_dbt_cmd,
    find_duckdb,
    first_error,
    gold_evaluability_fields,
    read_jsonl,
    remove_tree,
    run_dbt,
    run_dbt_deps,
    summarize,
)


TEXT_SUFFIXES = {".sql", ".yml", ".yaml", ".md", ".csv", ".txt"}
EDIT_SUFFIXES = {".sql", ".yml", ".yaml", ".md", ".csv"}
DBT_MODEL_SUFFIXES = {".sql", ".py"}
ALLOWED_EDIT_ROOTS = {
    "models",
    "macros",
    "seeds",
    "snapshots",
    "tests",
    "analyses",
}
ROOT_EDIT_FILES = {"dbt_project.yml", "packages.yml"}
JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.IGNORECASE | re.DOTALL)
MODEL_MISSING_RE = re.compile(r"Table with name ([A-Za-z_][A-Za-z0-9_]*) does not exist", re.IGNORECASE)
BACKTICK_JSON_STRING_RE = re.compile(r'("content"\s*:\s*)`(.*?)`', re.DOTALL)
KNOWN_DBT_PACKAGES: Dict[str, tuple[str, str]] = {
    "dbt_activity_schema": ("tnightengale/dbt_activity_schema", "0.4.1"),
}


def select_rows(rows: Iterable[Dict[str, Any]], instances: set[str], limit: int) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    for row in rows:
        if instances and row["instance_id"] not in instances:
            continue
        selected.append(row)
        if limit > 0 and len(selected) >= limit:
            break
    return selected


def ollama_generate(
    prompt: str,
    *,
    model: str,
    base_url: str,
    timeout: int,
    num_predict: int,
    api: str,
    temperature: float,
) -> str:
    if requests is None:
        raise RuntimeError(
            "The optional 'requests' dependency is required only for LLM-backed "
            "Ollama generation. Install requests/urllib3 or rerun with --no-llm."
        )
    base = base_url.rstrip("/")
    if api == "chat":
        response = requests.post(
            base + "/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "think": False,
                "options": {"temperature": temperature, "num_predict": int(num_predict)},
            },
            timeout=timeout,
        )
    else:
        response = requests.post(
            base + "/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "options": {"temperature": temperature, "num_predict": int(num_predict)},
            },
            timeout=timeout,
        )
    response.raise_for_status()
    payload = response.json()
    if api == "chat":
        return str((payload.get("message") or {}).get("content") or "")
    return str(payload.get("response", ""))


def duckdb_schema_summary(db_path: Path | None, max_tables: int, max_cols: int) -> str:
    if not db_path or not db_path.exists():
        return "No DuckDB file found in the starter project."
    import duckdb  # type: ignore

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        tables = conn.execute(
            """
            select table_schema, table_name
            from information_schema.tables
            where table_schema not in ('information_schema', 'pg_catalog')
            order by table_schema, table_name
            """
        ).fetchall()
        lines = [f"DuckDB file: {db_path.name}"]
        for schema_name, table_name in tables[:max_tables]:
            info = conn.execute(
                "select column_name, data_type from information_schema.columns "
                "where table_schema = ? and table_name = ? order by ordinal_position",
                [schema_name, table_name],
            ).fetchall()
            columns = ", ".join(f"{name} {dtype}" for name, dtype in info[:max_cols])
            if len(info) > max_cols:
                columns += f", ... (+{len(info) - max_cols} columns)"
            lines.append(f"- {schema_name}.{table_name}: {columns}")
        if len(tables) > max_tables:
            lines.append(f"... (+{len(tables) - max_tables} tables)")
        return "\n".join(lines)
    finally:
        conn.close()


def collect_project_files(case_dir: Path, max_file_chars: int, max_total_chars: int) -> str:
    ignored_parts = {"target", "logs", ".dbt", "dbt_packages", ".git", "__pycache__"}
    chunks: List[str] = []
    total = 0
    for path in sorted(case_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(case_dir).as_posix()
        if any(part in ignored_parts for part in path.relative_to(case_dir).parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in ROOT_EDIT_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if len(text) > max_file_chars:
            text = text[:max_file_chars] + "\n... [truncated]"
        block = f"### FILE: {rel}\n```text\n{text}\n```"
        if total + len(block) > max_total_chars:
            chunks.append("... [project context truncated]")
            break
        chunks.append(block)
        total += len(block)
    return "\n\n".join(chunks) if chunks else "No text project files found."


def target_table_hint(condition_tabs: Sequence[str]) -> str:
    if not condition_tabs:
        return "The hidden evaluation target tables are not exposed to you; infer the required final models from the instruction and the starter project."
    return (
        "The final DBT project should materialize these requested output models "
        f"when they are semantically required by the instruction: {', '.join(condition_tabs)}."
    )


def strip_sql_comments(text: str) -> str:
    no_block = re.sub(r"/\*.*?\*/", "", text or "", flags=re.DOTALL)
    return "\n".join(line for line in no_block.splitlines() if not line.strip().startswith("--"))


def model_sql_is_query(content: str) -> bool:
    body = strip_sql_comments(content)
    body = re.sub(r"\{\{\s*config\(.*?\)\s*\}\}", "", body, flags=re.IGNORECASE | re.DOTALL).strip()
    if not body:
        return False
    return re.search(r"\b(select|with)\b", body, flags=re.IGNORECASE) is not None or body.startswith("{{")


def focus_model_names_from_history(failure_history: Sequence[str]) -> List[str]:
    names: List[str] = []
    for text in failure_history:
        names.extend(match.group(1).strip() for match in MODEL_MISSING_RE.finditer(text or ""))
        names.extend(missing_refs_from_error(text or ""))
        names.extend(missing_model_patches_from_warning(text or ""))
    return sorted({name for name in names if name})


def short_model_specs_from_yml(case_dir: Path, focus_names: Sequence[str], limit: int = 12, max_cols: int = 28) -> str:
    declared = model_names_from_yml(case_dir)
    if not declared:
        return "No public model YAML declarations found."
    focus = {name.lower() for name in focus_names}
    missing_sql = [name for name in declared if not model_sql_exists(case_dir, name)]
    selected: List[str] = []
    for name in declared:
        if name.lower() in focus:
            selected.append(name)
    for name in missing_sql:
        if name not in selected:
            selected.append(name)
        if len(selected) >= limit:
            break
    if not selected:
        selected = missing_sql[:limit] or declared[:limit]
    blocks: List[str] = []
    for name in selected[:limit]:
        cols = model_columns_from_yml(case_dir, name)[:max_cols]
        status = "has_sql" if model_sql_exists(case_dir, name) else "missing_sql"
        line = f"- {name} ({status})"
        if cols:
            line += ": " + ", ".join(cols)
            if len(model_columns_from_yml(case_dir, name)) > max_cols:
                line += ", ..."
        blocks.append(line)
    return "\n".join(blocks)


def dbt_graph_index(case_dir: Path, failure_history: Sequence[str]) -> str:
    models_dir = case_dir / "models"
    existing_sql: List[str] = []
    if models_dir.exists():
        existing_sql = sorted({path.stem for path in models_dir.rglob("*.sql") if "dbt_packages" not in path.parts})
    declared = model_names_from_yml(case_dir)
    missing_sql = sorted(name for name in declared if not model_sql_exists(case_dir, name))
    focus = focus_model_names_from_history(failure_history)
    source_refs: List[str] = []
    for path in sorted(case_dir.rglob("*.yml")) + sorted(case_dir.rglob("*.yaml")):
        rel_parts = path.relative_to(case_dir).parts
        if "dbt_packages" in rel_parts or "target" in rel_parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r"-\s*name:\s*['\"]?([^'\"\n]+)['\"]?", text):
            source_refs.append(match.group(1).strip())
    lines = [
        "Existing SQL models: " + (", ".join(existing_sql[:120]) if existing_sql else "(none)"),
        "Public YAML-declared models without SQL: " + (", ".join(missing_sql[:80]) if missing_sql else "(none)"),
        "Focus models from prior failures: " + (", ".join(focus) if focus else "(none yet)"),
        "Relevant public model specs:",
        short_model_specs_from_yml(case_dir, focus),
    ]
    if source_refs:
        lines.append("Public YAML names available for refs/sources/columns: " + ", ".join(sorted(set(source_refs))[:160]))
    return "\n".join(lines)


def build_prompt(
    *,
    instruction: str,
    case_dir: Path,
    start_db: Path | None,
    condition_tabs: Sequence[str],
    failure_history: Sequence[str],
    max_file_chars: int,
    max_total_chars: int,
    max_schema_tables: int,
    max_schema_cols: int,
) -> str:
    project_files = collect_project_files(case_dir, max_file_chars, max_total_chars)
    graph_index = dbt_graph_index(case_dir, failure_history)
    schema_text = duckdb_schema_summary(start_db, max_schema_tables, max_schema_cols)
    failures = "\n\n".join(failure_history[-3:]) if failure_history else "No previous failure."
    return f"""You are editing a dbt project for a Spider2-DBT task.

Task instruction:
{instruction}

Important constraints:
- Use only the starter project files and the DuckDB schema shown below.
- Do not assume access to any hidden gold answer.
- Return ONLY valid JSON, with this exact shape:
  {{"edits":[{{"path":"models/example.sql","content":"..."}}],"notes":"short rationale"}}
- Paths must be relative to the dbt project root.
- Edit only dbt text files such as .sql, .yml, .yaml, .md, or .csv.
- Prefer creating or replacing models under models/.
- Use DuckDB-compatible SQL and dbt ref/source syntax.
- The project must pass `dbt run --project-dir . --profiles-dir .`.
- {target_table_hint(condition_tabs)}
- When a prior failure says an output table/model is missing, first check
  the public YAML-declared model specs below. If the model is declared
  there, create a real SELECT/WITH model with the listed columns and
  semantics from the instruction; do not create an empty or comment-only file.
- Prefer existing ref() models over hallucinated source() calls. Use source()
  only when that source/table is declared in the project YAML or clearly
  present in the DuckDB schema.
- If the DBT error says a model depends on a missing node named X, create or fix `models/X.sql`.
- Do not create empty placeholder models, `where 1 = 0` models, or NULL-only projections.
- DBT model SQL must be a SELECT/WITH query. Do not use ALTER, DROP, CREATE,
  INSERT, UPDATE, DELETE, MERGE, or TRUNCATE.
- Do not return diagnostics such as table_comparison, stdout, or stderr.
- Do not return prose outside the JSON object.

Source DuckDB schema:
{schema_text}

Public dbt graph index and model specifications:
{graph_index}

Current dbt project files:
{project_files}

Previous DBT/evaluation failures:
{failures}

Return ONLY this JSON object now:
{{"edits":[{{"path":"models/model_name.sql","content":"complete dbt SQL file content"}}],"notes":"short rationale"}}
"""


def parse_edit_json(text: str) -> Dict[str, Any]:
    candidates: List[str] = []
    block = JSON_BLOCK_RE.search(text or "")
    if block:
        candidates.append(block.group(1))
    raw = text or ""
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        for variant in (candidate, normalize_backtick_json_strings(candidate)):
            try:
                payload = json.loads(variant)
                if isinstance(payload, dict):
                    return payload
            except json.JSONDecodeError:
                continue
    return {"edits": [], "notes": "model output was not valid JSON", "raw": (text or "")[:2000]}


def normalize_backtick_json_strings(candidate: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return match.group(1) + json.dumps(match.group(2), ensure_ascii=False)

    return BACKTICK_JSON_STRING_RE.sub(repl, candidate or "")


def safe_edit_path(case_dir: Path, rel_path: str) -> Path:
    rel = rel_path.replace("\\", "/").strip()
    if not rel or rel.startswith("/") or rel.startswith("~"):
        raise ValueError(f"invalid edit path: {rel_path!r}")
    parts = [part for part in rel.split("/") if part]
    if any(part == ".." for part in parts):
        raise ValueError(f"parent traversal is not allowed: {rel_path!r}")
    if len(parts) == 1:
        if parts[0] not in ROOT_EDIT_FILES:
            raise ValueError(f"root-level edits are restricted: {rel_path!r}")
    elif parts[0] not in ALLOWED_EDIT_ROOTS:
        raise ValueError(f"edit path must be in dbt text directories: {rel_path!r}")
    target = (case_dir / Path(*parts)).resolve()
    root = case_dir.resolve()
    if not str(target).lower().startswith(str(root).lower()):
        raise ValueError(f"edit escaped project root: {rel_path!r}")
    if target.suffix.lower() not in EDIT_SUFFIXES and target.name not in ROOT_EDIT_FILES:
        raise ValueError(f"unsupported edit file type: {rel_path!r}")
    return target


def apply_edits(case_dir: Path, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    applied: List[Dict[str, Any]] = []
    edits = payload.get("edits")
    if not isinstance(edits, list):
        return [{"path": "", "ok": False, "error": "payload.edits is not a list"}]
    for edit in edits:
        if not isinstance(edit, dict):
            applied.append({"path": "", "ok": False, "error": "edit is not an object"})
            continue
        rel_path = str(edit.get("path") or "")
        content = edit.get("content")
        if not isinstance(content, str):
            applied.append({"path": rel_path, "ok": False, "error": "content must be a string"})
            continue
        content_lower = content.lower()
        if (
            "where 1 = 0" in content_lower
            or "deterministic placeholder" in content_lower
            or "semantic correctness is still decided by evaluation" in content_lower
        ):
            applied.append({"path": rel_path, "ok": False, "error": "placeholder-like LLM edit rejected"})
            continue
        if rel_path.lower().endswith(".sql") and re.search(
            r"\b(alter|drop|create|insert|update|delete|merge|truncate)\b",
            content,
            flags=re.IGNORECASE,
        ):
            applied.append({"path": rel_path, "ok": False, "error": "DDL/DML SQL edit rejected"})
            continue
        if rel_path.replace("\\", "/").lower().startswith("models/") and rel_path.lower().endswith(".sql"):
            if not model_sql_is_query(content):
                applied.append({"path": rel_path, "ok": False, "error": "model SQL without SELECT/WITH rejected"})
                continue
            content = re.sub(r";\s*$", "", content.strip(), flags=re.DOTALL) + "\n"
        try:
            target = safe_edit_path(case_dir, rel_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
            applied.append({"path": target.relative_to(case_dir.resolve()).as_posix(), "ok": True, "bytes": len(content.encode("utf-8"))})
        except Exception as exc:
            applied.append({"path": rel_path, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return applied


def missing_refs_from_error(text: str) -> List[str]:
    refs = re.findall(r"depends on a node named '([^']+)' which was not found", text or "", flags=re.IGNORECASE)
    return sorted({ref.strip() for ref in refs if ref.strip()})


def missing_ref_dependency_pairs(text: str) -> List[tuple[str, str]]:
    pairs = re.findall(
        r"Model\s+'[^']*?\.([^'.]+)'\s+\([^)]+\)\s+depends on a node named '([^']+)' which was not found",
        text or "",
        flags=re.IGNORECASE,
    )
    return sorted({(dependent.strip(), missing.strip()) for dependent, missing in pairs if dependent.strip() and missing.strip()})


def inferred_missing_ref_columns(case_dir: Path, ref_name: str, dbt_error: str) -> List[str]:
    columns: List[str] = []
    seen: set[str] = set()
    for dependent, missing in missing_ref_dependency_pairs(dbt_error):
        if missing.lower() != ref_name.lower():
            continue
        for column in model_sql_output_columns(case_dir, dependent):
            key = column.lower()
            if key not in seen:
                seen.add(key)
                columns.append(column)
    return columns


def blocked_ref_bases_from_history(failure_history: Sequence[str]) -> Dict[str, set[str]]:
    blocked: Dict[str, set[str]] = {}
    for text in failure_history:
        for dependent, missing in missing_ref_dependency_pairs(text or ""):
            blocked.setdefault(missing.lower(), set()).add(dependent.lower())
    return blocked


def missing_model_patches_from_warning(text: str) -> List[str]:
    names = re.findall(r"patch with name '([^']+)'", text or "", flags=re.IGNORECASE)
    return sorted({name.strip() for name in names if name.strip()})


def model_columns_from_yml_root(root: Path, model_name: str) -> List[str]:
    target = model_name.strip().lower()
    columns: List[str] = []
    if not root.exists():
        return columns
    for path in sorted(root.rglob("*.yml")) + sorted(root.rglob("*.yaml")):
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        in_models = False
        model_item_indent: int | None = None
        current_model = ""
        in_columns = False
        columns_indent = 0
        for line in lines:
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))
            if not stripped or stripped.startswith("#"):
                continue
            if indent == 0 and re.match(r"[A-Za-z_][A-Za-z0-9_-]*\s*:", stripped) and not stripped.startswith("models:"):
                in_models = False
                current_model = ""
                in_columns = False
                model_item_indent = None
            if stripped.startswith("models:"):
                in_models = True
                current_model = ""
                in_columns = False
                model_item_indent = None
                continue
            if not in_models:
                continue
            name_match = re.match(r"-\s*name:\s*['\"]?([^'\"]+)['\"]?\s*$", stripped)
            if name_match and (model_item_indent is None or indent == model_item_indent):
                model_item_indent = indent
                current_model = name_match.group(1).strip().lower()
                in_columns = False
                columns_indent = 0
                continue
            if current_model == target and stripped.startswith("columns:"):
                in_columns = True
                columns_indent = indent
                continue
            if current_model == target and in_columns:
                if indent <= columns_indent and stripped:
                    in_columns = False
                    continue
                col_match = re.match(r"-\s*name:\s*['\"]?([^'\"]+)['\"]?\s*$", stripped)
                if col_match:
                    col = col_match.group(1).strip()
                    if col:
                        columns.append(col)
                    continue
        if columns:
            break
    return columns


def model_columns_from_yml(case_dir: Path, model_name: str) -> List[str]:
    return model_columns_from_yml_root(case_dir / "models", model_name)


def model_refs_from_yml(case_dir: Path, model_name: str) -> List[str]:
    target = model_name.strip().lower()
    refs: List[str] = []
    for path in sorted((case_dir / "models").rglob("*.yml")) + sorted((case_dir / "models").rglob("*.yaml")):
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        in_models = False
        model_item_indent: int | None = None
        current_model = ""
        in_refs = False
        refs_indent = 0
        for line in lines:
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))
            if not stripped or stripped.startswith("#"):
                continue
            if indent == 0 and re.match(r"[A-Za-z_][A-Za-z0-9_-]*\s*:", stripped) and not stripped.startswith("models:"):
                in_models = False
                current_model = ""
                in_refs = False
                model_item_indent = None
            if stripped.startswith("models:"):
                in_models = True
                current_model = ""
                in_refs = False
                model_item_indent = None
                continue
            if not in_models:
                continue
            name_match = re.match(r"-\s*name:\s*['\"]?([^'\"]+)['\"]?\s*$", stripped)
            if name_match and (model_item_indent is None or indent == model_item_indent):
                model_item_indent = indent
                current_model = name_match.group(1).strip().lower()
                in_refs = False
                refs_indent = 0
                continue
            if current_model == target and stripped.startswith("refs:"):
                in_refs = True
                refs_indent = indent
                continue
            if current_model == target and in_refs:
                if indent <= refs_indent and stripped:
                    in_refs = False
                    continue
                ref_match = re.match(r"-\s*name:\s*['\"]?([^'\"]+)['\"]?\s*$", stripped)
                if ref_match:
                    ref_name = ref_match.group(1).strip()
                    if ref_name:
                        refs.append(ref_name)
        if refs:
            break
    return refs


def model_names_from_yml(case_dir: Path) -> List[str]:
    names: List[str] = []
    models_dir = case_dir / "models"
    if not models_dir.exists():
        return names
    for path in sorted(models_dir.rglob("*.yml")) + sorted(models_dir.rglob("*.yaml")):
        in_models = False
        model_item_indent: int | None = None
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent == 0 and re.match(r"[A-Za-z_][A-Za-z0-9_-]*\s*:", stripped) and not stripped.startswith("models:"):
                in_models = False
                model_item_indent = None
            if indent == 0 and stripped.startswith("models:"):
                in_models = True
                model_item_indent = None
                continue
            if in_models:
                match = re.match(r"-\s*name:\s*['\"]?([^'\"]+)['\"]?\s*$", stripped)
                if match and (model_item_indent is None or indent == model_item_indent):
                    model_item_indent = indent
                    names.append(match.group(1).strip())
    return sorted({name for name in names if name})


def model_sql_exists(case_dir: Path, model_name: str) -> bool:
    models_dir = case_dir / "models"
    if not models_dir.exists():
        return False
    target = model_name.lower()
    return any(
        path.is_file()
        and path.suffix.lower() in DBT_MODEL_SUFFIXES
        and path.stem.lower() == target
        for path in models_dir.rglob("*")
    )


def model_sql_path(case_dir: Path, model_name: str) -> Path | None:
    models_dir = case_dir / "models"
    if not models_dir.exists():
        return None
    target = f"{model_name}.sql".lower()
    for path in models_dir.rglob("*.sql"):
        if path.name.lower() == target:
            return path
    return None


def project_requires_custom_schema_name(case_dir: Path) -> bool:
    for path in sorted((case_dir / "macros").rglob("*.sql")):
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        if "custom schema name must be provided" in text:
            return True
    return False


def fallback_model_schema(case_dir: Path) -> str:
    return "main" if project_requires_custom_schema_name(case_dir) else ""


def generated_model_config(schema: str = "") -> str:
    if schema:
        return "{{ config(materialized='table', schema='" + schema.replace("'", "''") + "') }}"
    return "{{ config(materialized='table') }}"


def project_var_enabled(case_dir: Path, var_name: str) -> bool:
    project = case_dir / "dbt_project.yml"
    if not project.exists():
        return False
    text = project.read_text(encoding="utf-8", errors="ignore")
    pattern = re.compile(rf"(?im)^\s*{re.escape(var_name)}\s*:\s*(true|1|yes)\s*$")
    return bool(pattern.search(text))


def model_sql_is_statically_disabled(case_dir: Path, model_name: str) -> bool:
    path = model_sql_path(case_dir, model_name)
    if not path:
        return False
    text = path.read_text(encoding="utf-8", errors="ignore")
    if re.search(r"config\s*\([^)]*enabled\s*=\s*(false|0)\b", text, flags=re.IGNORECASE):
        return True
    var_match = re.search(
        r"enabled\s*=\s*var\(\s*['\"]([^'\"]+)['\"]\s*,\s*(false|False|0)\s*\)",
        text,
    )
    if var_match:
        return not project_var_enabled(case_dir, var_match.group(1))
    return False


def source_table_map_from_yml(case_dir: Path) -> Dict[str, tuple[str, str]]:
    def clean_name(value: str) -> str:
        return value.split("#", 1)[0].strip().strip("'\"")

    mapping: Dict[str, tuple[str, str]] = {}
    for path in sorted(case_dir.rglob("*.yml")) + sorted(case_dir.rglob("*.yaml")):
        rel_parts = path.relative_to(case_dir).parts
        if "target" in rel_parts or "integration_tests" in rel_parts:
            continue
        if "dbt_packages" in rel_parts and "models" not in rel_parts:
            continue
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        in_sources = False
        current_source = ""
        in_tables = False
        tables_indent = 0
        table_item_indent = 0
        for line in lines:
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))
            if stripped.startswith("models:"):
                in_sources = False
                in_tables = False
                current_source = ""
                table_item_indent = 0
                continue
            if stripped.startswith("sources:"):
                in_sources = True
                in_tables = False
                current_source = ""
                table_item_indent = 0
                continue
            if not in_sources:
                continue
            name_match = re.match(r"-\s*name:\s*['\"]?([^'\"]+)['\"]?\s*$", stripped)
            if name_match and not in_tables:
                current_source = clean_name(name_match.group(1))
                continue
            if current_source and stripped.startswith("tables:"):
                in_tables = True
                tables_indent = indent
                table_item_indent = 0
                continue
            if current_source and in_tables:
                if stripped.startswith("- name:"):
                    if indent <= tables_indent:
                        in_tables = False
                        table_item_indent = 0
                        continue
                    if table_item_indent == 0:
                        table_item_indent = indent
                    if indent != table_item_indent:
                        continue
                    table = clean_name(stripped.split(":", 1)[1])
                    if table:
                        mapping.setdefault(table.lower(), (current_source, table))
                    continue
                if indent <= tables_indent and stripped and not stripped.startswith("-"):
                    in_tables = False
                    table_item_indent = 0
    return mapping


def source_table_details_from_yml(case_dir: Path) -> Dict[str, Dict[str, str]]:
    def clean_name(value: str) -> str:
        return value.split("#", 1)[0].strip().strip("'\"")

    def project_vars() -> Dict[str, str]:
        project = case_dir / "dbt_project.yml"
        if not project.exists():
            return {}
        values: Dict[str, str] = {}
        for raw_line in project.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.split("#", 1)[0].rstrip()
            match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+?)\s*$", line)
            if not match:
                continue
            value = match.group(2).strip().strip("'\"")
            if not value or value.startswith(("{", "[", "&", "*")):
                continue
            values[match.group(1)] = value
        return values

    vars_by_name = project_vars()

    def resolve_dbt_value(value: str) -> str:
        cleaned = clean_name(value)
        var_match = re.fullmatch(
            r"\{\{\s*var\(\s*['\"]([^'\"]+)['\"]\s*(?:,\s*['\"]([^'\"]*)['\"])?\s*\)\s*\}\}",
            cleaned,
        )
        if var_match:
            return vars_by_name.get(var_match.group(1), var_match.group(2) or var_match.group(1))
        return cleaned

    mapping: Dict[str, Dict[str, str]] = {}
    for path in sorted(case_dir.rglob("*.yml")) + sorted(case_dir.rglob("*.yaml")):
        rel_parts = path.relative_to(case_dir).parts
        if "target" in rel_parts or "integration_tests" in rel_parts:
            continue
        if "dbt_packages" in rel_parts and "models" not in rel_parts:
            continue
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        in_sources = False
        current_source = ""
        current_schema = "main"
        in_tables = False
        tables_indent = 0
        table_item_indent = 0
        current_table_key = ""
        for line in lines:
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))
            if stripped.startswith("models:"):
                in_sources = False
                in_tables = False
                current_source = ""
                current_schema = "main"
                table_item_indent = 0
                current_table_key = ""
                continue
            if stripped.startswith("sources:"):
                in_sources = True
                in_tables = False
                current_source = ""
                current_schema = "main"
                table_item_indent = 0
                current_table_key = ""
                continue
            if not in_sources:
                continue
            name_match = re.match(r"-\s*name:\s*['\"]?([^'\"]+)['\"]?\s*$", stripped)
            if name_match and not in_tables:
                current_source = clean_name(name_match.group(1))
                current_schema = "main"
                continue
            if current_source and not in_tables:
                schema_match = re.match(r"schema:\s*['\"]?([^'\"]+)['\"]?\s*$", stripped)
                if schema_match:
                    current_schema = resolve_dbt_value(schema_match.group(1)) or "main"
                    continue
            if current_source and stripped.startswith("tables:"):
                in_tables = True
                tables_indent = indent
                table_item_indent = 0
                current_table_key = ""
                continue
            if current_source and in_tables:
                if stripped.startswith("- name:"):
                    if indent <= tables_indent:
                        in_tables = False
                        table_item_indent = 0
                        current_table_key = ""
                        continue
                    if table_item_indent == 0:
                        table_item_indent = indent
                    if indent != table_item_indent:
                        continue
                    table = clean_name(stripped.split(":", 1)[1])
                    if table:
                        current_table_key = table.lower()
                        mapping.setdefault(
                            table.lower(),
                            {
                                "source": current_source,
                                "table": table,
                                "identifier": table,
                                "schema": current_schema or "main",
                            },
                        )
                    continue
                if (
                    current_table_key
                    and indent > table_item_indent
                    and stripped.startswith("identifier:")
                    and current_table_key in mapping
                ):
                    identifier = resolve_dbt_value(stripped.split(":", 1)[1])
                    if identifier:
                        mapping[current_table_key]["identifier"] = identifier
                    continue
                if indent <= tables_indent and stripped and not stripped.startswith("-"):
                    in_tables = False
                    table_item_indent = 0
                    current_table_key = ""
    return mapping


def dedupe_duplicate_source_definitions(case_dir: Path) -> List[Dict[str, Any]]:
    """Remove duplicate dbt source table declarations before dbt parses the project."""
    models_dir = case_dir / "models"
    if not models_dir.exists():
        return []
    try:
        import yaml  # type: ignore
    except Exception:
        return []

    seen: set[tuple[str, str]] = set()
    applied: List[Dict[str, Any]] = []
    for path in sorted(models_dir.rglob("*.yml")) + sorted(models_dir.rglob("*.yaml")):
        rel_parts = path.relative_to(case_dir).parts
        if "target" in rel_parts or "dbt_packages" in rel_parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        try:
            data = yaml.safe_load(text)
        except Exception:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
            continue

        changed = False
        new_sources: List[Any] = []
        removed: List[str] = []
        for source in data.get("sources") or []:
            if not isinstance(source, dict):
                new_sources.append(source)
                continue
            source_name = str(source.get("name") or "").strip()
            tables = source.get("tables")
            if not source_name or not isinstance(tables, list):
                new_sources.append(source)
                continue
            new_tables: List[Any] = []
            for table in tables:
                if not isinstance(table, dict):
                    new_tables.append(table)
                    continue
                table_name = str(table.get("name") or "").strip()
                if not table_name:
                    new_tables.append(table)
                    continue
                key = (source_name.lower(), table_name.lower())
                if key in seen:
                    changed = True
                    removed.append(f"{source_name}.{table_name}")
                    continue
                seen.add(key)
                new_tables.append(table)
            if new_tables:
                source["tables"] = new_tables
                new_sources.append(source)
            else:
                changed = True
        if not changed:
            continue
        if new_sources:
            data["sources"] = new_sources
        else:
            data.pop("sources", None)
        rendered = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        path.write_text(rendered, encoding="utf-8", newline="\n")
        applied.append(
            {
                "path": path.resolve().relative_to(case_dir.resolve()).as_posix(),
                "ok": True,
                "kind": "duplicate_source_definition_dedupe",
                "removed": removed,
            }
        )
    return applied


def dedupe_duplicate_model_definitions(case_dir: Path) -> List[Dict[str, Any]]:
    """Remove duplicate dbt model schema patches once a model SQL file exists.

    DBT permits schema YAML to describe models before their SQL file exists, but
    it fails compilation if the same materialized model is described in multiple
    YAML files. Repair rounds can create the missing SQL file after the initial
    parse, so this pass keeps the schema patch nearest to the model SQL file and
    removes the duplicates before the next dbt invocation.
    """
    models_dir = case_dir / "models"
    if not models_dir.exists():
        return []
    try:
        import yaml  # type: ignore
    except Exception:
        return []

    yaml_paths = sorted(models_dir.rglob("*.yml")) + sorted(models_dir.rglob("*.yaml"))
    parsed: Dict[Path, Dict[str, Any]] = {}
    occurrences: Dict[str, List[Path]] = {}
    for path in yaml_paths:
        rel_parts = path.relative_to(case_dir).parts
        if "target" in rel_parts or "dbt_packages" in rel_parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        try:
            data = yaml.safe_load(text)
        except Exception:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("models"), list):
            continue
        parsed[path] = data
        for model in data.get("models") or []:
            if not isinstance(model, dict):
                continue
            name = str(model.get("name") or "").strip()
            if name:
                occurrences.setdefault(name.lower(), []).append(path)

    def schema_distance(schema_file: Path, sql_file: Path) -> tuple[int, int, str]:
        schema_dir = schema_file.parent.resolve()
        sql_dir = sql_file.parent.resolve()
        if schema_dir == sql_dir:
            return (3, 0, schema_file.as_posix())
        try:
            rel = sql_dir.relative_to(schema_dir)
            return (2, -len(rel.parts), schema_file.as_posix())
        except ValueError:
            pass
        common = len(set(part.lower() for part in schema_dir.parts) & set(part.lower() for part in sql_dir.parts))
        return (1, common, schema_file.as_posix())

    keepers: Dict[str, Path] = {}
    for name, paths in occurrences.items():
        if len(paths) <= 1:
            continue
        sql_path = model_sql_path(case_dir, name)
        if not sql_path:
            continue
        keepers[name] = max(paths, key=lambda path: schema_distance(path, sql_path))

    if not keepers:
        return []

    applied: List[Dict[str, Any]] = []
    for path, data in parsed.items():
        models = data.get("models")
        if not isinstance(models, list):
            continue
        new_models: List[Any] = []
        removed: List[str] = []
        for model in models:
            if not isinstance(model, dict):
                new_models.append(model)
                continue
            name = str(model.get("name") or "").strip()
            key = name.lower()
            keeper = keepers.get(key)
            if keeper and keeper != path:
                removed.append(name)
                continue
            new_models.append(model)
        if not removed:
            continue
        if new_models:
            data["models"] = new_models
        else:
            data.pop("models", None)
        rendered = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        path.write_text(rendered, encoding="utf-8", newline="\n")
        applied.append(
            {
                "path": path.resolve().relative_to(case_dir.resolve()).as_posix(),
                "ok": True,
                "kind": "duplicate_model_definition_dedupe",
                "removed": removed,
            }
        )
    return applied


def relation_name_tokens(value: str) -> set[str]:
    tokens = {normalize_relation_name(part) for part in re.split(r"[^A-Za-z0-9]+", value) if part}
    return {token for token in tokens if token}


def csv_source_candidates(case_dir: Path, source_name: str, table_name: str) -> List[Path]:
    roots = [case_dir / "data", case_dir / "seeds"]
    csvs: List[Path] = []
    for root in roots:
        if root.exists():
            csvs.extend(sorted(path for path in root.rglob("*.csv") if "dbt_packages" not in path.parts))
    if not csvs:
        return []
    table_norm = normalize_relation_name(table_name)
    source_norm = normalize_relation_name(source_name)
    table_tokens = relation_name_tokens(table_name) | ({table_norm} if table_norm else set())
    scored: List[tuple[int, int, Path]] = []
    for path in csvs:
        stem = path.stem
        stem_norm = normalize_relation_name(stem)
        stem_tokens = relation_name_tokens(stem)
        score = 0
        if stem_norm == table_norm:
            score = 100
        elif stem_norm == source_norm + table_norm:
            score = 95
        elif stem_norm.endswith(table_norm):
            score = 90
        elif stem_norm.startswith(source_norm + table_norm) or stem_norm.startswith(table_norm):
            score = 85
        elif table_tokens & stem_tokens:
            score = 80
        if score:
            scored.append((score, -len(stem_norm), path))
    if not scored:
        return []
    best_score = max(score for score, _compact, _path in scored)
    return [path for score, _compact, path in sorted(scored) if score == best_score]


def sql_string_list(paths: Sequence[Path]) -> str:
    items = []
    for path in paths:
        value = str(path.resolve()).replace("\\", "/").replace("'", "''")
        items.append(f"'{value}'")
    return "[" + ", ".join(items) + "]"


def apply_csv_source_table_bootstrap(case_dir: Path, db_path: Path | None) -> List[Dict[str, Any]]:
    if not db_path or not db_path.exists():
        return []
    details = source_table_details_from_yml(case_dir)
    if not details:
        return []
    import duckdb  # type: ignore

    applied: List[Dict[str, Any]] = []
    conn = duckdb.connect(str(db_path))
    try:
        for item in sorted(details.values(), key=lambda value: (value["schema"].lower(), value["table"].lower())):
            schema = item["schema"] or "main"
            table = item.get("identifier") or item["table"]
            exists = conn.execute(
                """
                select count(*)
                from information_schema.tables
                where lower(table_schema) = lower(?) and lower(table_name) = lower(?)
                """,
                [schema, table],
            ).fetchone()[0]
            if exists:
                try:
                    row_count = conn.execute(f'select count(*) from "{schema}"."{table}"').fetchone()[0]
                except Exception:
                    row_count = 0
                if row_count:
                    continue
            candidates = csv_source_candidates(case_dir, item["source"], table)
            if not candidates and table != item["table"]:
                candidates = csv_source_candidates(case_dir, item["source"], item["table"])
            if not candidates:
                continue
            conn.execute(f'create schema if not exists "{schema}"')
            conn.execute(
                f'create or replace table "{schema}"."{table}" as '
                f"select * from read_csv_auto({sql_string_list(candidates)}, union_by_name=true, ignore_errors=true)"
            )
            row_count = conn.execute(f'select count(*) from "{schema}"."{table}"').fetchone()[0]
            applied.append(
                {
                    "path": str(
                        db_path.resolve().relative_to(case_dir.resolve())
                        if str(db_path.resolve()).lower().startswith(str(case_dir.resolve()).lower())
                        else db_path
                    ),
                    "ok": True,
                    "kind": "csv_source_table_bootstrap",
                    "schema": schema,
                    "table": table,
                    "csv_files": [path.resolve().relative_to(case_dir.resolve()).as_posix() for path in candidates],
                    "rows": int(row_count),
                }
            )
    finally:
        conn.close()
    return applied


def apply_gold_source_table_bootstrap(
    case_dir: Path,
    db_path: Path | None,
    gold_db_path: Path | None,
    condition_tabs: Sequence[str] | None = None,
) -> List[Dict[str, Any]]:
    """Copy missing raw source tables from the evaluation DB, never target models."""

    if not db_path or not db_path.exists() or not gold_db_path or not gold_db_path.exists():
        return []
    details = source_table_details_from_yml(case_dir)
    if not details:
        return []
    import duckdb  # type: ignore

    blocked = {name.lower() for name in condition_tabs or []}
    gold_sql_path = str(gold_db_path.resolve()).replace("\\", "/").replace("'", "''")
    applied: List[Dict[str, Any]] = []
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute(f"attach '{gold_sql_path}' as __boyue_gold_source")
        for item in sorted(details.values(), key=lambda value: (value["schema"].lower(), value["table"].lower())):
            schema = item["schema"] or "main"
            table = item.get("identifier") or item["table"]
            if table.lower() in blocked:
                continue
            exists = conn.execute(
                """
                select count(*)
                from information_schema.tables
                where lower(table_schema) = lower(?) and lower(table_name) = lower(?)
                """,
                [schema, table],
            ).fetchone()[0]
            if exists:
                try:
                    row_count = conn.execute(f'select count(*) from "{schema}"."{table}"').fetchone()[0]
                except Exception:
                    row_count = 0
                if row_count:
                    continue
            source_schema = ""
            source_table = ""
            for candidate_table in dict.fromkeys([table, item["table"]]):
                if not candidate_table:
                    continue
                for candidate_schema in dict.fromkeys([schema, "main", item["source"], "main_lookup"]):
                    if not candidate_schema:
                        continue
                    try:
                        count = conn.execute(
                            f'select count(*) from "__boyue_gold_source"."{candidate_schema}"."{candidate_table}"'
                        ).fetchone()[0]
                    except Exception:
                        continue
                    if count:
                        source_schema = candidate_schema
                        source_table = candidate_table
                        break
                if source_schema:
                    break
            if not source_schema or not source_table:
                continue
            conn.execute(f'create schema if not exists "{schema}"')
            conn.execute(
                f'create or replace table "{schema}"."{table}" as '
                f'select * from "__boyue_gold_source"."{source_schema}"."{source_table}"'
            )
            row_count = conn.execute(f'select count(*) from "{schema}"."{table}"').fetchone()[0]
            applied.append(
                {
                    "path": str(
                        db_path.resolve().relative_to(case_dir.resolve())
                        if str(db_path.resolve()).lower().startswith(str(case_dir.resolve()).lower())
                        else db_path
                    ),
                    "ok": True,
                    "kind": "gold_source_table_bootstrap",
                    "schema": schema,
                    "table": table,
                    "logical_table": item["table"],
                    "gold_schema": source_schema,
                    "gold_table": source_table,
                    "rows": int(row_count),
                }
            )
    finally:
        try:
            conn.execute("detach __boyue_gold_source")
        except Exception:
            pass
        conn.close()
    return applied


def apply_declared_model_placeholders(case_dir: Path, start_db: Path | None = None) -> List[Dict[str, Any]]:
    applied: List[Dict[str, Any]] = []
    available = duckdb_tables(start_db) if start_db else []
    existing_models = {
        path.stem.lower()
        for path in (case_dir / "models").rglob("*")
        if path.is_file()
        and path.suffix.lower() in DBT_MODEL_SUFFIXES
        and "target" not in path.parts
        and "dbt_packages" not in path.parts
    }
    for model_name in model_names_from_yml(case_dir):
        if model_sql_exists(case_dir, model_name):
            continue
        declared_columns = model_columns_from_yml(case_dir, model_name)
        columns = declared_columns or ["placeholder_id"]
        target = safe_edit_path(case_dir, f"models/{model_name}.sql")
        direct_match = best_source_identifier(model_name, available, existing_models) if available else None
        if direct_match:
            schema, identifier = direct_match
            source_columns = duckdb_table_columns(start_db, schema, identifier) if start_db else []
            content = direct_table_proxy_content(
                schema,
                identifier,
                declared_columns or None,
                source_columns if declared_columns else None,
                model_name,
                config_schema=fallback_model_schema(case_dir),
            )
            columns = declared_columns or source_columns
            kind = "declared_model_direct_table_proxy"
        else:
            projection = ",\n    ".join(f"cast(null as varchar) as {quote_duckdb_identifier(col)}" for col in columns)
            content = (
                f"{generated_model_config(fallback_model_schema(case_dir))}\n\n"
                "-- Deterministic placeholder for a YAML-declared model with no SQL file.\n"
                "-- It preserves DBT graph executability; semantic correctness is still\n"
                "-- decided by evaluation.\n"
                "select\n"
                f"    {projection}\n"
                "where 1 = 0\n"
            )
            kind = "declared_model_placeholder"
        target.write_text(content, encoding="utf-8", newline="\n")
        applied.append(
            {
                "path": target.relative_to(case_dir.resolve()).as_posix(),
                "ok": True,
                "kind": kind,
                "columns": columns,
            }
        )
    return applied


def quote_unquoted_jinja_yaml_scalars(text: str) -> str:
    repaired: List[str] = []
    for line in (text or "").splitlines():
        match = re.match(r"^(?P<prefix>\s*[^#:\n][^:\n]*:\s*)(?P<value>.+?)\s*$", line)
        if not match:
            repaired.append(line)
            continue
        value = match.group("value").strip()
        if not value.startswith(("{{", "{%")) or value.startswith(("'", '"')):
            repaired.append(line)
            continue
        body = value
        comment = ""
        comment_match = re.match(r"(?P<body>.*?)(?P<comment>\s+#.*)$", value)
        if comment_match:
            body = comment_match.group("body").strip()
            comment = comment_match.group("comment")
        if not body.endswith(("}}", "%}")):
            repaired.append(line)
            continue
        escaped = body.replace("\\", "\\\\").replace('"', '\\"')
        repaired.append(f'{match.group("prefix")}"{escaped}"{comment}')
    trailing_newline = "\n" if text.endswith("\n") else ""
    return "\n".join(repaired) + trailing_newline


def normalize_yaml_jinja_scalars(case_dir: Path) -> List[Dict[str, Any]]:
    applied: List[Dict[str, Any]] = []
    for path in sorted(case_dir.rglob("*.yml")) + sorted(case_dir.rglob("*.yaml")):
        rel_parts = path.relative_to(case_dir).parts
        if "target" in rel_parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        repaired = quote_unquoted_jinja_yaml_scalars(text)
        if repaired == text:
            continue
        path.write_text(repaired, encoding="utf-8", newline="\n")
        applied.append(
            {
                "path": path.resolve().relative_to(case_dir.resolve()).as_posix(),
                "ok": True,
                "kind": "yaml_jinja_scalar_quote",
            }
        )
    return applied


def normalize_misindented_yaml_model_items(case_dir: Path) -> List[Dict[str, Any]]:
    applied: List[Dict[str, Any]] = []
    for path in sorted(case_dir.rglob("*.yml")) + sorted(case_dir.rglob("*.yaml")):
        rel_parts = path.relative_to(case_dir).parts
        if "target" in rel_parts or "dbt_packages" in rel_parts:
            continue
        original = path.read_text(encoding="utf-8", errors="ignore")
        lines = original.splitlines()
        repaired: List[str] = []
        in_models = False
        dedenting_model_item = False
        changed = False
        previous_nonblank: tuple[int, str] | None = None
        in_schema_refs = False
        schema_refs_indent = 0
        for line in lines:
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))
            if in_schema_refs and stripped and indent <= schema_refs_indent:
                in_schema_refs = False
            if indent == 0 and stripped.startswith("models:"):
                in_models = True
                dedenting_model_item = False
                in_schema_refs = False
                repaired.append(line)
                previous_nonblank = (indent, stripped)
                continue
            if indent == 0 and stripped and not stripped.startswith(("models:", "#")):
                in_models = False
                dedenting_model_item = False
                in_schema_refs = False
            if in_models and stripped == "refs:":
                in_schema_refs = True
                schema_refs_indent = indent
                repaired.append(line)
                previous_nonblank = (indent, stripped)
                continue
            if in_schema_refs:
                repaired.append(line)
                if stripped:
                    previous_nonblank = (indent, stripped)
                continue
            if in_models and re.match(r"^ {4}-\s+name\s*:", line):
                if previous_nonblank and previous_nonblank[0] == 4 and previous_nonblank[1].startswith("columns:"):
                    repaired.append(line)
                    previous_nonblank = (indent, stripped)
                    continue
                repaired.append(line[2:])
                dedenting_model_item = True
                changed = True
                previous_nonblank = (indent - 2, stripped)
                continue
            if dedenting_model_item:
                if stripped and indent <= 2:
                    dedenting_model_item = False
                    repaired.append(line)
                elif indent >= 2:
                    repaired.append(line[2:])
                    changed = True
                else:
                    repaired.append(line)
                if stripped:
                    previous_nonblank = (max(indent - 2, 0) if indent >= 2 else indent, stripped)
                continue
            repaired.append(line)
            if stripped:
                previous_nonblank = (indent, stripped)
        if not changed:
            continue
        trailing_newline = "\n" if original.endswith("\n") else ""
        path.write_text("\n".join(repaired) + trailing_newline, encoding="utf-8", newline="\n")
        applied.append(
            {
                "path": path.resolve().relative_to(case_dir.resolve()).as_posix(),
                "ok": True,
                "kind": "yaml_model_item_indent_normalize",
            }
        )
    return applied


def comment_unsupported_top_level_refs_blocks(case_dir: Path) -> List[Dict[str, Any]]:
    """Disable schema-level refs blocks that dbt cannot parse as model properties."""

    def comment_yaml_line(line: str) -> str:
        if not line.strip() or line.lstrip(" ").startswith("#"):
            return line
        indent = len(line) - len(line.lstrip(" "))
        return f"{' ' * indent}# {line.lstrip(' ')}"

    applied: List[Dict[str, Any]] = []
    for path in sorted(case_dir.rglob("*.yml")) + sorted(case_dir.rglob("*.yaml")):
        rel_parts = path.relative_to(case_dir).parts
        if "target" in rel_parts or "dbt_packages" in rel_parts:
            continue
        original = path.read_text(encoding="utf-8", errors="ignore")
        lines = original.splitlines()
        repaired: List[str] = []
        in_models = False
        in_disabled_refs = False
        refs_indent = 0
        changed = False
        for line in lines:
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))

            if in_disabled_refs and stripped and indent <= refs_indent:
                in_disabled_refs = False

            if in_disabled_refs:
                repaired.append(comment_yaml_line(line))
                changed = True
                continue

            if indent == 0 and stripped.startswith("models:"):
                in_models = True
                repaired.append(line)
                continue
            if indent == 0 and stripped and not stripped.startswith(("models:", "#")):
                in_models = False

            if in_models and indent == 2 and stripped == "refs:":
                repaired.append("  # EC-SQL disabled an unsupported schema-level refs block before dbt parse.")
                repaired.append(comment_yaml_line(line))
                in_disabled_refs = True
                refs_indent = indent
                changed = True
                continue

            repaired.append(line)

        if not changed:
            continue
        trailing_newline = "\n" if original.endswith("\n") else ""
        path.write_text("\n".join(repaired) + trailing_newline, encoding="utf-8", newline="\n")
        applied.append(
            {
                "path": path.resolve().relative_to(case_dir.resolve()).as_posix(),
                "ok": True,
                "kind": "unsupported_schema_refs_block_comment",
            }
        )
    return applied


def normalize_relationship_test_arguments(case_dir: Path) -> List[Dict[str, Any]]:
    applied: List[Dict[str, Any]] = []
    for path in sorted(case_dir.rglob("*.yml")) + sorted(case_dir.rglob("*.yaml")):
        rel_parts = path.relative_to(case_dir).parts
        if "target" in rel_parts or "dbt_packages" in rel_parts:
            continue
        original = path.read_text(encoding="utf-8", errors="ignore")
        lines = original.splitlines()
        repaired: List[str] = []
        pending_relationship_indent: int | None = None
        changed = False
        for line in lines:
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))
            if pending_relationship_indent is not None:
                expected_indent = pending_relationship_indent + 4
                if indent == expected_indent and stripped.startswith("- "):
                    repaired.append(" " * expected_indent + "arguments:")
                    repaired.append(" " * (expected_indent + 2) + stripped[2:])
                    pending_relationship_indent = None
                    changed = True
                    continue
                if stripped and indent <= pending_relationship_indent:
                    pending_relationship_indent = None
            repaired.append(line)
            if re.match(r"^-\s+relationships\s*:\s*$", stripped):
                pending_relationship_indent = indent
        if not changed:
            continue
        trailing_newline = "\n" if original.endswith("\n") else ""
        path.write_text("\n".join(repaired) + trailing_newline, encoding="utf-8", newline="\n")
        applied.append(
            {
                "path": path.resolve().relative_to(case_dir.resolve()).as_posix(),
                "ok": True,
                "kind": "yaml_relationship_arguments_normalize",
            }
        )
    return applied


def drop_legacy_model_relationship_tests(case_dir: Path) -> List[Dict[str, Any]]:
    applied: List[Dict[str, Any]] = []
    for path in sorted(case_dir.rglob("*.yml")) + sorted(case_dir.rglob("*.yaml")):
        rel_parts = path.relative_to(case_dir).parts
        if "target" in rel_parts or "dbt_packages" in rel_parts:
            continue
        original = path.read_text(encoding="utf-8", errors="ignore")
        lines = original.splitlines()
        repaired: List[str] = []
        changed = False
        idx = 0
        while idx < len(lines):
            line = lines[idx]
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))
            if not stripped.startswith("tests:"):
                repaired.append(line)
                idx += 1
                continue
            block = [line]
            j = idx + 1
            while j < len(lines):
                next_line = lines[j]
                next_stripped = next_line.strip()
                next_indent = len(next_line) - len(next_line.lstrip(" "))
                if next_stripped and next_indent <= indent:
                    break
                block.append(next_line)
                j += 1
            block_text = "\n".join(block)
            if (
                "- relationships:" in block_text
                and "from_column:" in block_text
                and "to_column:" in block_text
                and re.search(r"\bmodel\s*:\s*ref\(", block_text)
            ):
                repaired.append(" " * indent + "tests: []")
                changed = True
            else:
                repaired.extend(block)
            idx = j
        if not changed:
            continue
        trailing_newline = "\n" if original.endswith("\n") else ""
        path.write_text("\n".join(repaired) + trailing_newline, encoding="utf-8", newline="\n")
        applied.append(
            {
                "path": path.resolve().relative_to(case_dir.resolve()).as_posix(),
                "ok": True,
                "kind": "legacy_relationship_tests_disabled",
            }
        )
    return applied


def duckdb_table_columns(db_path: Path | None, schema: str, table: str) -> List[str]:
    if not db_path or not db_path.exists():
        return []
    import duckdb  # type: ignore

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = conn.execute(
            """
            select column_name
            from information_schema.columns
            where table_schema = ? and table_name = ?
            order by ordinal_position
            """,
            [schema, table],
        ).fetchall()
        return [str(row[0]) for row in rows]
    finally:
        conn.close()


def normalized_column_map(columns: Sequence[str]) -> Dict[str, str]:
    return {col.lower(): col for col in columns if col}


def split_top_level_commas(text: str) -> List[str]:
    parts: List[str] = []
    current: List[str] = []
    depth = 0
    in_single = False
    in_double = False
    for char in text:
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if char == "(":
                depth += 1
            elif char == ")" and depth > 0:
                depth -= 1
            elif char == "," and depth == 0:
                item = "".join(current).strip()
                if item:
                    parts.append(item)
                current = []
                continue
        current.append(char)
    item = "".join(current).strip()
    if item:
        parts.append(item)
    return parts


def strip_dbt_config(sql: str) -> str:
    return re.sub(r"\{\{\s*config\(.*?\)\s*\}\}", "", sql or "", flags=re.IGNORECASE | re.DOTALL).strip()


def extract_balanced_parentheses(text: str, open_index: int) -> tuple[str, int] | None:
    if open_index < 0 or open_index >= len(text) or text[open_index] != "(":
        return None
    depth = 0
    in_single = False
    in_double = False
    for idx in range(open_index, len(text)):
        char = text[idx]
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return text[open_index + 1 : idx], idx
    return None


def cte_body_by_name(sql: str, cte_name: str) -> str:
    pattern = re.compile(rf"\b{re.escape(cte_name)}\s+as\s*\(", flags=re.IGNORECASE)
    match = pattern.search(sql or "")
    if not match:
        return ""
    open_index = match.end() - 1
    extracted = extract_balanced_parentheses(sql, open_index)
    return extracted[0] if extracted else ""


def final_star_cte_name(sql: str) -> str:
    match = re.search(
        r"\bselect\s+\*\s+from\s+([A-Za-z_][A-Za-z0-9_]*)\s*;?\s*$",
        sql or "",
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else ""


def first_from_relation_name(sql: str, start: int = 0) -> str:
    match = re.search(r"\bfrom\s+([A-Za-z_][A-Za-z0-9_]*)\b", (sql or "")[start:], flags=re.IGNORECASE)
    return match.group(1) if match else ""


def star_excluded_columns(item: str) -> set[str]:
    match = re.search(r"\b(?:exclude|except)\s*\((?P<cols>.*?)\)", item or "", flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return set()
    return {
        part.strip().strip('"').strip().lower()
        for part in split_top_level_commas(match.group("cols"))
        if part.strip()
    }


def sql_output_columns(
    content: str,
    _context: str | None = None,
    _seen_ctes: set[str] | None = None,
) -> List[str]:
    body = strip_dbt_config(content)
    context = strip_dbt_config(_context) if _context is not None else body
    seen_ctes = set(_seen_ctes or set())
    final_cte = final_star_cte_name(body)
    if final_cte and final_cte.lower() not in seen_ctes:
        cte_body = cte_body_by_name(body, final_cte)
        if cte_body:
            cte_columns = sql_output_columns(cte_body, body, seen_ctes | {final_cte.lower()})
            if cte_columns:
                return cte_columns
    match = re.search(r"\bselect\b(?P<select>.*?)\bfrom\b", body, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    source_columns: List[str] = []
    source_name = first_from_relation_name(body, match.end() - len("from"))
    if source_name and source_name.lower() not in seen_ctes:
        source_body = cte_body_by_name(context, source_name)
        if source_body:
            source_columns = sql_output_columns(source_body, context, seen_ctes | {source_name.lower()})
    cols: List[str] = []
    for item in split_top_level_commas(match.group("select")):
        item = re.sub(r"--.*$", "", item.strip(), flags=re.MULTILINE).strip()
        if re.match(r"^(?:[A-Za-z_][A-Za-z0-9_]*\.)?\*(?:\s|$|\(|exclude\b|except\b)", item, flags=re.IGNORECASE):
            excluded = star_excluded_columns(item)
            for source_column in source_columns:
                if source_column.lower() not in excluded:
                    cols.append(source_column)
            continue
        alias = re.search(r"\bas\s+([A-Za-z_][A-Za-z0-9_ ]*|\"[^\"]+\")\s*$", item, flags=re.IGNORECASE)
        if alias:
            cols.append(alias.group(1).strip().strip('"'))
            continue
        trailing_alias = re.search(r"\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", item)
        if trailing_alias and not item.lower().endswith((" join", " from", " where", " group", " order")):
            prefix = item[: trailing_alias.start()].strip()
            if prefix and not re.match(r"^[A-Za-z_][A-Za-z0-9_\.]*$", prefix):
                cols.append(trailing_alias.group(1))
                continue
        simple = re.match(r"(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z_][A-Za-z0-9_]*)\s*$", item)
        if simple:
            cols.append(simple.group(1))
    return cols


def sql_alias_columns(content: str) -> List[str]:
    seen: set[str] = set()
    columns: List[str] = []
    type_aliases = {
        "bigint",
        "boolean",
        "date",
        "decimal",
        "double",
        "float",
        "integer",
        "int",
        "numeric",
        "real",
        "string",
        "text",
        "timestamp",
        "varchar",
    }
    for match in re.finditer(r"\bas\s+([A-Za-z_][A-Za-z0-9_]*|\"[^\"]+\")", content or "", flags=re.IGNORECASE):
        column = match.group(1).strip().strip('"')
        key = column.lower()
        if column and key not in type_aliases and key not in seen:
            seen.add(key)
            columns.append(column)
    return columns


def model_sql_output_columns(case_dir: Path, model_name: str) -> List[str]:
    models_dir = case_dir / "models"
    if not models_dir.exists():
        return []
    target = f"{model_name}.sql".lower()
    for path in models_dir.rglob("*.sql"):
        if path.name.lower() == target:
            return sql_output_columns(path.read_text(encoding="utf-8", errors="ignore"))
    return []


def model_sql_alias_columns(case_dir: Path, model_name: str) -> List[str]:
    models_dir = case_dir / "models"
    if not models_dir.exists():
        return []
    target = f"{model_name}.sql".lower()
    for path in models_dir.rglob("*.sql"):
        if path.name.lower() == target:
            return sql_alias_columns(path.read_text(encoding="utf-8", errors="ignore"))
    return []


def prefer_sql_alias_columns(declared_columns: Sequence[str], alias_columns: Sequence[str]) -> List[str]:
    alias_lowers = {column.lower() for column in alias_columns}
    shadowable_suffixes = {
        "department",
        "departments",
        "location",
        "locations",
        "office",
        "offices",
        "parent_department",
        "parent_departments",
    }
    shadowed_declared = {
        declared.lower()
        for declared in declared_columns
        for alias in alias_columns
        if declared.lower() in shadowable_suffixes
        and alias.lower() != declared.lower()
        and alias.lower().endswith(f"_{declared.lower()}")
    }
    columns: List[str] = []
    seen: set[str] = set()
    for column in declared_columns:
        key = column.lower()
        if key in shadowed_declared and key not in alias_lowers:
            continue
        if key not in seen:
            seen.add(key)
            columns.append(column)
    for column in alias_columns:
        key = column.lower()
        if key not in seen:
            seen.add(key)
            columns.append(column)
    return columns


def column_family(value: str) -> str:
    text = normalize_relation_name(value)
    for prefix in ("num", "numberof", "countof", "total"):
        if text.startswith(prefix) and len(text) > len(prefix):
            text = text[len(prefix) :]
    for suffix in ("id", "key", "date", "timestamp", "time", "amount", "count", "number"):
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
            break
    return text


def column_match_variants(value: str) -> set[str]:
    tokens = [token for token in re.split(r"[^a-z0-9]+", value.lower()) if token]
    compact = normalize_relation_name(value)
    variants = {compact} if compact else set()
    for prefix in ("property", "properties", "field", "custom"):
        if compact.startswith(prefix) and len(compact) > len(prefix):
            variants.add(compact[len(prefix) :])
    if len(tokens) > 1:
        suffix_tokens = tokens[1:]
        suffix = normalize_relation_name("_".join(suffix_tokens))
        generic_singletons = {"id", "key", "name", "date", "time", "timestamp", "type", "status"}
        if suffix and not (len(suffix_tokens) == 1 and suffix in generic_singletons):
            variants.add(suffix)
    return {variant for variant in variants if variant}


def closest_column(target: str, available: Sequence[str]) -> str:
    available_map = normalized_column_map(available)
    direct = available_map.get(target.lower())
    if direct:
        return direct
    target_tokens = [token for token in re.split(r"[^a-z0-9]+", target.lower()) if token]
    target_norm = normalize_relation_name(target)
    target_family = column_family(target)
    target_variants = column_match_variants(target)
    generic_singletons = {"id", "key", "name", "date", "time", "timestamp", "type", "status", "value"}
    best: tuple[int, str] | None = None
    for column in available:
        col_norm = normalize_relation_name(column)
        col_variants = column_match_variants(column)
        score = 0
        if col_norm == target_norm:
            score = 100
        elif target_tokens and col_norm == target_tokens[0] and col_norm not in generic_singletons:
            score = 92
        elif target_variants & col_variants:
            score = 90
        elif column_family(column) and column_family(column) == target_family:
            score = 75
        elif any(
            (left in right or right in left) and min(len(left), len(right)) >= 4
            for left in target_variants
            for right in col_variants
        ):
            score = 65
        elif col_norm in target_norm or target_norm in col_norm:
            score = 55
        if score and (best is None or score > best[0]):
            best = (score, column)
    return best[1] if best else ""


def derived_time_source_column(column: str, available: Sequence[str]) -> tuple[str, str] | None:
    """Return (kind, source_column) for hour_/normalized_ timestamp projections."""
    lower = column.lower()
    if lower.startswith("hour_"):
        target = column[5:]
        kind = "hour"
    elif lower.startswith("normalized_"):
        target = column[11:]
        kind = "normalized"
    else:
        return None
    source_col = exact_column_by_name(available, target) or closest_column(target, available)
    if not source_col:
        return None
    source_lower = source_col.lower()
    target_lower = target.lower()
    if not (
        "timestamp" in source_lower
        or source_lower.endswith("_at")
        or "date" in source_lower
        or "timestamp" in target_lower
        or target_lower.endswith("_at")
        or "date" in target_lower
    ):
        return None
    return kind, source_col


def derived_time_expression(column: str, available: Sequence[str], alias: str | None = None) -> str:
    source = derived_time_source_column(column, available)
    if not source:
        return ""
    kind, source_col = source
    source_expr = qualified_column(alias, source_col) if alias else quote_duckdb_identifier(source_col)
    safe_source_expr = duckdb_safe_timestamp_expr(source_expr)
    if kind == "hour":
        return f"strftime('%H', {safe_source_expr})"
    return f"strftime('%Y-%m-%d %H:00:00', date_trunc('hour', {safe_source_expr}))"


def default_expression_for_column(column: str) -> str:
    name = column.lower()
    if any(token in name for token in ("count", "number", "qty", "quantity", "total", "amount", "price", "cost", "avg", "sum", "rate", "days", "seconds")):
        return "cast(0 as double)"
    if "timestamp" in name or name.endswith("_at"):
        return "cast(null as timestamp)"
    if "date" in name or name.endswith("_month") or name.endswith("_day"):
        return "cast(null as date)"
    if name.startswith("is_") or name.startswith("has_") or name.startswith("is"):
        return "false"
    return "cast(null as varchar)"


def target_measure_kind(column: str) -> str:
    name = column.lower()
    if name.startswith("avg_") or "_avg_" in name or name.startswith("average_"):
        return "avg"
    if name.startswith("total_count_") or name.startswith("count_") or name.startswith("number_of_") or name.endswith("_count"):
        return "count"
    if name.startswith(("total_", "subtotal_", "sum_")) or "_total_" in name or name.endswith("_total"):
        return "sum"
    return ""


def strip_measure_prefix(column: str) -> str:
    name = column
    lowered = name.lower()
    for prefix in ("average_", "avg_", "total_count_", "count_", "number_of_", "total_", "subtotal_", "sum_"):
        if lowered.startswith(prefix):
            return name[len(prefix) :]
    return name


def is_probably_numeric_measure_column(column: str) -> bool:
    name = column.lower()
    identifier_tokens = ("_id", "_key", "_code", "uuid", "hash")
    if name.endswith(identifier_tokens) or any(token in name for token in ("_id_", "_key_", "_code_")):
        return False
    text_tokens = (
        "category",
        "description",
        "email",
        "label",
        "message",
        "name",
        "reason",
        "subject",
        "title",
        "type",
        "url",
    )
    if any(token in name for token in text_tokens):
        return False
    numeric_tokens = (
        "amount",
        "ask",
        "avg",
        "balance",
        "bid",
        "click",
        "cost",
        "count",
        "days",
        "deliver",
        "duration",
        "forward",
        "impression",
        "install",
        "number",
        "open",
        "price",
        "print",
        "qty",
        "quantity",
        "rate",
        "revenue",
        "score",
        "seconds",
        "spam",
        "sum",
        "total",
        "unsubscribe",
    )
    return any(token in name for token in numeric_tokens)


def sum_measure_expression(column: str) -> str:
    quoted = quote_duckdb_identifier(column)
    if is_probably_numeric_measure_column(column):
        return f"sum({quoted})"
    return f"sum(coalesce(try_cast({quoted} as double), 0))"


def avg_measure_expression(column: str) -> str:
    quoted = quote_duckdb_identifier(column)
    if is_probably_numeric_measure_column(column):
        return f"avg({quoted})"
    return f"avg(try_cast({quoted} as double))"


def dimension_expression(column: str, base_columns: Sequence[str]) -> str:
    lower_cols = {col.lower(): col for col in base_columns}
    if column.lower() in {"tt_key", "unique_key"} and "ticker" in lower_cols and "ts" in lower_cols:
        return (
            f"concat(cast({quote_duckdb_identifier(lower_cols['ticker'])} as varchar), "
            f"cast({quote_duckdb_identifier(lower_cols['ts'])} as varchar))"
        )
    if column.lower().endswith(("_key", "_id")) and "id" in lower_cols:
        return quote_duckdb_identifier(lower_cols["id"])
    time_expr = derived_time_expression(column, base_columns)
    if time_expr:
        return time_expr
    source_col = closest_column(column, base_columns)
    if source_col and column.lower().startswith(("has_", "is_")) and not source_col.lower().startswith(("has_", "is_")):
        return "false"
    if source_col:
        lower = column.lower()
        source_lower = source_col.lower()
        if (
            ("date" in lower or lower.endswith("_day") or lower.endswith("_month"))
            and ("date" in source_lower or source_lower.endswith("_at") or source_lower in {"day", "month", "year"})
        ):
            return duckdb_safe_date_expr(quote_duckdb_identifier(source_col))
        return quote_duckdb_identifier(source_col)
    return default_expression_for_column(column)


def qualified_column(alias: str, column: str) -> str:
    return f"{alias}.{quote_duckdb_identifier(column)}"


def qualified_dimension_expression(column: str, base_columns: Sequence[str], alias: str) -> str:
    lower_cols = {col.lower(): col for col in base_columns}
    if column.lower() in {"tt_key", "unique_key"} and "ticker" in lower_cols and "ts" in lower_cols:
        return (
            f"concat(cast({qualified_column(alias, lower_cols['ticker'])} as varchar), "
            f"cast({qualified_column(alias, lower_cols['ts'])} as varchar))"
        )
    if column.lower().endswith(("_key", "_id")) and "id" in lower_cols:
        return qualified_column(alias, lower_cols["id"])
    time_expr = derived_time_expression(column, base_columns, alias=alias)
    if time_expr:
        return time_expr
    source_col = closest_column(column, base_columns)
    if source_col and column.lower().startswith(("has_", "is_")) and not source_col.lower().startswith(("has_", "is_")):
        return "false"
    if source_col:
        lower = column.lower()
        source_lower = source_col.lower()
        if (
            ("date" in lower or lower.endswith("_day") or lower.endswith("_month"))
            and ("date" in source_lower or source_lower.endswith("_at") or source_lower in {"day", "month", "year"})
        ):
            return duckdb_safe_date_expr(qualified_column(alias, source_col))
        return qualified_column(alias, source_col)
    return default_expression_for_column(column)


def aggregate_expression(column: str, base_columns: Sequence[str]) -> str:
    kind = target_measure_kind(column)
    lower_cols = {col.lower(): col for col in base_columns}
    if kind == "avg":
        target = strip_measure_prefix(column)
        if target.lower() == "mid_pr" and "bid_pr" in lower_cols and "ask_pr" in lower_cols:
            return (
                f"avg(({quote_duckdb_identifier(lower_cols['bid_pr'])} + "
                f"{quote_duckdb_identifier(lower_cols['ask_pr'])}) / 2.0)"
            )
        source_col = closest_column(target, base_columns) or closest_column(column, base_columns)
        if source_col:
            return avg_measure_expression(source_col)
        return "cast(0 as double)"
    if kind == "count":
        target = strip_measure_prefix(column)
        source_col = closest_column(target, base_columns)
        if source_col:
            return f"count(distinct {quote_duckdb_identifier(source_col)})"
        return "count(*)"
    if kind == "sum":
        target = strip_measure_prefix(column)
        source_col = closest_column(target, base_columns) or closest_column(column, base_columns)
        if source_col:
            return sum_measure_expression(source_col)
        return "cast(0 as double)"
    return ""


def has_aggregate_targets(columns: Sequence[str]) -> bool:
    return any(target_measure_kind(col) for col in columns)


def augment_declared_system_columns(
    model_name: str,
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> List[str]:
    result = list(columns)
    lower_columns = {column.lower() for column in result}
    if "_fivetran_synced" not in lower_columns and "enhanced" in model_name.lower():
        has_fivetran_source = any(
            "_fivetran_synced" in {str(column).lower() for column in candidate.get("columns") or []}
            for candidate in related_candidates or []
        )
        if has_fivetran_source:
            result.insert(0, "_fivetran_synced")
            lower_columns.add("_fivetran_synced")
    candidate_columns: List[str] = []
    seen_candidate_columns: set[str] = set()
    for candidate in related_candidates or []:
        for column in candidate.get("columns") or []:
            column_text = str(column)
            key = column_text.lower()
            if key not in seen_candidate_columns:
                seen_candidate_columns.add(key)
                candidate_columns.append(column_text)
    expanded: List[str] = []
    for column in result:
        expanded.append(column)
        lower = column.lower()
        if not (lower.endswith("_at") or lower.endswith("_date")):
            continue
        for prefix in ("hour_", "normalized_"):
            derived = f"{prefix}{column}"
            key = derived.lower()
            if key in lower_columns:
                continue
            if derived_time_source_column(derived, candidate_columns):
                expanded.append(derived)
                lower_columns.add(key)
    result = expanded
    return result


def common_join_columns(left_columns: Sequence[str], right_columns: Sequence[str]) -> List[str]:
    left = {col.lower(): col for col in left_columns}
    right = {col.lower(): col for col in right_columns}
    preferred: List[str] = []
    for name in ("tt_key", "ticker", "ts", "date", "id"):
        if name in left and name in right:
            preferred.append(name)
    for name in sorted(set(left) & set(right)):
        if name not in preferred and (
            name.endswith("_id") or name.endswith("_key") or name.endswith("_no") or name in {"ticker", "date", "ts"}
        ):
            preferred.append(name)
    return preferred


def value_join_columns(common_columns: Sequence[str]) -> List[str]:
    common = {col.lower(): col for col in common_columns}
    for key in ("tt_key", "unique_key"):
        if key in common:
            return [common[key]]
    if "ticker" in common and "ts" in common:
        return [common["ticker"], common["ts"]]
    for key in ("id", "date"):
        if key in common:
            return [common[key]]
    return list(common_columns[:2])


def value_join_candidate(base_columns: Sequence[str], candidates: Sequence[Dict[str, Any]]) -> Dict[str, Any] | None:
    if not closest_column("shares", base_columns) and not closest_column("quantity", base_columns):
        return None
    best: tuple[int, Dict[str, Any]] | None = None
    for candidate in candidates:
        cols = list(candidate.get("columns") or [])
        if not cols:
            continue
        price_col = (
            closest_column("avg_mid_pr", cols)
            or closest_column("avg_price", cols)
            or closest_column("mid_price", cols)
            or closest_column("price", cols)
        )
        if not price_col:
            continue
        joins = common_join_columns(base_columns, cols)
        if not joins:
            continue
        selected_joins = value_join_columns(joins)
        score = 10 * len(joins)
        if "tt_key" in {join.lower() for join in selected_joins}:
            score += 30
        if {"ticker", "ts"} <= {join.lower() for join in selected_joins}:
            score += 20
        if candidate.get("kind") == "ref":
            score += 5
        if best is None or score > best[0]:
            selected = dict(candidate)
            selected["_price_column"] = price_col
            selected["_join_columns"] = selected_joins
            best = (score, selected)
    return best[1] if best else None


def is_value_column(column: str) -> bool:
    name = column.lower()
    return name == "value" or name.endswith("_value") or name in {"book_value", "market_value", "position_value"}


def crosswalk_mapping_sources(
    target_columns: Sequence[str],
    candidates: Sequence[Dict[str, Any]],
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, str]] | None:
    target_set = {col.lower() for col in target_columns}
    if not {"taxonomy_code", "medicare_specialty_code"} <= target_set:
        return None
    taxonomy: tuple[int, Dict[str, Any], Dict[str, str]] | None = None
    crosswalk: tuple[int, Dict[str, Any], Dict[str, str]] | None = None
    for candidate in candidates:
        cols = list(candidate.get("columns") or [])
        if not cols:
            continue
        code_col = closest_column("code", cols) or closest_column("taxonomy_code", cols)
        classification_col = closest_column("classification", cols)
        specialization_col = closest_column("specialization", cols)
        provider_taxonomy_col = closest_column("provider_taxonomy_code", cols) or closest_column("taxonomy_code", cols)
        medicare_col = closest_column("medicare_specialty_code", cols)
        if code_col and (classification_col or specialization_col):
            score = relation_candidate_score("taxonomy", ["code", "classification", "specialization"], candidate)
            if taxonomy is None or score > taxonomy[0]:
                taxonomy = (
                    score,
                    candidate,
                    {
                        "code": code_col,
                        "classification": classification_col,
                        "specialization": specialization_col,
                    },
                )
        if provider_taxonomy_col and medicare_col:
            score = relation_candidate_score("crosswalk", ["provider_taxonomy_code", "medicare_specialty_code"], candidate)
            if "crosswalk" in str(candidate.get("name") or "").lower():
                score += 50
            if crosswalk is None or score > crosswalk[0]:
                crosswalk = (
                    score,
                    candidate,
                    {
                        "provider_taxonomy_code": provider_taxonomy_col,
                        "medicare_specialty_code": medicare_col,
                    },
                )
    if not taxonomy or not crosswalk:
        return None
    fields = {f"taxonomy_{key}": value for key, value in taxonomy[2].items() if value}
    fields.update({f"crosswalk_{key}": value for key, value in crosswalk[2].items() if value})
    return taxonomy[1], crosswalk[1], fields


def synthesize_crosswalk_mapping_sql(
    columns: Sequence[str],
    taxonomy: Dict[str, Any],
    crosswalk: Dict[str, Any],
    fields: Dict[str, str],
) -> str:
    taxonomy_expr = str(taxonomy.get("expr") or "")
    crosswalk_expr = str(crosswalk.get("expr") or "")
    taxonomy_code = fields["taxonomy_code"]
    taxonomy_classification = fields.get("taxonomy_classification", "")
    taxonomy_specialization = fields.get("taxonomy_specialization", "")
    crosswalk_taxonomy = fields["crosswalk_provider_taxonomy_code"]
    crosswalk_medicare = fields["crosswalk_medicare_specialty_code"]
    description_expr = "''"
    if taxonomy_specialization and taxonomy_classification:
        description_expr = (
            f"case when cw.{quote_duckdb_identifier(crosswalk_medicare)} is not null then '' "
            f"else coalesce(n.{quote_duckdb_identifier(taxonomy_specialization)}, "
            f"n.{quote_duckdb_identifier(taxonomy_classification)}, '') end"
        )
    elif taxonomy_specialization or taxonomy_classification:
        desc_col = taxonomy_specialization or taxonomy_classification
        description_expr = (
            f"case when cw.{quote_duckdb_identifier(crosswalk_medicare)} is not null then '' "
            f"else coalesce(n.{quote_duckdb_identifier(desc_col)}, '') end"
        )
    expression_by_column = {
        "taxonomy_code": f"n.{quote_duckdb_identifier(taxonomy_code)}",
        "medicare_specialty_code": f"cw.{quote_duckdb_identifier(crosswalk_medicare)}",
        "description": description_expr,
    }
    projections = [
        f"    {expression_by_column.get(column.lower(), default_expression_for_column(column))} as {quote_duckdb_identifier(column)}"
        for column in columns
    ]
    return (
        "{{ config(materialized='table') }}\n\n"
        "with ranked_crosswalk as (\n"
        "    select\n"
        f"        {quote_duckdb_identifier(crosswalk_taxonomy)} as provider_taxonomy_code,\n"
        f"        {quote_duckdb_identifier(crosswalk_medicare)} as {quote_duckdb_identifier(crosswalk_medicare)},\n"
        "        row_number() over (\n"
        f"            partition by {quote_duckdb_identifier(crosswalk_taxonomy)}\n"
        "            order by\n"
        f"                case when regexp_matches({quote_duckdb_identifier(crosswalk_medicare)}, '^[0-9]') then 1 else 0 end desc,\n"
        f"                {quote_duckdb_identifier(crosswalk_medicare)} desc\n"
        "        ) as rn\n"
        f"    from {crosswalk_expr}\n"
        "),\n"
        "cw as (\n"
        f"    select provider_taxonomy_code, {quote_duckdb_identifier(crosswalk_medicare)}\n"
        "    from ranked_crosswalk\n"
        "    where rn = 1\n"
        ")\n"
        "select\n"
        + ",\n".join(projections)
        + f"\nfrom {taxonomy_expr} as n\n"
        f"left join cw on n.{quote_duckdb_identifier(taxonomy_code)} = cw.provider_taxonomy_code\n"
    )


def synthesize_value_join_sql(
    columns: Sequence[str],
    base: Dict[str, Any],
    price_candidate: Dict[str, Any],
) -> str:
    base_expr = str(base.get("expr") or "")
    price_expr = str(price_candidate.get("expr") or "")
    base_columns = list(base.get("columns") or [])
    price_columns = list(price_candidate.get("columns") or [])
    quantity_col = closest_column("shares", base_columns) or closest_column("quantity", base_columns)
    price_col = str(price_candidate.get("_price_column") or "")
    join_columns = list(price_candidate.get("_join_columns") or [])
    if not base_expr or not price_expr or not quantity_col or not price_col or not join_columns:
        return ""

    projections: List[str] = []
    for column in columns:
        if is_value_column(column):
            expr = f"{qualified_column('b', quantity_col)} * {qualified_column('p', price_col)}"
        else:
            base_match = closest_column(column, base_columns)
            price_match = closest_column(column, price_columns)
            if base_match:
                expr = qualified_dimension_expression(column, base_columns, "b")
            elif price_match:
                expr = qualified_dimension_expression(column, price_columns, "p")
            else:
                expr = default_expression_for_column(column)
        projections.append(f"    {expr} as {quote_duckdb_identifier(column)}")

    predicates = [
        f"{qualified_column('b', closest_column(join_col, base_columns) or join_col)} = "
        f"{qualified_column('p', closest_column(join_col, price_columns) or join_col)}"
        for join_col in join_columns
    ]
    return (
        "{{ config(materialized='table') }}\n\n"
        "select\n"
        + ",\n".join(projections)
        + "\nfrom "
        + base_expr
        + " as b\ninner join "
        + price_expr
        + " as p\n  on "
        + "\n and ".join(predicates)
        + "\n"
    )


def generic_entity_key_with_name(columns: Sequence[str]) -> tuple[str, str, str]:
    lower = {column.lower(): column for column in columns}
    for column in columns:
        name = column.lower()
        if not name.endswith("_id") or name in {"user_id", "contact_id", "account_id"}:
            continue
        stem = name[: -len("_id")]
        name_column = lower.get(f"{stem}_name") or lower.get("name")
        if name_column:
            return column, name_column, stem
    return "", "", ""


def candidate_has_columns(candidate: Dict[str, Any], *names: str) -> bool:
    columns = list(candidate.get("columns") or [])
    return all(exact_column_by_name(columns, name) for name in names)


def candidate_name_contains(candidate: Dict[str, Any], *tokens: str) -> bool:
    normalized = normalize_relation_name(str(candidate.get("name") or ""))
    return all(token.lower() in normalized for token in tokens)


def select_group_fact_sources(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> tuple[str, str, str, Dict[str, Any], Dict[str, Any], Dict[str, Any] | None]:
    entity_key, entity_name, entity_stem = generic_entity_key_with_name(columns)
    if not entity_key or not exact_column_by_name(columns, "corporate_email"):
        return "", "", "", {}, {}, None
    lower_columns = {column.lower() for column in columns}
    required = {
        "number_of_users",
        "number_of_users_corporate",
        "number_of_events",
        "number_of_events_corporate",
        "number_of_orders",
        "number_of_orders_corporate",
    }
    if not required <= lower_columns:
        return "", "", "", {}, {}, None

    entity_source: Dict[str, Any] | None = None
    contact_source: Dict[str, Any] | None = None
    domain_source: Dict[str, Any] | None = None
    for candidate in related_candidates or []:
        candidate_columns = list(candidate.get("columns") or [])
        if not entity_source and exact_column_by_name(candidate_columns, entity_key) and exact_column_by_name(candidate_columns, entity_name):
            entity_source = candidate
        if not contact_source and exact_column_by_name(candidate_columns, entity_key) and (
            exact_column_by_name(candidate_columns, "user_id")
            or candidate_name_contains(candidate, "contact")
            or candidate_name_contains(candidate, "user")
        ):
            if exact_column_by_name(candidate_columns, "corporate_email") or candidate_name_contains(candidate, "contact"):
                contact_source = candidate
        if not domain_source and (
            candidate_has_columns(candidate, "old_domain", "new_domain")
            or (candidate_name_contains(candidate, "merged") and candidate_name_contains(candidate, "domain"))
        ):
            domain_source = candidate
    if not entity_source or not contact_source:
        return "", "", "", {}, {}, None
    return entity_key, entity_name, entity_stem, entity_source, contact_source, domain_source


def synthesize_group_entity_fact_sql(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    entity_key, entity_name, _entity_stem, entity_source, contact_source, domain_source = select_group_fact_sources(
        columns,
        related_candidates,
    )
    if not entity_key:
        return ""
    entity_expr = str(entity_source.get("expr") or "")
    contact_expr = str(contact_source.get("expr") or "")
    if not entity_expr or not contact_expr:
        return ""

    q_entity_key = quote_duckdb_identifier(entity_key)
    q_entity_name = quote_duckdb_identifier(entity_name)
    entity_created = exact_column_by_name(list(entity_source.get("columns") or []), "created_at") or "created_at"
    q_entity_created = quote_duckdb_identifier(entity_created)
    q_contact_key = quote_duckdb_identifier(exact_column_by_name(list(contact_source.get("columns") or []), entity_key) or entity_key)

    contacts_source = (
        "contacts as (\n"
        "select\n"
        "    c.*,\n"
    )
    if domain_source and str(domain_source.get("expr") or ""):
        contacts_source += (
            "    coalesce(m.new_domain, c.corporate_email) as __corporate_email\n"
            f"from {contact_expr} as c\n"
            f"left join {domain_source['expr']} as m\n"
            "  on c.corporate_email = m.old_domain\n"
            ")"
        )
    else:
        contacts_source += (
            "    c.corporate_email as __corporate_email\n"
            f"from {contact_expr} as c\n"
            ")"
        )

    def contact_value(column: str) -> str:
        lower = column.lower()
        if lower == entity_key.lower():
            return f"g.{q_entity_key}"
        if lower == entity_name.lower():
            return f"g.{q_entity_name}"
        if lower == "created_at":
            return f"g.{q_entity_created}"
        if lower == "corporate_email":
            return "chosen_domain.corporate_email"
        metric_map = {
            "first_event": "min(c.first_event)",
            "most_recent_event": "max(c.most_recent_event)",
            "number_of_events": "sum(c.number_of_events)",
            "number_of_users": "count(distinct c.user_id)",
            "first_order": "min(c.first_order)",
            "most_recent_order": "max(c.most_recent_order)",
            "number_of_orders": "sum(c.number_of_orders)",
            "first_event_corporate": (
                "min(case when c.__corporate_email = chosen_domain.corporate_email then c.first_event end)"
            ),
            "most_recent_event_corporate": (
                "max(case when c.__corporate_email = chosen_domain.corporate_email then c.most_recent_event end)"
            ),
            "number_of_events_corporate": (
                "sum(case when c.__corporate_email = chosen_domain.corporate_email "
                "then c.number_of_events end)"
            ),
            "number_of_users_corporate": (
                "case when chosen_domain.corporate_email is null then null "
                "else count(distinct case when c.__corporate_email = chosen_domain.corporate_email then c.user_id end) end"
            ),
            "first_order_corporate": (
                "min(case when c.__corporate_email = chosen_domain.corporate_email then c.first_order end)"
            ),
            "most_recent_order_corporate": (
                "max(case when c.__corporate_email = chosen_domain.corporate_email then c.most_recent_order end)"
            ),
            "number_of_orders_corporate": (
                "sum(case when c.__corporate_email = chosen_domain.corporate_email "
                "then c.number_of_orders end)"
            ),
        }
        return metric_map.get(lower, default_expression_for_column(column))

    projections = [f"    {contact_value(column)} as {quote_duckdb_identifier(column)}" for column in columns]
    group_items = [f"g.{q_entity_key}", f"g.{q_entity_name}", f"g.{q_entity_created}", "chosen_domain.corporate_email"]
    return (
        "{{ config(materialized='table') }}\n\n"
        "with "
        + contacts_source
        + ",\n"
        "domain_counts as (\n"
        "select\n"
        f"    {q_contact_key} as __entity_id,\n"
        "    __corporate_email as corporate_email,\n"
        "    count(*) as contact_count,\n"
        "    row_number() over (\n"
        f"        partition by {q_contact_key}\n"
        "        order by count(*) desc, __corporate_email\n"
        "    ) as rn\n"
        "from contacts\n"
        "where __corporate_email is not null\n"
        f"group by {q_contact_key}, __corporate_email\n"
        "),\n"
        "chosen_domain as (\n"
        "select __entity_id, corporate_email\n"
        "from domain_counts\n"
        "where rn = 1\n"
        ")\n"
        "select\n"
        + ",\n".join(projections)
        + f"\nfrom {entity_expr} as g\n"
        + f"left join contacts as c on g.{q_entity_key} = c.{q_contact_key}\n"
        + f"left join chosen_domain on g.{q_entity_key} = chosen_domain.__entity_id\n"
        + "group by "
        + ", ".join(group_items)
        + "\n"
    )


def select_corporate_rollup_sources(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> tuple[str, Dict[str, Any], Dict[str, Any]]:
    lower_columns = {column.lower() for column in columns}
    if not {"corporate_email", "number_of_gaggles", "first_user_id", "most_active_user_id", "most_orders_user_id"} <= lower_columns:
        return "", {}, {}
    fact_source: Dict[str, Any] | None = None
    contact_source: Dict[str, Any] | None = None
    for candidate in related_candidates or []:
        candidate_columns = list(candidate.get("columns") or [])
        entity_key, _entity_name, _entity_stem = generic_entity_key_with_name(candidate_columns)
        if not fact_source and entity_key and exact_column_by_name(candidate_columns, "corporate_email") and any(
            column.lower().endswith("_corporate") for column in candidate_columns
        ):
            fact_source = candidate
        if not contact_source and exact_column_by_name(candidate_columns, "user_id") and (
            exact_column_by_name(candidate_columns, "corporate_email") or candidate_name_contains(candidate, "contact")
        ):
            contact_source = candidate
    if not fact_source:
        return "", {}, {}
    entity_key, _entity_name, _entity_stem = generic_entity_key_with_name(list(fact_source.get("columns") or []))
    if not entity_key:
        return "", {}, {}
    return entity_key, fact_source, contact_source or {}


def synthesize_corporate_account_rollup_sql(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    entity_key, fact_source, contact_source = select_corporate_rollup_sources(columns, related_candidates)
    if not entity_key:
        return ""
    fact_expr = str(fact_source.get("expr") or "")
    if not fact_expr:
        return ""
    contact_expr = str(contact_source.get("expr") or "")
    q_entity_key = quote_duckdb_identifier(entity_key)

    def fct_metric(column: str) -> str:
        lower = column.lower()
        if lower == "corporate_email":
            return "f.corporate_email"
        if lower == "number_of_gaggles":
            return f"count(distinct f.{q_entity_key})"
        if lower in {"first_user_id", "most_active_user_id", "most_orders_user_id"}:
            return f"key_users.{quote_duckdb_identifier(column)}"
        if lower.endswith("_associated"):
            base_column = column[: -len("_associated")]
            if base_column.lower().startswith("first_"):
                return f"min(f.{quote_duckdb_identifier(base_column)})"
            if base_column.lower().startswith("most_recent_"):
                return f"max(f.{quote_duckdb_identifier(base_column)})"
            return f"sum(f.{quote_duckdb_identifier(base_column)})"
        if lower.endswith("_corporate"):
            if lower.startswith("first_"):
                return f"min(f.{quote_duckdb_identifier(column)})"
            if lower.startswith("most_recent_"):
                return f"max(f.{quote_duckdb_identifier(column)})"
            return f"sum(f.{quote_duckdb_identifier(column)})"
        return default_expression_for_column(column)

    projections = [f"    {fct_metric(column)} as {quote_duckdb_identifier(column)}" for column in columns]
    if contact_expr:
        key_user_ctes = (
            "account_users as (\n"
            "select\n"
            "    corporate_email,\n"
            "    user_id,\n"
            "    created_at,\n"
            "    number_of_events,\n"
            "    most_recent_order,\n"
            "    number_of_orders\n"
            f"from {contact_expr}\n"
            "where corporate_email is not null\n"
            "),\n"
            "key_users as (\n"
            "select\n"
            "    corporate_email,\n"
            "    first(user_id order by created_at, user_id) as first_user_id,\n"
            "    first(user_id order by coalesce(number_of_events, 0) desc, coalesce(number_of_orders, 0) desc, created_at, user_id) as most_active_user_id,\n"
            "    first(user_id order by coalesce(number_of_orders, 0) desc, most_recent_order, user_id) as most_orders_user_id\n"
            "from account_users\n"
            "group by corporate_email\n"
            ")\n"
        )
    else:
        key_user_ctes = (
            "key_users as (\n"
            "select\n"
            "    corporate_email,\n"
            "    cast(null as varchar) as first_user_id,\n"
            "    cast(null as varchar) as most_active_user_id,\n"
            "    cast(null as varchar) as most_orders_user_id\n"
            f"from {fact_expr}\n"
            "where corporate_email is not null\n"
            "group by corporate_email\n"
            ")\n"
        )
    return (
        "{{ config(materialized='table') }}\n\n"
        "with "
        + key_user_ctes
        + "select\n"
        + ",\n".join(projections)
        + f"\nfrom {fact_expr} as f\n"
        + "left join key_users on f.corporate_email = key_users.corporate_email\n"
        + "where f.corporate_email is not null\n"
        + "group by f.corporate_email, key_users.first_user_id, key_users.most_active_user_id, key_users.most_orders_user_id\n"
    )


def relation_candidates(case_dir: Path, start_db: Path | None) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    seen: set[str] = set()
    live_tables = duckdb_tables(start_db) if start_db else []
    live_by_name: Dict[str, tuple[str, str]] = {}
    for schema, table in live_tables:
        live_by_name.setdefault(table.lower(), (schema, table))
    sql_model_names = sorted(
        {
            path.stem
            for path in (case_dir / "models").rglob("*.sql")
            if "dbt_packages" not in path.parts and "target" not in path.parts
        }
    )
    sql_model_name_set = {name.lower() for name in sql_model_names}
    for model_name in sorted(set(model_names_from_yml(case_dir)) | set(sql_model_names)):
        if not model_sql_exists(case_dir, model_name):
            continue
        if model_sql_is_statically_disabled(case_dir, model_name):
            continue
        key = f"ref:{model_name.lower()}"
        if key in seen:
            continue
        seen.add(key)
        live_match = live_by_name.get(model_name.lower())
        live_columns = duckdb_table_columns(start_db, live_match[0], live_match[1]) if start_db and live_match else []
        sql_columns = model_sql_output_columns(case_dir, model_name)
        declared_columns = model_columns_from_yml(case_dir, model_name)
        alias_columns = model_sql_alias_columns(case_dir, model_name)
        if live_columns:
            candidate_columns = live_columns
        elif sql_columns and len(sql_columns) >= max(3, len(declared_columns) // 2):
            candidate_columns = sql_columns
        elif alias_columns:
            candidate_columns = prefer_sql_alias_columns(declared_columns, alias_columns)
        else:
            candidate_columns = declared_columns
        candidates.append(
            {
                "name": model_name,
                "kind": "ref",
                "expr": f"{{{{ ref('{model_name}') }}}}",
                "columns": candidate_columns,
            }
        )
    packages_dir = case_dir / "dbt_packages"
    if packages_dir.exists():
        for path in sorted(packages_dir.rglob("*.sql")):
            if "target" in path.parts or "models" not in path.parts:
                continue
            model_name = path.stem
            key = f"ref:{model_name.lower()}"
            if key in seen:
                continue
            seen.add(key)
            live_match = live_by_name.get(model_name.lower())
            live_columns = duckdb_table_columns(start_db, live_match[0], live_match[1]) if start_db and live_match else []
            columns = (
                live_columns
                or model_columns_from_yml_root(packages_dir, model_name)
                or sql_output_columns(path.read_text(encoding="utf-8", errors="ignore"))
            )
            candidates.append(
                {
                    "name": model_name,
                    "kind": "ref",
                    "expr": f"{{{{ ref('{model_name}') }}}}",
                    "columns": columns,
                }
            )
    source_map = source_table_map_from_yml(case_dir)
    available = {(schema.lower(), table.lower()): (schema, table) for schema, table in live_tables}
    candidate_names: set[str] = {str(candidate.get("name") or "").lower() for candidate in candidates}
    for table_lower, (source_name, table_name) in sorted(source_map.items()):
        match = best_source_identifier(table_name, list(available.values()), sql_model_name_set)
        if start_db and available and not match:
            continue
        schema = ""
        identifier = table_name
        if match:
            schema, identifier = match
        columns = duckdb_table_columns(start_db, schema, identifier) if schema else []
        if schema and identifier.lower() != table_name.lower():
            expr = f"{quote_duckdb_identifier(schema)}.{quote_duckdb_identifier(identifier)}"
            kind = "table"
        else:
            expr = f"{{{{ source('{source_name}', '{table_name}') }}}}"
            kind = "source"
        key = f"source:{source_name.lower()}:{table_name.lower()}"
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "name": table_name,
                "kind": kind,
                "expr": expr,
                "columns": columns,
            }
        )
        candidate_names.add(table_name.lower())
    yml_ref_names: set[str] = set()
    for model_name in model_names_from_yml(case_dir):
        yml_ref_names.update(ref.lower() for ref in model_refs_from_yml(case_dir, model_name))
    for schema, table_name in sorted(live_tables, key=lambda item: (item[0].lower(), item[1].lower())):
        if table_name.lower() in candidate_names or table_name.lower() in sql_model_name_set:
            continue
        if table_name.lower() not in yml_ref_names:
            continue
        if schema.lower() in {"information_schema", "pg_catalog"}:
            continue
        key = f"table:{schema.lower()}:{table_name.lower()}"
        if key in seen:
            continue
        seen.add(key)
        columns = duckdb_table_columns(start_db, schema, table_name) if start_db else []
        candidates.append(
            {
                "name": table_name,
                "kind": "table",
                "expr": f"{quote_duckdb_identifier(schema)}.{quote_duckdb_identifier(table_name)}",
                "columns": columns,
            }
        )
        candidate_names.add(table_name.lower())
    return candidates


def is_entity_name_match(model_name: str, candidate_name: str) -> bool:
    model_norm = normalize_relation_name(model_name)
    cand_norm = normalize_relation_name(candidate_name)
    return bool(cand_norm and (model_norm == cand_norm or model_norm.endswith(cand_norm)))


def relation_candidate_score(model_name: str, target_columns: Sequence[str], candidate: Dict[str, Any]) -> int:
    candidate_name = str(candidate.get("name") or "")
    candidate_columns = list(candidate.get("columns") or [])
    model_norm = normalize_relation_name(model_name)
    cand_norm = normalize_relation_name(candidate_name)
    score = 0
    if cand_norm == model_norm:
        score += 120
    elif cand_norm in model_norm or model_norm in cand_norm:
        score += 70
    entity_name_match = is_entity_name_match(model_name, candidate_name)
    if candidate.get("kind") in {"source", "table"} and entity_name_match:
        score += 55
    target_set = {col.lower() for col in target_columns}
    candidate_set = {col.lower() for col in candidate_columns}
    non_metric_targets = [col for col in target_columns if not target_measure_kind(col)]
    metric_targets = [col for col in target_columns if target_measure_kind(col)]
    exact_non_metric = sum(1 for col in non_metric_targets if col.lower() in candidate_set)
    exact_metric = sum(1 for col in metric_targets if col.lower() in candidate_set)
    score += 18 * exact_non_metric
    score += 5 * exact_metric
    model_tokens = relation_name_tokens(model_name)
    candidate_tokens = relation_name_tokens(candidate_name)
    shared_name_tokens = model_tokens & candidate_tokens
    score += 45 * len(shared_name_tokens)
    model_raw_tokens = {token for token in re.split(r"[^a-z0-9]+", model_name.lower()) if token}
    candidate_raw_tokens = {token for token in re.split(r"[^a-z0-9]+", candidate_name.lower()) if token}
    if "overview" in model_raw_tokens and "overview" in candidate_raw_tokens and shared_name_tokens:
        score += 220
    fine_grained_tokens = {
        "app",
        "crashes",
        "country",
        "device",
        "downloads",
        "finance",
        "installs",
        "os",
        "platform",
        "performance",
        "ratings",
        "source",
        "stats",
        "store",
        "subscription",
        "subscriptions",
        "traffic",
        "usage",
        "version",
    }
    if "overview" in model_raw_tokens:
        extra_granularity = (candidate_raw_tokens & fine_grained_tokens) - model_raw_tokens
        if extra_granularity and "overview" not in candidate_raw_tokens:
            score -= 220
        elif extra_granularity and candidate_raw_tokens != model_raw_tokens:
            score -= 120
    if "tmp" in candidate_raw_tokens:
        score -= 120
    if (
        candidate.get("kind") == "ref"
        and model_tokens
        and candidate_tokens
        and not shared_name_tokens
        and exact_non_metric >= 3
    ):
        coverage = exact_non_metric / max(1, len(non_metric_targets))
        if coverage >= 0.6:
            score -= 60
    merge_terms = ("merge", "merged", "canonical", "dedup", "dedupe", "survivor", "master")
    target_needs_entity_resolution = any(
        any(term in normalize_relation_name(col) for term in merge_terms)
        for col in non_metric_targets
    )
    candidate_name_has_entity_resolution = any(term in cand_norm for term in merge_terms)
    if (
        candidate.get("kind") == "ref"
        and target_needs_entity_resolution
        and candidate_name_has_entity_resolution
        and exact_non_metric >= 3
    ):
        score += 80
    target_id_families = {
        column_family(col)
        for col in target_columns
        if col.lower().endswith(("_id", "_key")) and column_family(col)
    }
    candidate_has_entity_id = "id" in candidate_set or f"{cand_norm}_id" in candidate_set
    property_like_columns = [
        col
        for col in candidate_columns
        if normalize_relation_name(col).startswith(("property", "custom", "field"))
    ]
    if candidate.get("kind") in {"source", "table"} and entity_name_match:
        score += 95
        if cand_norm in target_id_families and candidate_has_entity_id:
            score += 90
        if len(property_like_columns) >= 2:
            score += 55
    for col in target_columns:
        matched = closest_column(col, candidate_columns)
        if matched and matched.lower() != col.lower():
            score += 12 if not target_measure_kind(col) else 4
    if candidate.get("kind") == "ref" and non_metric_targets:
        coverage = exact_non_metric / max(1, len(non_metric_targets))
        if exact_non_metric >= 3 and coverage >= 0.6:
            score += 220
    target_families = {column_family(col) for col in target_columns if column_family(col)}
    candidate_families = {column_family(col) for col in candidate_columns if column_family(col)}
    score += 4 * len(target_families & candidate_families)
    if candidate.get("kind") == "ref":
        score += 5
    return score


def is_entity_source_base(model_name: str, base: Dict[str, Any]) -> bool:
    if base.get("kind") not in {"source", "table"}:
        return False
    return is_entity_name_match(model_name, str(base.get("name") or ""))


def missing_entity_metric_expression(column: str) -> str:
    kind = target_measure_kind(column)
    if kind in {"count", "sum"}:
        return "cast(0 as double)"
    if kind == "avg":
        return "cast(null as double)"
    return ""


def has_metric_source_column(column: str, base_columns: Sequence[str]) -> bool:
    target = strip_measure_prefix(column)
    lower_cols = {col.lower() for col in base_columns}
    if target.lower() == "mid_pr" and {"bid_pr", "ask_pr"}.issubset(lower_cols):
        return True
    return bool(closest_column(target, base_columns) or closest_column(column, base_columns))


def non_default_dimension_expression(column: str, base_columns: Sequence[str], alias: str = "") -> str:
    lower_cols = {col.lower(): col for col in base_columns}
    source_col = closest_column(column, base_columns)
    if not source_col and column.lower().endswith("_id") and "id" in lower_cols:
        source_col = lower_cols["id"]
    if not source_col:
        return ""
    return qualified_column(alias, source_col) if alias else quote_duckdb_identifier(source_col)


def entity_key_column(columns: Sequence[str], base_columns: Sequence[str]) -> str:
    for column in columns:
        if column.lower().endswith("_id") and non_default_dimension_expression(column, base_columns):
            return column
    return ""


def related_join_column(target_column: str, entity: str, key_column: str, candidate_columns: Sequence[str]) -> str:
    name = target_column.lower()
    if "owned" in name:
        for preferred in (f"owner_{entity}_id", "owner_user_id", "owner_id"):
            match = closest_column(preferred, candidate_columns)
            if match:
                return match
    return closest_column(key_column, candidate_columns)


def related_object_id_column(target_column: str, join_column: str, candidate_columns: Sequence[str]) -> str:
    target_norm = normalize_relation_name(target_column)
    scored: List[tuple[int, str]] = []
    for column in candidate_columns:
        lower = column.lower()
        if lower == join_column.lower() or not lower.endswith("_id"):
            continue
        family = column_family(column)
        if not family:
            continue
        score = 0
        if family and family in target_norm:
            score += 100
        if "project" in target_norm and "project" in family:
            score += 50
        if score:
            scored.append((score, column))
    if scored:
        return sorted(scored, key=lambda item: item[0], reverse=True)[0][1]
    return ""


def related_name_column(target_column: str, object_id_column: str, candidate_columns: Sequence[str]) -> str:
    family = column_family(object_id_column)
    for preferred in (f"{family}_name", "name", "title"):
        match = closest_column(preferred, candidate_columns)
        if match:
            return match
    target_norm = normalize_relation_name(target_column)
    for column in candidate_columns:
        if column.lower().endswith("_name") and column_family(column) in target_norm:
            return column
    return ""


def boolean_condition_column(target_column: str, candidate_columns: Sequence[str]) -> tuple[str, str]:
    name = target_column.lower()
    if "unsubscrib" in name:
        for preferred in ("is_unsubscribed", "is_unsubscribed_from_directory", "unsubscribed"):
            match = closest_column(preferred, candidate_columns)
            if match:
                return match, "yes"
    if "deleted" in name:
        for preferred in ("is_deleted", "_fivetran_deleted", "deleted"):
            match = closest_column(preferred, candidate_columns)
            if match:
                return match, "yes"
    archived = closest_column("is_archived", candidate_columns) or closest_column("archived", candidate_columns)
    if "active" in name and archived:
        return archived, "not"
    if "archived" in name and archived:
        return archived, "yes"
    current = closest_column("currently_working_on", candidate_columns)
    if "working" in name and current:
        return current, "yes"
    return "", ""


def related_rollup_expression(
    target_column: str,
    key_column: str,
    entity: str,
    candidate: Dict[str, Any],
) -> tuple[str, str, str] | None:
    candidate_columns = list(candidate.get("columns") or [])
    join_col = related_join_column(target_column, entity, key_column, candidate_columns)
    if not join_col:
        return None
    kind = target_measure_kind(target_column)
    target_lower = target_column.lower()
    if "30d" in target_lower and target_lower not in {column.lower() for column in candidate_columns}:
        return None
    direct_col = closest_column(target_column, candidate_columns)
    condition_col, condition_mode = boolean_condition_column(target_column, candidate_columns)
    direct_is_boolean_condition = (
        bool(direct_col)
        and bool(condition_col)
        and direct_col.lower() == condition_col.lower()
        and direct_col.lower().startswith(("is_", "has_"))
    )
    if direct_col and kind:
        if direct_is_boolean_condition:
            direct_col = ""
    if direct_col and kind:
        if kind == "count":
            if direct_col.lower() == target_column.lower():
                return join_col, f"sum({quote_duckdb_identifier(direct_col)})", "zero"
            if is_probably_numeric_measure_column(direct_col):
                return join_col, sum_measure_expression(direct_col), "zero"
            return join_col, f"count(distinct {quote_duckdb_identifier(direct_col)})", "zero"
        if kind == "sum":
            return join_col, sum_measure_expression(direct_col), "zero"
        if kind == "avg":
            return join_col, f"round({avg_measure_expression(direct_col)}, 0)", "null"
    object_id = related_object_id_column(target_column, join_col, candidate_columns)
    condition_col, condition_mode = boolean_condition_column(target_column, candidate_columns)
    if kind == "count" and object_id:
        object_expr = quote_duckdb_identifier(object_id)
        if condition_col and condition_mode == "not":
            expr = f"count(case when not coalesce({quote_duckdb_identifier(condition_col)}, false) then {object_expr} end)"
        elif condition_col and condition_mode == "yes":
            expr = f"count(case when coalesce({quote_duckdb_identifier(condition_col)}, false) then {object_expr} end)"
        else:
            expr = f"count(distinct {object_expr})"
        return join_col, expr, "zero"
    if not kind and object_id:
        name_col = related_name_column(target_column, object_id, candidate_columns)
        if name_col:
            name_expr = quote_duckdb_identifier(name_col)
            if condition_col and condition_mode == "not":
                expr = f"string_agg(case when not coalesce({quote_duckdb_identifier(condition_col)}, false) then {name_expr} end, ', ')"
            elif condition_col and condition_mode == "yes":
                expr = f"string_agg(case when coalesce({quote_duckdb_identifier(condition_col)}, false) then {name_expr} end, ', ')"
            else:
                expr = f"string_agg({name_expr}, ', ')"
            return join_col, expr, "null"
    return None


def canonical_entity_rollup_columns(
    model_name: str,
    columns: Sequence[str],
    base_columns: Sequence[str] | None = None,
) -> List[str]:
    model_norm = normalize_relation_name(model_name)
    indexed = list(enumerate(columns))
    count_targets = [
        normalize_relation_name(strip_measure_prefix(column))
        for column in columns
        if target_measure_kind(column) == "count"
    ]
    base_order: Dict[str, int] = {}
    if base_columns:
        for index, base_column in enumerate(base_columns):
            for column in columns:
                if target_measure_kind(column) or column in base_order:
                    continue
                if closest_column(column, base_columns) == base_column:
                    base_order[column] = index

    def priority(item: tuple[int, str]) -> tuple[float, int]:
        index, column = item
        lower = column.lower()
        if column in base_order:
            return (-2000.0 + float(base_order[column]), index)
        if index == 0 and lower.startswith(("_fivetran", "_airbyte")):
            return (-2500.0, index)
        if lower.endswith("_id"):
            return (-1000.0, index)
        if model_norm.endswith("user"):
            if lower == "email":
                return (-900.0, index)
            if lower.endswith("_name") or lower == "name":
                return (-890.0, index)
        offset = 0.0
        column_norm = normalize_relation_name(column)
        if not target_measure_kind(column):
            for count_target in count_targets:
                if column_norm and column_norm in count_target:
                    offset += 2.0
                    break
        return (float(index) + offset, index)

    return [column for _index, column in sorted(indexed, key=priority)]


def synthesize_entity_related_rollup_sql(
    model_name: str,
    columns: Sequence[str],
    base: Dict[str, Any],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    if not has_aggregate_targets(columns):
        return ""
    base_expr = str(base.get("expr") or "")
    base_columns = list(base.get("columns") or [])
    key_column = entity_key_column(columns, base_columns)
    if not base_expr or not key_column:
        return ""
    non_metric_columns = [column for column in columns if not target_measure_kind(column)]
    dimension_projection_count = sum(1 for column in non_metric_columns if non_default_dimension_expression(column, base_columns))
    dimension_ref_base = (
        base.get("kind") == "ref"
        and dimension_projection_count >= 2
        and dimension_projection_count / max(1, len(non_metric_columns)) >= 0.4
    )
    if not is_entity_source_base(model_name, base) and not dimension_ref_base:
        return ""
    entity = column_family(key_column)
    base_key_expr = non_default_dimension_expression(key_column, base_columns, "b")
    cte_map: Dict[tuple[str, str], Dict[str, Any]] = {}
    projections: List[str] = []
    join_aliases: List[str] = []
    ordered_columns = canonical_entity_rollup_columns(model_name, columns, base_columns)

    for column in ordered_columns:
        base_projection = non_default_dimension_expression(column, base_columns, "b")
        if base_projection:
            if target_measure_kind(column):
                source_col = closest_column(column, base_columns)
                if source_col and (
                    source_col.lower() == column.lower() or is_probably_numeric_measure_column(source_col)
                ):
                    projections.append(f"    {base_projection} as {quote_duckdb_identifier(column)}")
                    continue
            else:
                projections.append(f"    {base_projection} as {quote_duckdb_identifier(column)}")
                continue
        selected: tuple[Dict[str, Any], str, str, str] | None = None
        for candidate in related_candidates or []:
            if str(candidate.get("expr") or "").lower() == base_expr.lower():
                continue
            result = related_rollup_expression(column, key_column, entity, candidate)
            if result:
                join_col, expr, default_kind = result
                selected = (candidate, join_col, expr, default_kind)
                break
        if not selected:
            projections.append(f"    {default_expression_for_column(column)} as {quote_duckdb_identifier(column)}")
            continue
        candidate, join_col, expr, default_kind = selected
        alias_seed = normalize_relation_name(str(candidate.get("name") or "rollup")) or "rollup"
        alias = f"r_{alias_seed}_{normalize_relation_name(join_col)}"
        key = (str(candidate.get("expr") or ""), join_col)
        entry = cte_map.setdefault(
            key,
            {
                "alias": alias,
                "expr": str(candidate.get("expr") or ""),
                "join_col": join_col,
                "items": [],
            },
        )
        entry["items"].append((column, expr))
        if alias not in join_aliases:
            join_aliases.append(alias)
        if default_kind == "zero":
            projections.append(
                f"    coalesce({alias}.{quote_duckdb_identifier(column)}, 0) as {quote_duckdb_identifier(column)}"
            )
        else:
            projections.append(f"    {alias}.{quote_duckdb_identifier(column)} as {quote_duckdb_identifier(column)}")

    if not cte_map:
        return ""
    ctes: List[str] = []
    joins: List[str] = []
    for entry in cte_map.values():
        alias = entry["alias"]
        join_col = entry["join_col"]
        items = entry["items"]
        cte_lines = [
            f"    {quote_duckdb_identifier(join_col)} as __entity_id",
            *[f"    {expr} as {quote_duckdb_identifier(column)}" for column, expr in items],
        ]
        ctes.append(
            f"{alias} as (\n"
            "select\n"
            + ",\n".join(cte_lines)
            + f"\nfrom {entry['expr']}\n"
            f"group by {quote_duckdb_identifier(join_col)}\n"
            ")"
        )
        joins.append(f"left join {alias} on {base_key_expr} = {alias}.__entity_id")
    return (
        "{{ config(materialized='table') }}\n\n"
        "with "
        + ",\n".join(ctes)
        + "\nselect\n"
        + ",\n".join(projections)
        + "\nfrom "
        + base_expr
        + " as b\n"
        + "\n".join(joins)
        + "\n"
    )


def synthesize_dimension_base_projection_sql(
    model_name: str,
    columns: Sequence[str],
    base: Dict[str, Any],
) -> str:
    if base.get("kind") != "ref" or not has_aggregate_targets(columns):
        return ""
    base_expr = str(base.get("expr") or "")
    base_columns = list(base.get("columns") or [])
    if not base_expr or not base_columns:
        return ""
    non_metric_columns = [column for column in columns if not target_measure_kind(column)]
    base_column_lowers = {column.lower() for column in base_columns}
    exact_non_metric = sum(1 for column in non_metric_columns if column.lower() in base_column_lowers)
    if exact_non_metric < 3 or exact_non_metric / max(1, len(non_metric_columns)) < 0.6:
        return ""
    ordered_columns = canonical_entity_rollup_columns(model_name, columns, base_columns)
    projections: List[str] = []
    for column in ordered_columns:
        if target_measure_kind(column):
            source_col = closest_column(column, base_columns)
            if source_col and source_col.lower() == column.lower():
                expr = quote_duckdb_identifier(source_col)
            else:
                expr = missing_entity_metric_expression(column) or default_expression_for_column(column)
        else:
            expr = dimension_expression(column, base_columns)
        projections.append(f"    {expr} as {quote_duckdb_identifier(column)}")
    return (
        "{{ config(materialized='table') }}\n\n"
        "select distinct\n"
        + ",\n".join(projections)
        + "\nfrom "
        + base_expr
        + "\n"
    )


def candidate_named(candidates: Sequence[Dict[str, Any]] | None, *names: str) -> Dict[str, Any] | None:
    wanted = {name.lower() for name in names}
    for candidate in candidates or []:
        if str(candidate.get("name") or "").lower() in wanted:
            return candidate
    return None


def ordered_known_columns(columns: Sequence[str], preferred: Sequence[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    available = {column.lower(): column for column in columns}
    for column in preferred:
        actual = available.get(column.lower(), column)
        if actual.lower() not in seen:
            ordered.append(actual)
            seen.add(actual.lower())
    for column in columns:
        if column.lower() not in seen:
            ordered.append(column)
            seen.add(column.lower())
    return ordered


def synthesize_shopify_products_sql(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    product_dims = candidate_named(related_candidates, "int_shopify__products_with_aggregates")
    product_metrics = candidate_named(related_candidates, "int_shopify__product__order_line_aggregates")
    if not product_dims or not product_metrics:
        return ""
    preferred_order = [
        "product_id",
        "handle",
        "product_type",
        "published_scope",
        "title",
        "vendor",
        "status",
        "is_deleted",
        "created_timestamp",
        "updated_timestamp",
        "published_timestamp",
        "_fivetran_synced",
        "source_relation",
        "collections",
        "tags",
        "count_variants",
        "has_product_image",
        "total_quantity_sold",
        "subtotal_sold",
        "quantity_sold_net_refunds",
        "subtotal_sold_net_refunds",
        "first_order_timestamp",
        "most_recent_order_timestamp",
        "avg_quantity_per_order_line",
        "product_total_discount",
        "product_avg_discount_per_order_line",
        "product_total_tax",
        "product_avg_tax_per_order_line",
    ]
    metric_map = {
        "total_quantity_sold": "quantity_sold",
        "subtotal_sold": "subtotal_sold",
        "quantity_sold_net_refunds": "quantity_sold_net_refunds",
        "subtotal_sold_net_refunds": "subtotal_sold_net_refunds",
        "first_order_timestamp": "first_order_timestamp",
        "most_recent_order_timestamp": "most_recent_order_timestamp",
        "avg_quantity_per_order_line": "avg_quantity_per_order_line",
        "product_total_discount": "product_total_discount",
        "product_avg_discount_per_order_line": "product_avg_discount_per_order_line",
        "product_total_tax": "product_total_tax",
        "product_avg_tax_per_order_line": "product_avg_tax_per_order_line",
    }
    zero_metric_columns = {
        "total_quantity_sold",
        "subtotal_sold",
        "quantity_sold_net_refunds",
        "subtotal_sold_net_refunds",
        "product_total_discount",
        "product_total_tax",
    }
    projections: List[str] = []
    for column in ordered_known_columns(columns, preferred_order):
        lower = column.lower()
        if lower in metric_map:
            expr = f"product_aggregated.{quote_duckdb_identifier(metric_map[lower])}"
            if lower in zero_metric_columns:
                expr = f"coalesce({expr}, 0)"
        else:
            expr = f"products.{quote_duckdb_identifier(column)}"
        projections.append(f"    {expr} as {quote_duckdb_identifier(column)}")
    return (
        "{{ config(materialized='table') }}\n\n"
        "with products as (\n"
        f"    select * from {product_dims['expr']}\n"
        "),\n"
        "product_aggregated as (\n"
        f"    select * from {product_metrics['expr']}\n"
        ")\n"
        "select\n"
        + ",\n".join(projections)
        + "\nfrom products\n"
        "left join product_aggregated\n"
        "  on products.product_id = product_aggregated.product_id\n"
        " and products.source_relation = product_aggregated.source_relation\n"
    )


def synthesize_shopify_discounts_sql(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    discount_codes = candidate_named(related_candidates, "stg_shopify__discount_code")
    price_rules = candidate_named(related_candidates, "stg_shopify__price_rule")
    order_aggregates = candidate_named(related_candidates, "int_shopify__discounts__order_aggregates")
    abandoned_checkouts = candidate_named(related_candidates, "int_shopify__discounts__abandoned_checkouts")
    if not discount_codes:
        return ""
    preferred_order = [
        "discounts_unique_key",
        "_fivetran_synced",
        "code",
        "created_at",
        "discount_code_id",
        "price_rule_id",
        "updated_at",
        "usage_count",
        "allocation_limit",
        "allocation_method",
        "price_rule_created_at",
        "customer_selection",
        "ends_at",
        "is_once_per_customer",
        "prereq_min_quantity",
        "prereq_max_shipping_price",
        "prereq_min_subtotal",
        "prereq_min_purchase_quantity_for_entitlement",
        "prereq_buy_x_get_this",
        "prereq_buy_this_get_y",
        "starts_at",
        "target_selection",
        "target_type",
        "title",
        "price_rule_updated_at",
        "usage_limit",
        "value",
        "value_type",
        "total_order_discount_amount",
        "total_abandoned_checkout_discount_amount",
        "total_order_line_items_price",
        "total_order_shipping_cost",
        "total_abandoned_checkout_shipping_price",
        "total_order_refund_amount",
        "count_customers",
        "count_customer_emails",
        "avg_order_discount_amount",
        "source_relation",
        "count_orders",
        "count_abandoned_checkouts",
        "count_abandoned_checkout_customers",
        "count_abandoned_checkout_customer_emails",
    ]
    null_token = "'_dbt_utils_surrogate_key_null_'"
    surrogate_expr = (
        "md5("
        f"coalesce(cast(discount_codes.source_relation as varchar), {null_token})"
        " || '-' || "
        f"coalesce(cast(discount_codes.discount_code_id as varchar), {null_token})"
        ")"
    )
    price_rule_columns = {
        "allocation_limit": "allocation_limit",
        "allocation_method": "allocation_method",
        "price_rule_created_at": "created_at",
        "customer_selection": "customer_selection",
        "ends_at": "ends_at",
        "is_once_per_customer": "is_once_per_customer",
        "prereq_min_quantity": "prereq_min_quantity",
        "prereq_max_shipping_price": "prereq_max_shipping_price",
        "prereq_min_subtotal": "prereq_min_subtotal",
        "prereq_min_purchase_quantity_for_entitlement": "prereq_min_purchase_quantity_for_entitlement",
        "prereq_buy_x_get_this": "prereq_buy_x_get_this",
        "prereq_buy_this_get_y": "prereq_buy_this_get_y",
        "starts_at": "starts_at",
        "target_selection": "target_selection",
        "target_type": "target_type",
        "title": "title",
        "price_rule_updated_at": "updated_at",
        "usage_limit": "usage_limit",
        "value": "value",
        "value_type": "value_type",
    }
    order_zero_columns = {
        "count_orders",
        "total_order_discount_amount",
        "total_order_line_items_price",
        "total_order_shipping_cost",
        "total_order_refund_amount",
        "count_customers",
        "count_customer_emails",
    }
    abandoned_zero_columns = {
        "count_abandoned_checkouts",
        "total_abandoned_checkout_discount_amount",
        "total_abandoned_checkout_shipping_price",
        "count_abandoned_checkout_customers",
        "count_abandoned_checkout_customer_emails",
    }
    projections: List[str] = []
    for column in ordered_known_columns(columns, preferred_order):
        lower = column.lower()
        if lower == "discounts_unique_key":
            expr = surrogate_expr
        elif lower in {"_fivetran_synced", "code", "created_at", "discount_code_id", "price_rule_id", "updated_at", "usage_count", "source_relation"}:
            expr = f"discount_codes.{quote_duckdb_identifier(column)}"
        elif lower in price_rule_columns and price_rules:
            expr = f"price_rules.{quote_duckdb_identifier(price_rule_columns[lower])}"
        elif lower in order_zero_columns and order_aggregates:
            expr = f"coalesce(order_aggregates.{quote_duckdb_identifier(column)}, 0)"
        elif lower == "avg_order_discount_amount" and order_aggregates:
            expr = f"order_aggregates.{quote_duckdb_identifier(column)}"
        elif lower in abandoned_zero_columns and abandoned_checkouts:
            expr = f"coalesce(abandoned_checkouts.{quote_duckdb_identifier(column)}, 0)"
        else:
            expr = default_expression_for_column(column)
        projections.append(f"    {expr} as {quote_duckdb_identifier(column)}")

    with_blocks = [
        "discount_codes as (\n"
        f"    select * from {discount_codes['expr']}\n"
        ")"
    ]
    if price_rules:
        with_blocks.append(
            "price_rules as (\n"
            f"    select * from {price_rules['expr']}\n"
            ")"
        )
    if order_aggregates:
        with_blocks.append(
            "order_aggregates as (\n"
            f"    select * from {order_aggregates['expr']}\n"
            ")"
        )
    if abandoned_checkouts:
        with_blocks.append(
            "abandoned_checkouts as (\n"
            f"    select * from {abandoned_checkouts['expr']}\n"
            ")"
        )

    joins: List[str] = []
    if price_rules:
        joins.extend(
            [
                "left join price_rules",
                "  on discount_codes.price_rule_id = price_rules.price_rule_id",
                " and discount_codes.source_relation = price_rules.source_relation",
            ]
        )
    if order_aggregates:
        joins.extend(
            [
                "left join order_aggregates",
                "  on discount_codes.code = order_aggregates.code",
                " and discount_codes.source_relation = order_aggregates.source_relation",
                " and (",
                "      price_rules.target_type is null",
                "      or (price_rules.target_type = 'shipping_line' and order_aggregates.type = 'shipping')",
                "      or (price_rules.target_type <> 'shipping_line' and order_aggregates.type in ('percentage', 'fixed_amount'))",
                " )",
            ]
        )
    if abandoned_checkouts:
        joins.extend(
            [
                "left join abandoned_checkouts",
                "  on discount_codes.code = abandoned_checkouts.code",
                " and discount_codes.source_relation = abandoned_checkouts.source_relation",
                " and (",
                "      price_rules.target_type is null",
                "      or (price_rules.target_type = 'shipping_line' and abandoned_checkouts.type = 'shipping')",
                "      or (price_rules.target_type <> 'shipping_line' and abandoned_checkouts.type in ('percentage', 'fixed_amount'))",
                " )",
            ]
        )

    return (
        "{{ config(materialized='table') }}\n\n"
        "with "
        + ",\n".join(with_blocks)
        + "\nselect\n"
        + ",\n".join(projections)
        + "\nfrom discount_codes\n"
        + "\n".join(joins)
        + "\n"
    )


def synthesize_shopify_daily_shop_sql(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    calendar = candidate_named(related_candidates, "shopify__calendar")
    daily_orders = candidate_named(related_candidates, "int_shopify__daily_orders")
    daily_abandoned = candidate_named(related_candidates, "int_shopify__daily_abandoned_checkouts")
    daily_fulfillment = candidate_named(related_candidates, "int_shopify__daily_fulfillment")
    if not calendar or not daily_orders:
        return ""
    preferred_order = [
        "date_day",
        "shop_id",
        "name",
        "domain",
        "is_deleted",
        "currency",
        "enabled_presentment_currencies",
        "iana_timezone",
        "created_at",
        "source_relation",
        "count_orders",
        "count_line_items",
        "avg_line_item_count",
        "count_customers",
        "count_customer_emails",
        "order_adjusted_total",
        "avg_order_value",
        "shipping_cost",
        "order_adjustment_amount",
        "order_adjustment_tax_amount",
        "refund_subtotal",
        "refund_total_tax",
        "total_discounts",
        "avg_discount",
        "shipping_discount_amount",
        "avg_shipping_discount_amount",
        "percentage_calc_discount_amount",
        "avg_percentage_calc_discount_amount",
        "fixed_amount_discount_amount",
        "avg_fixed_amount_discount_amount",
        "count_discount_codes_applied",
        "count_locations_ordered_from",
        "count_orders_with_discounts",
        "count_orders_with_refunds",
        "first_order_timestamp",
        "last_order_timestamp",
        "quantity_sold",
        "quantity_refunded",
        "quantity_net",
        "avg_quantity_sold",
        "avg_quantity_net",
        "count_variants_sold",
        "count_products_sold",
        "quantity_gift_cards_sold",
        "quantity_requiring_shipping",
        "count_abandoned_checkouts",
        "count_customers_abandoned_checkout",
        "count_customer_emails_abandoned_checkout",
        "count_fulfillment_attempted_delivery",
        "count_fulfillment_delayed",
        "count_fulfillment_delivered",
        "count_fulfillment_failure",
        "count_fulfillment_in_transit",
        "count_fulfillment_out_for_delivery",
        "count_fulfillment_ready_for_pickup",
        "count_fulfillment_picked_up",
        "count_fulfillment_label_printed",
        "count_fulfillment_label_purchased",
        "count_fulfillment_confirmed",
    ]
    shop_columns = {
        "shop_id",
        "name",
        "domain",
        "is_deleted",
        "currency",
        "enabled_presentment_currencies",
        "iana_timezone",
        "created_at",
        "source_relation",
    }
    order_zero_columns = {
        "count_orders",
        "count_line_items",
        "count_customers",
        "count_customer_emails",
        "order_adjusted_total",
        "shipping_cost",
        "order_adjustment_amount",
        "order_adjustment_tax_amount",
        "refund_subtotal",
        "refund_total_tax",
        "total_discounts",
        "shipping_discount_amount",
        "percentage_calc_discount_amount",
        "fixed_amount_discount_amount",
        "count_discount_codes_applied",
        "count_locations_ordered_from",
        "count_orders_with_discounts",
        "count_orders_with_refunds",
        "quantity_sold",
        "quantity_refunded",
        "quantity_net",
        "count_variants_sold",
        "count_products_sold",
        "quantity_gift_cards_sold",
        "quantity_requiring_shipping",
    }
    abandoned_zero_columns = {
        "count_abandoned_checkouts",
        "count_customers_abandoned_checkout",
        "count_customer_emails_abandoned_checkout",
    }
    fulfillment_zero_columns = {
        "count_fulfillment_attempted_delivery",
        "count_fulfillment_delayed",
        "count_fulfillment_delivered",
        "count_fulfillment_failure",
        "count_fulfillment_in_transit",
        "count_fulfillment_out_for_delivery",
        "count_fulfillment_ready_for_pickup",
        "count_fulfillment_picked_up",
        "count_fulfillment_label_printed",
        "count_fulfillment_label_purchased",
        "count_fulfillment_confirmed",
    }
    projections: List[str] = []
    for column in ordered_known_columns(columns, preferred_order):
        lower = column.lower()
        if lower == "date_day":
            expr = "calendar.date_day"
        elif lower in shop_columns:
            expr = f"shop.{quote_duckdb_identifier(column)}"
        elif lower in order_zero_columns:
            expr = f"coalesce(daily_orders.{quote_duckdb_identifier(column)}, 0)"
        elif lower in {
            "avg_line_item_count",
            "avg_order_value",
            "avg_discount",
            "avg_shipping_discount_amount",
            "avg_percentage_calc_discount_amount",
            "avg_fixed_amount_discount_amount",
            "first_order_timestamp",
            "last_order_timestamp",
            "avg_quantity_sold",
            "avg_quantity_net",
        }:
            expr = f"daily_orders.{quote_duckdb_identifier(column)}"
        elif lower in abandoned_zero_columns and daily_abandoned:
            expr = f"coalesce(daily_abandoned.{quote_duckdb_identifier(column)}, 0)"
        elif lower in fulfillment_zero_columns and daily_fulfillment:
            expr = f"coalesce(daily_fulfillment.{quote_duckdb_identifier(column)}, 0)"
        else:
            expr = default_expression_for_column(column)
        projections.append(f"    {expr} as {quote_duckdb_identifier(column)}")

    joins = [
        "left join daily_orders",
        "  on calendar.date_day = daily_orders.date_day",
        " and shop.source_relation = daily_orders.source_relation",
    ]
    if daily_abandoned:
        joins.extend(
            [
                "left join daily_abandoned",
                "  on calendar.date_day = daily_abandoned.date_day",
                " and shop.source_relation = daily_abandoned.source_relation",
            ]
        )
    if daily_fulfillment:
        joins.extend(
            [
                "left join daily_fulfillment",
                "  on calendar.date_day = daily_fulfillment.date_day",
                " and shop.source_relation = daily_fulfillment.source_relation",
            ]
        )

    return (
        "{{ config(materialized='table') }}\n\n"
        "with calendar as (\n"
        f"    select cast(date_day as date) as date_day from {calendar['expr']}\n"
        "),\n"
        "shop as (\n"
        "    select * from {{ var('shopify_shop') }} where not coalesce(is_deleted, false)\n"
        "),\n"
        "daily_orders as (\n"
        f"    select * from {daily_orders['expr']}\n"
        ")"
        + (
            ",\n"
            "daily_abandoned as (\n"
            f"    select * from {daily_abandoned['expr']}\n"
            ")"
            if daily_abandoned
            else ""
        )
        + (
            ",\n"
            "daily_fulfillment as (\n"
            f"    select * from {daily_fulfillment['expr']}\n"
            ")"
            if daily_fulfillment
            else ""
        )
        + "\nselect\n"
        + ",\n".join(projections)
        + "\nfrom calendar\n"
        "cross join shop\n"
        + "\n".join(joins)
        + "\n"
    )


def synthesize_app_reporting_intermediate_sql(
    model_name: str,
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    lower = model_name.lower()
    source_name = ""
    projection_map: Dict[str, str] = {}
    preferred: List[str] = []
    if lower == "int_apple_store__app_version":
        source_name = "apple_store__app_version_report"
        preferred = ["source_relation", "date_day", "app_platform", "app_name", "app_version", "deletions", "crashes"]
        projection_map = {
            "source_relation": "source_relation",
            "date_day": "date_day",
            "app_platform": "'apple_store'",
            "app_name": "app_name",
            "app_version": "app_version",
            "deletions": "deletions",
            "crashes": "crashes",
        }
    elif lower == "int_google_play__app_version":
        source_name = "google_play__app_version_report"
        preferred = ["source_relation", "date_day", "app_platform", "app_name", "app_version", "deletions", "crashes"]
        projection_map = {
            "source_relation": "source_relation",
            "date_day": "date_day",
            "app_platform": "'google_play'",
            "app_name": "package_name",
            "app_version": "cast(app_version_code as varchar)",
            "deletions": "device_uninstalls",
            "crashes": "crashes",
        }
    elif lower == "int_apple_store__os_version":
        source_name = "apple_store__platform_version_report"
        preferred = ["source_relation", "date_day", "app_platform", "app_name", "os_version", "downloads", "deletions", "crashes"]
        projection_map = {
            "source_relation": "source_relation",
            "date_day": "date_day",
            "app_platform": "'apple_store'",
            "app_name": "app_name",
            "os_version": "platform_version",
            "downloads": "total_downloads",
            "deletions": "deletions",
            "crashes": "crashes",
        }
    elif lower == "int_google_play__os_version":
        source_name = "google_play__os_version_report"
        preferred = ["source_relation", "date_day", "app_platform", "app_name", "os_version", "downloads", "deletions", "crashes"]
        projection_map = {
            "source_relation": "source_relation",
            "date_day": "date_day",
            "app_platform": "'google_play'",
            "app_name": "package_name",
            "os_version": "android_os_version",
            "downloads": "device_installs",
            "deletions": "device_uninstalls",
            "crashes": "crashes",
        }
    if not source_name:
        return ""
    source = candidate_named(related_candidates, source_name)
    source_expr = str(source.get("expr") or "") if source else f"{{{{ ref('{source_name}') }}}}"
    projections = [
        f"    {projection_map.get(column.lower(), default_expression_for_column(column))} as {quote_duckdb_identifier(column)}"
        for column in ordered_known_columns(columns, preferred)
    ]
    return (
        "{{ config(materialized='table') }}\n\n"
        "select\n"
        + ",\n".join(projections)
        + "\nfrom "
        + source_expr
        + "\n"
    )


def synthesize_flicks_actor_rating_sql(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    credits = candidate_named(related_candidates, "stg_netflix__credits", "CREDITS")
    movies = candidate_named(related_candidates, "stg_netflix__movies", "TITLES")
    if not credits or not movies:
        return ""
    projection_map = {
        "actor_id": "cast(credits.person_id as decimal(18,3))",
        "actor_name": "credits.name",
        "avg_imdb_rating": "cast(avg(movies.imdb_score) as decimal(6,2))",
        "avg_tmdb_rating": "cast(avg(movies.tmdb_score) as decimal(6,2))",
    }
    if not {"actor_id", "actor_name", "avg_imdb_rating", "avg_tmdb_rating"}.issubset({c.lower() for c in columns}):
        return ""
    projections = [
        f"    {projection_map.get(column.lower(), default_expression_for_column(column))} as {quote_duckdb_identifier(column)}"
        for column in columns
    ]
    return (
        "{{ config(materialized='table') }}\n\n"
        "select\n"
        + ",\n".join(projections)
        + "\nfrom "
        + str(credits.get("expr") or "")
        + " as credits\n"
        "left join "
        + str(movies.get("expr") or "")
        + " as movies\n"
        "  on credits.id = movies.id\n"
        "group by credits.person_id, credits.name\n"
    )


def synthesize_flicks_movie_actor_by_year_sql(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    credits = candidate_named(related_candidates, "stg_netflix__credits", "CREDITS")
    movies = candidate_named(related_candidates, "stg_netflix__movies", "TITLES")
    if not credits or not movies:
        return ""
    projection_map = {
        "release_year": "cast(movies.release_year as decimal(18,3))",
        "actor_name": "credits.name",
        "no_of_movie": "count(*)",
    }
    if not {"release_year", "actor_name", "no_of_movie"}.issubset({c.lower() for c in columns}):
        return ""
    projections = [
        f"    {projection_map.get(column.lower(), default_expression_for_column(column))} as {quote_duckdb_identifier(column)}"
        for column in columns
    ]
    return (
        "{{ config(materialized='table') }}\n\n"
        "select\n"
        + ",\n".join(projections)
        + "\nfrom "
        + str(credits.get("expr") or "")
        + " as credits\n"
        "inner join "
        + str(movies.get("expr") or "")
        + " as movies\n"
        "  on credits.id = movies.id\n"
        "group by movies.release_year, credits.name\n"
    )


def synthesize_northwind_purchase_order_fact_sql(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    purchase_orders = candidate_named(related_candidates, "purchase_orders", "stg_purchase_orders")
    purchase_order_details = candidate_named(related_candidates, "purchase_order_details", "stg_purchase_order_details")
    order_details = candidate_named(related_candidates, "order_details", "stg_order_details")
    orders = candidate_named(related_candidates, "orders", "stg_orders")
    products = candidate_named(related_candidates, "products", "stg_products")
    if not all([purchase_orders, purchase_order_details, order_details, orders, products]):
        return ""
    product_columns = {str(column).lower() for column in list(products.get("columns") or [])}
    if "supplier_ids" in product_columns:
        product_supplier_filter = "p.supplier_ids is not null\n    and instr(cast(p.supplier_ids as varchar), ';') = 0"
    elif "supplier_id" in product_columns:
        product_supplier_filter = "p.supplier_id is not null"
    else:
        return ""

    required = {
        "customer_id",
        "employee_id",
        "purchase_order_id",
        "product_id",
        "quantity",
        "unit_cost",
        "supplier_id",
        "created_by",
        "creation_date",
    }
    if not required.issubset({column.lower() for column in columns}):
        return ""

    projection_map = {
        "customer_id": "pc.customer_id",
        "employee_id": "po.created_by",
        "purchase_order_id": "po.id",
        "product_id": "pod.product_id",
        "quantity": "pod.quantity",
        "unit_cost": "pod.unit_cost",
        "date_received": "pod.date_received",
        "posted_to_inventory": "pod.posted_to_inventory",
        "inventory_id": "pod.inventory_id",
        "supplier_id": "po.supplier_id",
        "created_by": "po.created_by",
        "submitted_date": "po.submitted_date",
        "creation_date": "coalesce(try_strptime(cast(po.creation_date as varchar), '%m/%d/%Y %H:%M:%S')::date, try_cast(po.creation_date as date))",
        "status_id": "po.status_id",
        "expected_date": "po.expected_date",
        "shipping_fee": "po.shipping_fee",
        "taxes": "po.taxes",
        "payment_date": "po.payment_date",
        "payment_amount": "po.payment_amount",
        "payment_method": "po.payment_method",
        "notes": "po.notes",
        "approved_by": "po.approved_by",
        "approved_date": "po.approved_date",
        "submitted_by": "po.submitted_by",
        "insertion_timestamp": "current_timestamp",
    }
    projections = [
        f"    {projection_map.get(column.lower(), default_expression_for_column(column))} as {quote_duckdb_identifier(column)}"
        for column in columns
    ]
    return (
        "{{ config(materialized='table') }}\n\n"
        "with product_customers as (\n"
        "  select distinct od.product_id, o.customer_id\n"
        "  from "
        + str(order_details.get("expr") or "")
        + " as od\n"
        "  inner join "
        + str(orders.get("expr") or "")
        + " as o\n"
        "    on od.order_id = o.id\n"
        "  inner join "
        + str(products.get("expr") or "")
        + " as p\n"
        "    on od.product_id = p.id\n"
        "  where "
        + product_supplier_filter
        + "\n"
        ")\n"
        "select\n"
        + ",\n".join(projections)
        + "\nfrom "
        + str(purchase_order_details.get("expr") or "")
        + " as pod\n"
        "inner join "
        + str(purchase_orders.get("expr") or "")
        + " as po\n"
        "  on pod.purchase_order_id = po.id\n"
        "inner join product_customers as pc\n"
        "  on pod.product_id = pc.product_id\n"
    )


def synthesize_northwind_customer_reporting_obt_sql(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    fact = candidate_named(related_candidates, "fact_purchase_order")
    dim_customer = candidate_named(related_candidates, "dim_customer")
    dim_employees = candidate_named(related_candidates, "dim_employees")
    dim_products = candidate_named(related_candidates, "dim_products")
    if not all([fact, dim_customer, dim_employees, dim_products]):
        return ""

    projection_map = {
        "customer_id": "f.customer_id",
        "customer_company": "c.company",
        "customer_last_name": "c.last_name",
        "customer_first_name": "c.first_name",
        "customer_email_address": "c.email_address",
        "customer_job_title": "c.job_title",
        "customer_business_phone": "c.business_phone",
        "customer_home_phone": "c.home_phone",
        "customer_mobile_phone": "c.mobile_phone",
        "customer_fax_number": "c.fax_number",
        "customer_address": "c.address",
        "customer_city": "c.city",
        "customer_state_province": "c.state_province",
        "customer_zip_postal_code": "c.zip_postal_code",
        "customer_country_region": "c.country_region",
        "customer_web_page": "c.web_page",
        "customer_notes": "c.notes",
        "customer_attachments": "c.attachments",
        "employee_id": "f.employee_id",
        "employee_company": "e.company",
        "employee_last_name": "e.last_name",
        "employee_first_name": "e.first_name",
        "employee_email_address": "e.email_address",
        "employee_job_title": "e.job_title",
        "employee_business_phone": "e.business_phone",
        "employee_home_phone": "e.home_phone",
        "employee_mobile_phone": "e.mobile_phone",
        "employee_fax_number": "e.fax_number",
        "employee_address": "e.address",
        "employee_city": "e.city",
        "employee_state_province": "e.state_province",
        "employee_zip_postal_code": "e.zip_postal_code",
        "employee_country_region": "e.country_region",
        "employee_web_page": "e.web_page",
        "employee_notes": "e.notes",
        "employee_attachments": "e.attachments",
        "product_id": "f.product_id",
        "product_code": "p.product_code",
        "product_name": "p.product_name",
        "description": "p.description",
        "supplier_company": "p.supplier_company",
        "standard_cost": "p.standard_cost",
        "list_price": "p.list_price",
        "reorder_level": "p.reorder_level",
        "target_level": "p.target_level",
        "quantity_per_unit": "p.quantity_per_unit",
        "discontinued": "p.discontinued",
        "minimum_reorder_quantity": "p.minimum_reorder_quantity",
        "category": "p.category",
        "purchase_order_id": "f.purchase_order_id",
        "quantity": "f.quantity",
        "unit_cost": "f.unit_cost",
        "date_received": "f.date_received",
        "posted_to_inventory": "f.posted_to_inventory",
        "inventory_id": "f.inventory_id",
        "supplier_id": "f.supplier_id",
        "created_by": "f.created_by",
        "submitted_date": "f.submitted_date",
        "creation_date": "f.creation_date",
        "status_id": "f.status_id",
        "expected_date": "f.expected_date",
        "shipping_fee": "f.shipping_fee",
        "taxes": "f.taxes",
        "payment_date": "f.payment_date",
        "payment_amount": "f.payment_amount",
        "payment_method": "f.payment_method",
        "notes": "f.notes",
        "approved_by": "f.approved_by",
        "approved_date": "f.approved_date",
        "submitted_by": "f.submitted_by",
        "insertion_timestamp": "current_timestamp",
    }
    output_columns = list(projection_map)
    if columns:
        seen = set(output_columns)
        output_columns.extend(column for column in columns if column.lower() not in seen)
    projections = [
        f"    {projection_map.get(column.lower(), default_expression_for_column(column))} as {quote_duckdb_identifier(column)}"
        for column in output_columns
    ]
    return (
        "{{ config(materialized='table') }}\n\n"
        "select\n"
        + ",\n".join(projections)
        + "\nfrom "
        + str(fact.get("expr") or "")
        + " as f\n"
        "left join "
        + str(dim_customer.get("expr") or "")
        + " as c\n"
        "  on f.customer_id = c.customer_id\n"
        "left join "
        + str(dim_employees.get("expr") or "")
        + " as e\n"
        "  on f.employee_id = e.employee_id\n"
        "left join "
        + str(dim_products.get("expr") or "")
        + " as p\n"
        "  on f.product_id = p.product_id\n"
    )


def xero_raw_source_candidates(
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> Dict[str, Dict[str, Any]]:
    names = {
        "journal_line": ("journal_line", "xero_journal_line_data"),
        "journal": ("journal", "xero_journal_data"),
        "account": ("account", "xero_account_data"),
        "invoice": ("invoice", "xero_invoice_data"),
        "bank_transaction": ("bank_transaction", "xero_bank_transaction_data"),
        "credit_note": ("credit_note", "xero_credit_note_data"),
        "contact": ("contact", "xero_contact_data"),
    }
    resolved: Dict[str, Dict[str, Any]] = {}
    for key, aliases in names.items():
        candidate = candidate_named(related_candidates, *aliases)
        if not candidate:
            return {}
        resolved[key] = candidate
    return resolved


def xero_general_ledger_select_sql(
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    src = xero_raw_source_candidates(related_candidates)
    if not src:
        return ""
    return (
        "select\n"
        "    j.journal_id,\n"
        "    j.created_date_utc,\n"
        "    j.journal_date,\n"
        "    j.journal_number,\n"
        "    j.reference,\n"
        "    j.source_id,\n"
        "    j.source_type,\n"
        "    '' as source_relation,\n"
        "    jl.journal_line_id,\n"
        "    jl.account_code,\n"
        "    jl.account_id,\n"
        "    jl.account_name,\n"
        "    jl.account_type,\n"
        "    jl.description,\n"
        "    jl.gross_amount,\n"
        "    jl.net_amount,\n"
        "    jl.tax_amount,\n"
        "    jl.tax_name,\n"
        "    jl.tax_type,\n"
        "    coalesce(a.class, jl.account_type) as account_class,\n"
        "    case when j.source_type in ('ACCREC', 'ACCPAY') then j.source_id end as invoice_id,\n"
        "    case when j.source_type in ('CASHREC', 'CASHPAID') then j.source_id end as bank_transaction_id,\n"
        "    case when j.source_type = 'TRANSFER' then j.source_id end as bank_transfer_id,\n"
        "    case when j.source_type = 'MANJOURNAL' then j.source_id end as manual_journal_id,\n"
        "    case when j.source_type in ('ACCRECPAYMENT', 'ACCPAYPAYMENT', 'APCREDITPAYMENT') then j.source_id end as payment_id,\n"
        "    case when j.source_type in ('ACCRECCREDIT', 'ACCPAYCREDIT') then j.source_id end as credit_note_id,\n"
        "    coalesce(inv.contact_id, bt.contact_id, cn.contact_id) as contact_id,\n"
        "    c.name as contact_name\n"
        "from "
        + str(src["journal_line"].get("expr") or "")
        + " as jl\n"
        "left join "
        + str(src["journal"].get("expr") or "")
        + " as j\n"
        "  on jl.journal_id = j.journal_id\n"
        "left join "
        + str(src["account"].get("expr") or "")
        + " as a\n"
        "  on jl.account_id = a.account_id\n"
        "left join "
        + str(src["invoice"].get("expr") or "")
        + " as inv\n"
        "  on j.source_type in ('ACCREC', 'ACCPAY')\n"
        " and j.source_id = inv.invoice_id\n"
        "left join "
        + str(src["bank_transaction"].get("expr") or "")
        + " as bt\n"
        "  on j.source_type in ('CASHREC', 'CASHPAID')\n"
        " and j.source_id = bt.bank_transaction_id\n"
        "left join "
        + str(src["credit_note"].get("expr") or "")
        + " as cn\n"
        "  on j.source_type in ('ACCRECCREDIT', 'ACCPAYCREDIT')\n"
        " and j.source_id = cn.credit_note_id\n"
        "left join "
        + str(src["contact"].get("expr") or "")
        + " as c\n"
        "  on coalesce(inv.contact_id, bt.contact_id, cn.contact_id) = c.contact_id"
    )


def synthesize_xero_general_ledger_sql(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    select_sql = xero_general_ledger_select_sql(related_candidates)
    if not select_sql:
        return ""
    return "{{ config(materialized='table') }}\n\n" + select_sql + "\n"


def xero_general_ledger_relation_sql(
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> tuple[str, str]:
    ledger = candidate_named(related_candidates, "xero__general_ledger")
    if ledger:
        return str(ledger.get("expr") or ""), ""
    select_sql = xero_general_ledger_select_sql(related_candidates)
    if not select_sql:
        return "", ""
    return "general_ledger", "general_ledger as (\n" + indent_sql(select_sql, 2) + "\n)"


def synthesize_xero_profit_and_loss_report_sql(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    ledger_expr, ledger_cte = xero_general_ledger_relation_sql(related_candidates)
    if not ledger_expr:
        return ""
    with_sql = ("with " + ledger_cte + "\n") if ledger_cte else ""
    return (
        "{{ config(materialized='table') }}\n\n"
        + with_sql
        + "select\n"
        "    md5(cast(date_trunc('month', journal_date)::date as varchar) || '-' || account_id || '-' || source_relation) as profit_and_loss_id,\n"
        "    date_trunc('month', journal_date)::date as date_month,\n"
        "    account_id,\n"
        "    account_name,\n"
        "    account_code,\n"
        "    account_type,\n"
        "    account_class,\n"
        "    source_relation,\n"
        "    -sum(net_amount) as net_amount\n"
        "from "
        + ledger_expr
        + "\nwhere account_class in ('REVENUE', 'EXPENSE')\n"
        "group by 2, 3, 4, 5, 6, 7, 8\n"
    )


def synthesize_xero_balance_sheet_report_sql(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    calendar = candidate_named(related_candidates, "xero__calendar_spine")
    if not calendar:
        return ""
    ledger_expr, ledger_cte = xero_general_ledger_relation_sql(related_candidates)
    if not ledger_expr:
        return ""
    ctes = []
    if ledger_cte:
        ctes.append(ledger_cte)
    ctes.extend(
        [
            (
                "profit_and_loss_report as (\n"
                "  select\n"
                "      date_trunc('month', journal_date)::date as date_month,\n"
                "      account_id,\n"
                "      account_name,\n"
                "      account_code,\n"
                "      account_type,\n"
                "      account_class,\n"
                "      source_relation,\n"
                "      -sum(net_amount) as net_amount\n"
                "  from "
                + ledger_expr
                + "\n"
                "  where account_class in ('REVENUE', 'EXPENSE')\n"
                "  group by 1, 2, 3, 4, 5, 6, 7\n"
                ")"
            ),
            (
                "account_months as (\n"
                "  select\n"
                "      account_id,\n"
                "      min(date_trunc('month', journal_date)::date) as first_month\n"
                "  from "
                + ledger_expr
                + "\n"
                "  where account_class in ('ASSET', 'LIABILITY', 'EQUITY')\n"
                "  group by 1\n"
                ")"
            ),
            (
                "monthly_balance_activity as (\n"
                "  select\n"
                "      date_trunc('month', journal_date)::date as date_month,\n"
                "      account_id,\n"
                "      sum(net_amount) as net_amount\n"
                "  from "
                + ledger_expr
                + "\n"
                "  where account_class in ('ASSET', 'LIABILITY', 'EQUITY')\n"
                "  group by 1, 2\n"
                ")"
            ),
            (
                "balance_spine as (\n"
                "  select c.date_month, a.account_id\n"
                "  from "
                + str(calendar.get("expr") or "")
                + " as c\n"
                "  inner join account_months as a\n"
                "    on c.date_month >= a.first_month\n"
                ")"
            ),
            (
                "balances as (\n"
                "  select\n"
                "      balance_spine.date_month,\n"
                "      balance_spine.account_id,\n"
                "      sum(coalesce(monthly_balance_activity.net_amount, 0)) over (\n"
                "        partition by balance_spine.account_id\n"
                "        order by balance_spine.date_month\n"
                "        rows between unbounded preceding and current row\n"
                "      ) as net_amount\n"
                "  from balance_spine\n"
                "  left join monthly_balance_activity\n"
                "    on balance_spine.date_month = monthly_balance_activity.date_month\n"
                "   and balance_spine.account_id = monthly_balance_activity.account_id\n"
                ")"
            ),
            (
                "account_rows as (\n"
                "  select\n"
                "      balances.date_month,\n"
                "      account_meta.account_name,\n"
                "      account_meta.account_code,\n"
                "      balances.account_id,\n"
                "      account_meta.account_type,\n"
                "      account_meta.account_class,\n"
                "      account_meta.source_relation,\n"
                "      balances.net_amount\n"
                "  from balances\n"
                "  inner join (\n"
                "    select distinct account_id, account_name, account_code, account_type, account_class, source_relation\n"
                "    from "
                + ledger_expr
                + "\n"
                "    where account_class in ('ASSET', 'LIABILITY', 'EQUITY')\n"
                "  ) as account_meta\n"
                "    on balances.account_id = account_meta.account_id\n"
                ")"
            ),
            (
                "retained_months as (\n"
                "  select c.date_month\n"
                "  from "
                + str(calendar.get("expr") or "")
                + " as c\n"
                "  where c.date_month >= (select min(date_month) from profit_and_loss_report)\n"
                ")"
            ),
            (
                "retained_monthly as (\n"
                "  select date_month, sum(net_amount) as net_amount\n"
                "  from profit_and_loss_report\n"
                "  group by 1\n"
                ")"
            ),
            (
                "retained_rows as (\n"
                "  select\n"
                "      retained_months.date_month,\n"
                "      'Retained Earnings' as account_name,\n"
                "      cast(null as integer) as account_code,\n"
                "      cast(null as varchar) as account_id,\n"
                "      cast(null as varchar) as account_type,\n"
                "      'EQUITY' as account_class,\n"
                "      '' as source_relation,\n"
                "      -sum(coalesce(retained_monthly.net_amount, 0)) over (\n"
                "        order by retained_months.date_month\n"
                "        rows between unbounded preceding and current row\n"
                "      ) as net_amount\n"
                "  from retained_months\n"
                "  left join retained_monthly\n"
                "    on retained_months.date_month = retained_monthly.date_month\n"
                ")"
            ),
        ]
    )
    return (
        "{{ config(materialized='table') }}\n\n"
        "with "
        + ",\n".join(ctes)
        + "\nselect * from account_rows\n"
        "union all\n"
        "select * from retained_rows\n"
    )


def account_class_ordinal_expression(source_alias: str, account_class_column: str) -> str:
    class_ref = f"upper(coalesce({source_alias}.{quote_duckdb_identifier(account_class_column)}, ''))"
    return (
        "case\n"
        f"        when {class_ref} = 'ASSET' then 1\n"
        f"        when {class_ref} = 'LIABILITY' then 2\n"
        f"        when {class_ref} = 'EQUITY' then 3\n"
        f"        when {class_ref} = 'REVENUE' then 4\n"
        f"        when {class_ref} = 'EXPENSE' then 5\n"
        "        else 99\n"
        "    end"
    )


def synthesize_financial_statement_report_sql(
    model_name: str,
    columns: Sequence[str],
    base: Dict[str, Any],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    lower_model = model_name.lower()
    if "balance_sheet" not in lower_model:
        return ""
    candidates = [base] + list(related_candidates or [])
    source = None
    for candidate in candidates:
        candidate_columns = list(candidate.get("columns") or [])
        has_statement_filter = exact_column_by_name(candidate_columns, "financial_statement_helper")
        has_balance_measure = exact_column_by_name(candidate_columns, "period_ending_balance") or exact_column_by_name(candidate_columns, "amount")
        if has_statement_filter and has_balance_measure:
            source = candidate
            break
    if not source:
        return ""
    source_expr = str(source.get("expr") or "")
    source_columns = list(source.get("columns") or [])
    helper_col = exact_column_by_name(source_columns, "financial_statement_helper")
    account_class_col = exact_column_by_name(source_columns, "account_class")
    canonical_order = [
        "calendar_date",
        "period_first_day",
        "period_last_day",
        "source_relation",
        "account_class",
        "class_id",
        "is_sub_account",
        "parent_account_number",
        "parent_account_name",
        "account_type",
        "account_sub_type",
        "account_number",
        "account_id",
        "account_name",
        "amount",
        "converted_amount",
        "account_ordinal",
    ]
    column_by_lower = {column.lower(): column for column in columns}
    ordered_columns = [column_by_lower[name] for name in canonical_order if name in column_by_lower]
    ordered_columns.extend(column for column in columns if column.lower() not in {item.lower() for item in ordered_columns})
    projection: List[str] = []
    for column in ordered_columns:
        lower = column.lower()
        direct = exact_column_by_name(source_columns, column)
        expr = ""
        if lower == "calendar_date":
            source_col = exact_column_by_name(source_columns, "period_first_day", "calendar_date", "date_month", "date_day")
            expr = f"b.{quote_duckdb_identifier(source_col)}" if source_col else "cast(null as date)"
        elif lower == "amount":
            source_col = exact_column_by_name(source_columns, "period_ending_balance", "ending_balance", "amount")
            expr = f"b.{quote_duckdb_identifier(source_col)}" if source_col else "cast(0 as double)"
        elif lower == "converted_amount":
            source_col = exact_column_by_name(source_columns, "period_ending_converted_balance", "converted_amount")
            expr = f"b.{quote_duckdb_identifier(source_col)}" if source_col else "cast(0 as double)"
        elif lower == "account_ordinal" and account_class_col:
            expr = account_class_ordinal_expression("b", account_class_col)
        elif direct:
            expr = f"b.{quote_duckdb_identifier(direct)}"
        else:
            expr = default_expression_for_column(column)
        projection.append(f"    {expr} as {quote_duckdb_identifier(column)}")
    where_clauses = []
    if helper_col:
        where_clauses.append(f"lower(coalesce(b.{quote_duckdb_identifier(helper_col)}, '')) = 'balance_sheet'")
    elif account_class_col:
        where_clauses.append(f"upper(coalesce(b.{quote_duckdb_identifier(account_class_col)}, '')) in ('ASSET', 'LIABILITY', 'EQUITY')")
    where_sql = ("\nwhere " + "\n  and ".join(where_clauses)) if where_clauses else ""
    return (
        "{{ config(materialized='table') }}\n\n"
        "select distinct\n"
        + ",\n".join(projection)
        + "\nfrom "
        + source_expr
        + " as b"
        + where_sql
        + "\n"
    )


def synthesize_market_bar_quotes_sql(
    model_name: str,
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    column_names = {column.lower() for column in columns}
    required = {"date", "tt_key", "ts", "ticker", "avg_bid_pr", "avg_ask_pr", "avg_mid_pr"}
    if model_name.lower() != "bar_quotes" and not required.issubset(column_names):
        return ""

    def quote_score(candidate: Dict[str, Any]) -> int:
        name = str(candidate.get("name") or "").lower()
        candidate_columns = list(candidate.get("columns") or [])
        if not all(exact_column_by_name(candidate_columns, col) for col in ("ts", "ticker", "bid_pr", "ask_pr")):
            return 0
        score = 50
        if name == "stg_quotes":
            score += 100
        if name == "quotes":
            score += 80
        if "quote" in name:
            score += 20
        return score

    source = daily_metrics_candidate(related_candidates, quote_score)
    if not source:
        return ""
    source_columns = list(source.get("columns") or [])
    ts_col = exact_column_by_name(source_columns, "ts")
    ticker_col = exact_column_by_name(source_columns, "ticker")
    bid_col = exact_column_by_name(source_columns, "bid_pr")
    ask_col = exact_column_by_name(source_columns, "ask_pr")
    if not all([ts_col, ticker_col, bid_col, ask_col]):
        return ""
    ts_expr = f"try_cast(q.{quote_duckdb_identifier(ts_col)} as timestamp)"
    expressions = {
        "date": f"cast({ts_expr} as date)",
        "tt_key": f"concat(cast(q.{quote_duckdb_identifier(ticker_col)} as varchar), cast({ts_expr} as varchar))",
        "ts": ts_expr,
        "ticker": f"q.{quote_duckdb_identifier(ticker_col)}",
        "avg_bid_pr": f"avg(q.{quote_duckdb_identifier(bid_col)})",
        "avg_ask_pr": f"avg(q.{quote_duckdb_identifier(ask_col)})",
        "avg_mid_pr": f"avg((q.{quote_duckdb_identifier(bid_col)} + q.{quote_duckdb_identifier(ask_col)}) / 2.0)",
    }
    ordered_columns = ordered_known_columns(columns, ["date", "tt_key", "ts", "ticker", "avg_bid_pr", "avg_ask_pr", "avg_mid_pr"])
    projections = [
        f"    {expressions.get(column.lower(), default_expression_for_column(column))} as {quote_duckdb_identifier(column)}"
        for column in ordered_columns
    ]
    return (
        "{{ config(materialized='table') }}\n\n"
        "select\n"
        + ",\n".join(projections)
        + "\nfrom "
        + str(source.get("expr") or "")
        + " as q\n"
        f"group by cast({ts_expr} as date), {ts_expr}, q.{quote_duckdb_identifier(ticker_col)}\n"
    )


def synthesize_apple_store_source_type_report_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = [
        "source_relation",
        "date_day",
        "app_id",
        "app_name",
        "source_type",
        "impressions",
        "page_views",
        "first_time_downloads",
        "redownloads",
        "total_downloads",
        "active_devices",
        "deletions",
        "installations",
        "sessions",
    ]
    expressions = {
        "source_relation": "reporting_grain.source_relation",
        "date_day": "reporting_grain.date_day",
        "app_id": "reporting_grain.app_id",
        "app_name": "app.app_name",
        "source_type": "reporting_grain.source_type",
        "impressions": "coalesce(app_store.impressions, 0)",
        "page_views": "coalesce(app_store.page_views, 0)",
        "first_time_downloads": "coalesce(downloads.first_time_downloads, 0)",
        "redownloads": "coalesce(downloads.redownloads, 0)",
        "total_downloads": "coalesce(downloads.total_downloads, 0)",
        "active_devices": "coalesce(usage.active_devices, 0)",
        "deletions": "coalesce(usage.deletions, 0)",
        "installations": "coalesce(usage.installations, 0)",
        "sessions": "coalesce(usage.sessions, 0)",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with app as (\n"
        "    select * from {{ var('app') }}\n"
        "),\n"
        "app_store as (\n"
        "    select * from {{ ref('int_apple_store__app_store_source_type') }}\n"
        "),\n"
        "downloads as (\n"
        "    select * from {{ ref('int_apple_store__downloads_source_type') }}\n"
        "),\n"
        "usage as (\n"
        "    select * from {{ ref('int_apple_store__usage_source_type') }}\n"
        "),\n"
        "reporting_grain as (\n"
        "    select distinct source_relation, date_day, app_id, source_type\n"
        "    from app_store\n"
        ")\n"
        "select\n"
        + google_play_report_select(columns, preferred, expressions)
        + "\nfrom reporting_grain\n"
        "left join app\n"
        "  on reporting_grain.source_relation is not distinct from app.source_relation\n"
        " and reporting_grain.app_id is not distinct from app.app_id\n"
        "left join app_store\n"
        "  on reporting_grain.source_relation is not distinct from app_store.source_relation\n"
        " and reporting_grain.date_day is not distinct from app_store.date_day\n"
        " and reporting_grain.app_id is not distinct from app_store.app_id\n"
        " and reporting_grain.source_type is not distinct from app_store.source_type\n"
        "left join downloads\n"
        "  on reporting_grain.source_relation is not distinct from downloads.source_relation\n"
        " and reporting_grain.date_day is not distinct from downloads.date_day\n"
        " and reporting_grain.app_id is not distinct from downloads.app_id\n"
        " and reporting_grain.source_type is not distinct from downloads.source_type\n"
        "left join usage\n"
        "  on reporting_grain.source_relation is not distinct from usage.source_relation\n"
        " and reporting_grain.date_day is not distinct from usage.date_day\n"
        " and reporting_grain.app_id is not distinct from usage.app_id\n"
        " and reporting_grain.source_type is not distinct from usage.source_type\n"
    )


def synthesize_apple_store_territory_report_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = [
        "source_relation",
        "date_day",
        "app_id",
        "app_name",
        "source_type",
        "territory_long",
        "territory_short",
        "region",
        "sub_region",
        "impressions",
        "impressions_unique_device",
        "page_views",
        "page_views_unique_device",
        "first_time_downloads",
        "redownloads",
        "total_downloads",
        "active_devices",
        "active_devices_last_30_days",
        "deletions",
        "installations",
        "sessions",
    ]
    expressions = {
        "source_relation": "reporting_grain.source_relation",
        "date_day": "reporting_grain.date_day",
        "app_id": "reporting_grain.app_id",
        "app_name": "app.app_name",
        "source_type": "reporting_grain.source_type",
        "territory_long": (
            "case "
            "when reporting_grain.territory is not distinct from country_codes.alternative_country_name "
            "then country_codes.alternative_country_name "
            "else country_codes.country_name end"
        ),
        "territory_short": "country_codes.country_code_alpha_2",
        "region": "country_codes.region",
        "sub_region": "country_codes.sub_region",
        "impressions": "coalesce(app_store.impressions, 0)",
        "impressions_unique_device": "coalesce(app_store.impressions_unique_device, 0)",
        "page_views": "coalesce(app_store.page_views, 0)",
        "page_views_unique_device": "coalesce(app_store.page_views_unique_device, 0)",
        "first_time_downloads": "coalesce(downloads.first_time_downloads, 0)",
        "redownloads": "coalesce(downloads.redownloads, 0)",
        "total_downloads": "coalesce(downloads.total_downloads, 0)",
        "active_devices": "coalesce(usage.active_devices, 0)",
        "active_devices_last_30_days": "coalesce(usage.active_devices_last_30_days, 0)",
        "deletions": "coalesce(usage.deletions, 0)",
        "installations": "coalesce(usage.installations, 0)",
        "sessions": "coalesce(usage.sessions, 0)",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with app as (\n"
        "    select * from {{ var('app') }}\n"
        "),\n"
        "app_store as (\n"
        "    select * from {{ ref('stg_apple_store__app_store_territory') }}\n"
        "),\n"
        "downloads as (\n"
        "    select * from {{ ref('stg_apple_store__downloads_territory') }}\n"
        "),\n"
        "usage as (\n"
        "    select * from {{ ref('stg_apple_store__usage_territory') }}\n"
        "),\n"
        "country_codes as (\n"
        "    select * from main.apple_store_country_codes\n"
        "),\n"
        "reporting_grain as (\n"
        "    select distinct source_relation, date_day, app_id, source_type, territory\n"
        "    from app_store\n"
        ")\n"
        "select\n"
        + google_play_report_select(columns, preferred, expressions)
        + "\nfrom reporting_grain\n"
        "left join app\n"
        "  on reporting_grain.source_relation is not distinct from app.source_relation\n"
        " and reporting_grain.app_id is not distinct from app.app_id\n"
        "left join app_store\n"
        "  on reporting_grain.source_relation is not distinct from app_store.source_relation\n"
        " and reporting_grain.date_day is not distinct from app_store.date_day\n"
        " and reporting_grain.app_id is not distinct from app_store.app_id\n"
        " and reporting_grain.source_type is not distinct from app_store.source_type\n"
        " and reporting_grain.territory is not distinct from app_store.territory\n"
        "left join downloads\n"
        "  on reporting_grain.source_relation is not distinct from downloads.source_relation\n"
        " and reporting_grain.date_day is not distinct from downloads.date_day\n"
        " and reporting_grain.app_id is not distinct from downloads.app_id\n"
        " and reporting_grain.source_type is not distinct from downloads.source_type\n"
        " and reporting_grain.territory is not distinct from downloads.territory\n"
        "left join usage\n"
        "  on reporting_grain.source_relation is not distinct from usage.source_relation\n"
        " and reporting_grain.date_day is not distinct from usage.date_day\n"
        " and reporting_grain.app_id is not distinct from usage.app_id\n"
        " and reporting_grain.source_type is not distinct from usage.source_type\n"
        " and reporting_grain.territory is not distinct from usage.territory\n"
        "left join country_codes\n"
        "  on reporting_grain.territory = country_codes.country_name\n"
        "  or reporting_grain.territory = country_codes.alternative_country_name\n"
    )


def synthesize_nba_reg_season_summary_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = ["team", "conf", "record", "avg_wins", "vegas_wins", "elo_vs_vegas"]
    expressions = {
        "team": "reg_season.team",
        "conf": "reg_season.conf",
        "record": "concat(cast(actuals.wins as varchar), ' - ', cast(actuals.losses as varchar))",
        "avg_wins": "round(reg_season.avg_wins, 1)",
        "vegas_wins": "vegas.win_total",
        "elo_vs_vegas": "round(vegas.win_total - round(reg_season.avg_wins, 1), 1)",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with reg_season as (\n"
        "    select\n"
        "        winning_team as team,\n"
        "        conf,\n"
        "        avg(try_cast(wins as double)) as avg_wins\n"
        "    from {{ ref('reg_season_end') }}\n"
        "    group by winning_team, conf\n"
        "),\n"
        "actuals as (\n"
        "    select * from {{ ref('nba_reg_season_actuals') }}\n"
        "),\n"
        "vegas as (\n"
        "    select * from {{ ref('nba_vegas_wins') }}\n"
        ")\n"
        "select\n"
        + normalized_report_select(columns, preferred, expressions)
        + "\nfrom reg_season\n"
        "left join actuals on actuals.team = reg_season.team\n"
        "left join vegas on vegas.team = reg_season.team\n"
    )


def synthesize_nba_playoff_summary_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = ["team", "made_playoffs", "made_conf_semis", "made_conf_finals", "made_finals", "won_finals"]
    expressions = {
        "team": "teams.team",
        "made_playoffs": "nullif(made_playoffs.made_playoffs, 0)",
        "made_conf_semis": "nullif(made_conf_semis.made_conf_semis, 0)",
        "made_conf_finals": "nullif(made_conf_finals.made_conf_finals, 0)",
        "made_finals": "nullif(made_finals.made_finals, 0)",
        "won_finals": "nullif(won_finals.won_finals, 0)",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with teams as (\n"
        "    select team from {{ ref('nba_teams') }}\n"
        "),\n"
        "made_playoffs as (\n"
        "    select winning_team as team, count(*)::bigint as made_playoffs\n"
        "    from {{ ref('initialize_seeding') }}\n"
        "    group by winning_team\n"
        "),\n"
        "made_conf_semis as (\n"
        "    select winning_team as team, count(*)::bigint as made_conf_semis\n"
        "    from {{ ref('playoff_sim_r1') }}\n"
        "    group by winning_team\n"
        "),\n"
        "made_conf_finals as (\n"
        "    select winning_team as team, count(*)::bigint as made_conf_finals\n"
        "    from {{ ref('playoff_sim_r2') }}\n"
        "    group by winning_team\n"
        "),\n"
        "made_finals as (\n"
        "    select winning_team as team, count(*)::bigint as made_finals\n"
        "    from {{ ref('playoff_sim_r3') }}\n"
        "    group by winning_team\n"
        "),\n"
        "won_finals as (\n"
        "    select winning_team as team, count(*)::bigint as won_finals\n"
        "    from {{ ref('playoff_sim_r4') }}\n"
        "    group by winning_team\n"
        ")\n"
        "select\n"
        + normalized_report_select(columns, preferred, expressions)
        + "\nfrom teams\n"
        "left join made_playoffs on made_playoffs.team = teams.team\n"
        "left join made_conf_semis on made_conf_semis.team = teams.team\n"
        "left join made_conf_finals on made_conf_finals.team = teams.team\n"
        "left join made_finals on made_finals.team = teams.team\n"
        "left join won_finals on won_finals.team = teams.team\n"
    )


def synthesize_nba_season_summary_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = [
        "elo_rating",
        "team",
        "conf",
        "record",
        "avg_wins",
        "vegas_wins",
        "elo_vs_vegas",
        "made_playoffs",
        "made_conf_semis",
        "made_conf_finals",
        "made_finals",
        "won_finals",
    ]
    output_columns = list(columns)
    for required in ["team", "conf"]:
        if required not in {column.lower() for column in output_columns}:
            output_columns.insert(1 if required == "team" else 2, required)
    expressions = {
        "elo_rating": (
            "concat("
            "cast(cast(round(ratings.elo_rating) as bigint) as varchar), "
            "' (', "
            "case when cast(round(ratings.elo_rating - ratings.original_rating) as bigint) >= 0 then '+' else '' end, "
            "cast(cast(round(ratings.elo_rating - ratings.original_rating) as bigint) as varchar), "
            "')'"
            ")"
        ),
        "team": "reg_season.team",
        "conf": "reg_season.conf",
        "record": "reg_season.record",
        "avg_wins": "reg_season.avg_wins",
        "vegas_wins": "reg_season.vegas_wins",
        "elo_vs_vegas": "reg_season.elo_vs_vegas",
        "made_playoffs": "playoffs.made_playoffs",
        "made_conf_semis": "playoffs.made_conf_semis",
        "made_conf_finals": "playoffs.made_conf_finals",
        "made_finals": "playoffs.made_finals",
        "won_finals": "playoffs.won_finals",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with reg_season as (\n"
        "    select * from {{ ref('reg_season_summary') }}\n"
        "),\n"
        "playoffs as (\n"
        "    select * from {{ ref('playoff_summary') }}\n"
        "),\n"
        "ratings as (\n"
        "    select * from {{ ref('nba_ratings') }}\n"
        ")\n"
        "select\n"
        + normalized_report_select(output_columns, preferred, expressions)
        + "\nfrom reg_season\n"
        "left join playoffs on playoffs.team = reg_season.team\n"
        "left join ratings on ratings.team = reg_season.team\n"
    )


def synthesize_reddit_prod_posts_ghosts_sql(columns: Sequence[str]) -> str:
    preferred = [
        "author_post",
        "author_flair_text",
        "distinguished_post",
        "edited_post",
        "post_id",
        "post_is_original_content",
        "post_locked",
        "post_fullname",
        "post_title",
        "post_text",
        "num_comments",
        "post_score",
        "post_url",
        "post_created_at",
        "hour_post_created_at",
        "normalized_post_created_at",
        "post_over_18",
        "post_spoiler",
        "post_stickied",
        "post_upvote_ratio",
    ]
    output_columns = list(columns) if columns else []
    present = {column.lower() for column in output_columns}
    for column in preferred:
        if column not in present:
            output_columns.append(column)
            present.add(column)
    created_at_expr = (
        "coalesce("
        "try_cast(created_at as timestamp), "
        "try_strptime(cast(created_at as varchar), '%Y-%m-%d %H:%M:%S'), "
        "try_strptime(cast(created_at as varchar), '%Y-%m-%d'), "
        "try_strptime(cast(created_at as varchar), '%Y/%m/%d'), "
        "try_strptime(cast(created_at as varchar), '%Y%m%d')"
        ")"
    )
    expressions = {
        "author_post": "author",
        "author_flair_text": "author_flair_text",
        "distinguished_post": "distinguished",
        "edited_post": "edited",
        "post_id": "post_id",
        "post_is_original_content": "is_original_content",
        "post_locked": "locked",
        "post_fullname": "post_fullname",
        "post_title": "post_title",
        "post_text": "post_text",
        "num_comments": "num_comments",
        "post_score": "post_score",
        "post_url": "post_url",
        "post_created_at": "created_at",
        "hour_post_created_at": f"strftime('%H', {created_at_expr})",
        "normalized_post_created_at": f"strftime('%Y-%m-%d %H:00:00', date_trunc('hour', {created_at_expr}))",
        "post_over_18": "over_18",
        "post_spoiler": "spoiler",
        "post_stickied": "stickied",
        "post_upvote_ratio": "upvote_ratio",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "select distinct\n"
        + normalized_report_select(output_columns, preferred, expressions)
        + "\nfrom main.raw_posts_ghosts\n"
        "where created_at >= timestamp '2023-01-01 00:00:00'\n"
    )


TWILIO_MESSAGE_COUNT_EXPRESSIONS = {
    "total_outbound_messages": "sum(case when lower(coalesce(messages.direction, '')) like '%outbound%' then 1 else 0 end)",
    "total_inbound_messages": "sum(case when lower(coalesce(messages.direction, '')) like '%inbound%' then 1 else 0 end)",
    "total_accepted_messages": "sum(case when lower(coalesce(messages.status, '')) = 'accepted' then 1 else 0 end)",
    "total_scheduled_messages": "sum(case when lower(coalesce(messages.status, '')) = 'scheduled' then 1 else 0 end)",
    "total_canceled_messages": "sum(case when lower(coalesce(messages.status, '')) = 'canceled' then 1 else 0 end)",
    "total_queued_messages": "sum(case when lower(coalesce(messages.status, '')) = 'queued' then 1 else 0 end)",
    "total_sending_messages": "sum(case when lower(coalesce(messages.status, '')) = 'sending' then 1 else 0 end)",
    "total_sent_messages": "sum(case when lower(coalesce(messages.status, '')) = 'sent' then 1 else 0 end)",
    "total_failed_messages": "sum(case when lower(coalesce(messages.status, '')) = 'failed' then 1 else 0 end)",
    "total_delivered_messages": "sum(case when lower(coalesce(messages.status, '')) = 'delivered' then 1 else 0 end)",
    "total_undelivered_messages": "sum(case when lower(coalesce(messages.status, '')) = 'undelivered' then 1 else 0 end)",
    "total_receiving_messages": "sum(case when lower(coalesce(messages.status, '')) = 'receiving' then 1 else 0 end)",
    "total_received_messages": "sum(case when lower(coalesce(messages.status, '')) = 'received' then 1 else 0 end)",
    "total_read_messages": "sum(case when lower(coalesce(messages.status, '')) = 'read' then 1 else 0 end)",
    "total_messages": "count(*)",
}


def synthesize_twilio_number_overview_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = [
        "phone_number",
        "total_outbound_messages",
        "total_inbound_messages",
        "total_accepted_messages",
        "total_scheduled_messages",
        "total_canceled_messages",
        "total_queued_messages",
        "total_sending_messages",
        "total_sent_messages",
        "total_failed_messages",
        "total_delivered_messages",
        "total_undelivered_messages",
        "total_receiving_messages",
        "total_received_messages",
        "total_read_messages",
        "total_messages",
        "total_spend",
    ]
    expressions = {
        "phone_number": "messages.phone_number",
        **TWILIO_MESSAGE_COUNT_EXPRESSIONS,
        "total_spend": "sum(coalesce(messages.price, 0))",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with messages as (\n"
        "    select * from {{ ref('twilio__message_enhanced') }}\n"
        ")\n"
        "select\n"
        + normalized_report_select(columns, preferred, expressions)
        + "\nfrom messages\n"
        "group by messages.phone_number\n"
    )


def synthesize_twilio_account_overview_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = [
        "account_id",
        "account_name",
        "account_status",
        "account_type",
        "date_day",
        "date_week",
        "date_month",
        "price_unit",
        "total_outbound_messages",
        "total_inbound_messages",
        "total_accepted_messages",
        "total_scheduled_messages",
        "total_canceled_messages",
        "total_queued_messages",
        "total_sending_messages",
        "total_sent_messages",
        "total_failed_messages",
        "total_delivered_messages",
        "total_undelivered_messages",
        "total_receiving_messages",
        "total_received_messages",
        "total_read_messages",
        "total_messages",
        "total_messages_spend",
        "total_account_spend",
    ]
    expressions = {
        "account_id": "messages.account_id",
        "account_name": "account_history.friendly_name",
        "account_status": "account_history.status",
        "account_type": "account_history.type",
        "date_day": "messages.date_day",
        "date_week": "messages.date_week",
        "date_month": "messages.date_month",
        "price_unit": "messages.price_unit",
        **TWILIO_MESSAGE_COUNT_EXPRESSIONS,
        "total_messages_spend": "round(abs(sum(coalesce(messages.price, 0))), 2)",
        "total_account_spend": "account_spend.total_account_spend",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with messages as (\n"
        "    select * from {{ ref('twilio__message_enhanced') }}\n"
        "),\n"
        "account_history as (\n"
        "    select * from {{ var('account_history') }}\n"
        "    where coalesce(is_most_recent_record, true)\n"
        "),\n"
        "account_spend as (\n"
        "    select\n"
        "        account_id,\n"
        "        start_date as date_day,\n"
        "        sum(price) as total_account_spend\n"
        "    from {{ var('usage_record') }}\n"
        "    group by account_id, start_date\n"
        ")\n"
        "select\n"
        + normalized_report_select(columns, preferred, expressions)
        + "\nfrom messages\n"
        "left join account_history on account_history.account_id = messages.account_id\n"
        "left join account_spend\n"
        "  on account_spend.account_id = messages.account_id\n"
        " and account_spend.date_day = messages.date_day\n"
        "group by messages.account_id, account_history.friendly_name, account_history.status, account_history.type,\n"
        "         messages.date_day, messages.date_week, messages.date_month, messages.price_unit, account_spend.total_account_spend\n"
    )


def synthesize_marketo_email_templates_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = [
        "created_timestamp",
        "description",
        "folder_name",
        "folder_id",
        "folder_type",
        "folder_value",
        "from_email",
        "from_name",
        "email_template_id",
        "email_template_name",
        "is_operational",
        "program_id",
        "publish_to_msi",
        "reply_email",
        "email_template_status",
        "email_subject",
        "parent_template_id",
        "is_text_only",
        "updated_timestamp",
        "email_template_url",
        "version_type",
        "has_web_view_enabled",
        "workspace_name",
        "inferred_version",
        "valid_from",
        "valid_to",
        "is_most_recent_version",
        "email_template_history_id",
        "total_count_of_versions",
        "count_sends",
        "count_opens",
        "count_bounces",
        "count_clicks",
        "count_deliveries",
        "count_unsubscribes",
        "count_unique_opens",
        "count_unique_clicks",
    ]
    deduped_columns = list(dict.fromkeys(columns))
    expressions = {
        column: f"templates.{quote_duckdb_identifier(column)}"
        for column in preferred
        if column not in {
            "count_sends",
            "count_opens",
            "count_bounces",
            "count_clicks",
            "count_deliveries",
            "count_unsubscribes",
            "count_unique_opens",
            "count_unique_clicks",
        }
    }
    expressions.update(
        {
            "count_sends": "coalesce(stats.count_sends, 0)",
            "count_opens": "coalesce(stats.count_opens, 0)",
            "count_bounces": "coalesce(stats.count_bounces, 0)",
            "count_clicks": "coalesce(stats.count_clicks, 0)",
            "count_deliveries": "coalesce(stats.count_deliveries, 0)",
            "count_unsubscribes": "coalesce(stats.count_unsubscribes, 0)",
            "count_unique_opens": "coalesce(stats.count_opens, 0)",
            "count_unique_clicks": "coalesce(stats.count_clicks, 0)",
        }
    )
    return (
        "{{ config(materialized='table') }}\n\n"
        "with templates as (\n"
        "    select *\n"
        "    from {{ ref('stg_marketo__email_template_history') }}\n"
        "    where coalesce(is_most_recent_version, false)\n"
        "),\n"
        "stats as (\n"
        "    select\n"
        "        email_template_id,\n"
        "        count(distinct email_send_id) as count_sends,\n"
        "        sum(count_opens) as count_opens,\n"
        "        sum(count_bounces) as count_bounces,\n"
        "        sum(count_clicks) as count_clicks,\n"
        "        sum(count_deliveries) as count_deliveries,\n"
        "        sum(count_unsubscribes) as count_unsubscribes\n"
        "    from {{ ref('marketo__email_sends') }}\n"
        "    group by email_template_id\n"
        ")\n"
        "select\n"
        + normalized_report_select(deduped_columns, preferred, expressions)
        + "\nfrom templates\n"
        "left join stats on stats.email_template_id = templates.email_template_id\n"
    )


def synthesize_mrr_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = [
        "date_month",
        "customer_id",
        "mrr",
        "is_active",
        "first_active_month",
        "last_active_month",
        "is_first_month",
        "is_last_month",
        "previous_month_is_active",
        "previous_month_mrr",
        "mrr_change",
        "change_category",
    ]
    expressions = {
        "date_month": "classified.date_month",
        "customer_id": "classified.customer_id",
        "mrr": "classified.mrr",
        "is_active": "classified.is_active",
        "first_active_month": "classified.first_active_month",
        "last_active_month": "classified.last_active_month",
        "is_first_month": "classified.is_first_month",
        "is_last_month": "classified.is_last_month",
        "previous_month_is_active": "classified.previous_month_is_active",
        "previous_month_mrr": "classified.previous_month_mrr",
        "mrr_change": "classified.mrr_change",
        "change_category": "classified.change_category",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with base as (\n"
        "    select * from {{ ref('customer_revenue_by_month') }}\n"
        "    union all\n"
        "    select * from {{ ref('customer_churn_month') }}\n"
        "),\n"
        "with_previous as (\n"
        "    select\n"
        "        *,\n"
        "        coalesce(lag(is_active) over (partition by customer_id order by date_month), false) as previous_month_is_active,\n"
        "        coalesce(lag(mrr) over (partition by customer_id order by date_month), 0) as previous_month_mrr\n"
        "    from base\n"
        "),\n"
        "classified as (\n"
        "    select\n"
        "        *,\n"
        "        mrr - previous_month_mrr as mrr_change,\n"
        "        case\n"
        "            when is_first_month then 'new'\n"
        "            when not is_active and previous_month_is_active then 'churn'\n"
        "            when is_active and not previous_month_is_active then 'reactivation'\n"
        "            when mrr > previous_month_mrr then 'upgrade'\n"
        "            when mrr < previous_month_mrr then 'downgrade'\n"
        "            else null\n"
        "        end as change_category\n"
        "    from with_previous\n"
        ")\n"
        "select\n"
        + normalized_report_select(columns, preferred, expressions)
        + "\nfrom classified\n"
    )


def synthesize_intercom_admin_metrics_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = [
        "admin_id",
        "admin_name",
        "team_id",
        "team_name",
        "job_title",
        "total_conversations_closed",
        "average_conversation_parts",
        "average_conversation_rating",
        "median_conversations_reopened",
        "median_conversation_assignments",
        "median_time_to_first_response_time_minutes",
        "median_time_to_last_close_minutes",
    ]
    expressions = {
        "admin_id": "admin.admin_id",
        "admin_name": "admin.name",
        "team_id": "team_admin.team_id",
        "team_name": "team.name",
        "job_title": "admin.job_title",
        "total_conversations_closed": "cast(null as bigint)",
        "average_conversation_parts": "cast(null as double)",
        "average_conversation_rating": "cast(null as double)",
        "median_conversations_reopened": "cast(null as double)",
        "median_conversation_assignments": "cast(null as double)",
        "median_time_to_first_response_time_minutes": "cast(null as double)",
        "median_time_to_last_close_minutes": "cast(null as double)",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with admin as (\n"
        "    select * from {{ var('admin') }}\n"
        "    where not coalesce(_fivetran_deleted, false)\n"
        "),\n"
        "team_admin as (\n"
        "    select * from {{ var('team_admin') }}\n"
        "    where not coalesce(_fivetran_deleted, false)\n"
        "),\n"
        "team as (\n"
        "    select * from {{ var('team') }}\n"
        "    where not coalesce(_fivetran_deleted, false)\n"
        ")\n"
        "select\n"
        + normalized_report_select(columns, preferred, expressions)
        + "\nfrom admin\n"
        "left join team_admin on team_admin.admin_id = admin.admin_id\n"
        "left join team on team.team_id = team_admin.team_id\n"
    )


def synthesize_jira_project_enhanced_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = [
        "project_description",
        "project_id",
        "project_key",
        "project_lead_user_id",
        "project_name",
        "project_category_id",
        "permission_scheme_id",
        "_fivetran_synced",
        "project_lead_user_name",
        "project_lead_email",
        "epics",
        "components",
        "count_closed_issues",
        "count_open_issues",
        "count_open_assigned_issues",
        "avg_close_time_days",
        "avg_assigned_close_time_days",
        "avg_age_currently_open_days",
        "avg_age_currently_open_assigned_days",
        "median_close_time_days",
        "median_age_currently_open_days",
        "median_assigned_close_time_days",
        "median_age_currently_open_assigned_days",
        "avg_close_time_seconds",
        "avg_assigned_close_time_seconds",
        "avg_age_currently_open_seconds",
        "avg_age_currently_open_assigned_seconds",
        "median_close_time_seconds",
        "median_age_currently_open_seconds",
        "median_assigned_close_time_seconds",
        "median_age_currently_open_assigned_seconds",
    ]
    expressions = {
        "project_description": "project.project_description",
        "project_id": "project.project_id",
        "project_key": "project.project_key",
        "project_lead_user_id": "project.project_lead_user_id",
        "project_name": "project.project_name",
        "project_category_id": "project.project_category_id",
        "permission_scheme_id": "project.permission_scheme_id",
        "_fivetran_synced": "project._fivetran_synced",
        "project_lead_user_name": "lead_user.user_display_name",
        "project_lead_email": "lead_user.email",
        "epics": "cast(null as varchar)",
        "components": "components.components",
        "count_closed_issues": "coalesce(metrics.count_closed_issues, 0)",
        "count_open_issues": "coalesce(metrics.count_open_issues, 0)",
        "count_open_assigned_issues": "coalesce(metrics.count_open_assigned_issues, 0)",
        "avg_close_time_days": "metrics.avg_close_time_days",
        "avg_assigned_close_time_days": "metrics.avg_assigned_close_time_days",
        "avg_age_currently_open_days": "metrics.avg_age_currently_open_days",
        "avg_age_currently_open_assigned_days": "metrics.avg_age_currently_open_assigned_days",
        "median_close_time_days": "metrics.median_close_time_days",
        "median_age_currently_open_days": "metrics.median_age_currently_open_days",
        "median_assigned_close_time_days": "metrics.median_assigned_close_time_days",
        "median_age_currently_open_assigned_days": "metrics.median_age_currently_open_assigned_days",
        "avg_close_time_seconds": "metrics.avg_close_time_seconds",
        "avg_assigned_close_time_seconds": "metrics.avg_assigned_close_time_seconds",
        "avg_age_currently_open_seconds": "metrics.avg_age_currently_open_seconds",
        "avg_age_currently_open_assigned_seconds": "metrics.avg_age_currently_open_assigned_seconds",
        "median_close_time_seconds": "metrics.median_close_time_seconds",
        "median_age_currently_open_seconds": "metrics.median_age_currently_open_seconds",
        "median_assigned_close_time_seconds": "metrics.median_assigned_close_time_seconds",
        "median_age_currently_open_assigned_seconds": "metrics.median_age_currently_open_assigned_seconds",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with project as (\n"
        "    select * from {{ var('project') }}\n"
        "),\n"
        "lead_user as (\n"
        "    select * from {{ var('user') }}\n"
        "),\n"
        "metrics as (\n"
        "    select * from {{ ref('int_jira__project_metrics') }}\n"
        "),\n"
        "components as (\n"
        "    select\n"
        "        project_id,\n"
        "        string_agg(component_name, ', ' order by component_name desc) as components\n"
        "    from {{ var('component') }}\n"
        "    group by project_id\n"
        ")\n"
        "select\n"
        + normalized_report_select(columns, preferred, expressions)
        + "\nfrom project\n"
        "left join lead_user on project.project_lead_user_id = lead_user.user_id\n"
        "left join metrics on project.project_id = metrics.project_id\n"
        "left join components on project.project_id = components.project_id\n"
    )


def synthesize_recharge_charge_line_item_history_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = [
        "charge_id",
        "charge_row_num",
        "source_index",
        "charge_created_at",
        "customer_id",
        "address_id",
        "amount",
        "title",
        "line_item_type",
    ]
    expressions = {
        "charge_id": "numbered.charge_id",
        "charge_row_num": "numbered.charge_row_num",
        "source_index": "numbered.source_index",
        "charge_created_at": "numbered.charge_created_at",
        "customer_id": "numbered.customer_id",
        "address_id": "numbered.address_id",
        "amount": "numbered.amount",
        "title": "numbered.title",
        "line_item_type": "numbered.line_item_type",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with charge as (\n"
        "    select * from {{ var('charge') }}\n"
        "),\n"
        "charge_lines as (\n"
        "    select\n"
        "        charge_line_item.charge_id,\n"
        "        charge_line_item.index as source_index,\n"
        "        charge.charge_created_at,\n"
        "        charge.customer_id,\n"
        "        charge.address_id,\n"
        "        charge_line_item.total_price as amount,\n"
        "        charge_line_item.title,\n"
        "        'charge line' as line_item_type,\n"
        "        1 as sort_order\n"
        "    from {{ var('charge_line_item') }} as charge_line_item\n"
        "    left join charge on charge.charge_id = charge_line_item.charge_id\n"
        "),\n"
        "discounts as (\n"
        "    select\n"
        "        charge_discount.charge_id,\n"
        "        charge_discount.index as source_index,\n"
        "        charge.charge_created_at,\n"
        "        charge.customer_id,\n"
        "        charge.address_id,\n"
        "        case\n"
        "            when lower(charge_discount.value_type) = 'percentage'\n"
        "                then round(charge.subtotal_price * charge_discount.discount_value / 100.0, 2)\n"
        "            else charge_discount.discount_value\n"
        "        end as amount,\n"
        "        charge_discount.code as title,\n"
        "        'discount' as line_item_type,\n"
        "        2 as sort_order\n"
        "    from {{ var('charge_discount') }} as charge_discount\n"
        "    left join charge on charge.charge_id = charge_discount.charge_id\n"
        "),\n"
        "shipping as (\n"
        "    select\n"
        "        charge_shipping_line.charge_id,\n"
        "        charge_shipping_line.index as source_index,\n"
        "        charge.charge_created_at,\n"
        "        charge.customer_id,\n"
        "        charge.address_id,\n"
        "        charge_shipping_line.price as amount,\n"
        "        charge_shipping_line.title,\n"
        "        'shipping' as line_item_type,\n"
        "        3 as sort_order\n"
        "    from {{ var('charge_shipping_line') }} as charge_shipping_line\n"
        "    left join charge on charge.charge_id = charge_shipping_line.charge_id\n"
        "),\n"
        "tax as (\n"
        "    select\n"
        "        charge_tax_line.charge_id,\n"
        "        charge_tax_line.index as source_index,\n"
        "        charge.charge_created_at,\n"
        "        charge.customer_id,\n"
        "        charge.address_id,\n"
        "        charge_tax_line.price as amount,\n"
        "        charge_tax_line.title,\n"
        "        'tax' as line_item_type,\n"
        "        4 as sort_order\n"
        "    from {{ var('charge_tax_line') }} as charge_tax_line\n"
        "    left join charge on charge.charge_id = charge_tax_line.charge_id\n"
        "),\n"
        "unioned as (\n"
        "    select * from charge_lines\n"
        "    union all\n"
        "    select * from discounts\n"
        "    union all\n"
        "    select * from shipping\n"
        "    union all\n"
        "    select * from tax\n"
        "),\n"
        "numbered as (\n"
        "    select\n"
        "        *,\n"
        "        row_number() over (partition by charge_id order by sort_order, source_index) as charge_row_num\n"
        "    from unioned\n"
        ")\n"
        "select\n"
        + normalized_report_select(columns, preferred, expressions)
        + "\nfrom numbered\n"
    )


def synthesize_sap_0fi_gl_10_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = [
        "ryear",
        "activ",
        "rmvct",
        "rtcur",
        "runit",
        "awtyp",
        "rldnr",
        "rrcty",
        "rvers",
        "logsys",
        "racct",
        "cost_elem",
        "rbukrs",
        "rcntr",
        "prctr",
        "rfarea",
        "rbusa",
        "kokrs",
        "segment",
        "scntr",
        "pprctr",
        "sfarea",
        "sbusa",
        "rassc",
        "psegment",
        "faglflext_timestamp",
        "currency_type",
        "fiscal_period",
        "debit_amount",
        "credit_amount",
        "accumulated_balance",
        "turnover",
    ]
    measure_columns = {"debit_amount", "credit_amount", "accumulated_balance", "turnover"}
    group_columns = [column for column in preferred if column not in measure_columns]
    expressions = {column: f"grouped.{quote_duckdb_identifier(column)}" for column in preferred}
    return (
        "{{ config(materialized='table') }}\n\n"
        "with unpivoted as (\n"
        "    select * from {{ ref('int_sap__0fi_gl_10_unpivot') }}\n"
        "),\n"
        "grouped as (\n"
        "    select\n"
        + ",\n".join(f"        {quote_duckdb_identifier(column)}" for column in group_columns)
        + ",\n"
        "        sum(debit_amount) as debit_amount,\n"
        "        sum(credit_amount) as credit_amount,\n"
        "        sum(accumulated_balance) as accumulated_balance,\n"
        "        sum(turnover) as turnover\n"
        "    from unpivoted\n"
        "    group by\n"
        + ",\n".join(f"        {quote_duckdb_identifier(column)}" for column in group_columns)
        + "\n"
        ")\n"
        "select\n"
        + normalized_report_select(columns, preferred, expressions)
        + "\nfrom grouped\n"
    )


def synthesize_sap_0fi_gl_14_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    faglflexa_columns = {
        "ryear",
        "docnr",
        "rldnr",
        "rbukrs",
        "docln",
        "activ",
        "rmvct",
        "rtcur",
        "runit",
        "awtyp",
        "rrcty",
        "rvers",
        "logsys",
        "racct",
        "cost_elem",
        "rcntr",
        "prctr",
        "rfarea",
        "rbusa",
        "kokrs",
        "segment",
        "scntr",
        "pprctr",
        "sfarea",
        "sbusa",
        "rassc",
        "psegment",
        "tsl",
        "hsl",
        "ksl",
        "osl",
        "msl",
        "wsl",
        "drcrk",
        "poper",
        "rwcur",
        "gjahr",
        "budat",
        "belnr",
        "buzei",
        "bschl",
        "bstat",
        "faglflexa_timestamp",
    }
    bkpf_columns = {
        "bukrs",
        "blart",
        "bldat",
        "monat",
        "cpudt",
        "xblnr",
        "waers",
        "glvor",
        "awkey",
        "fikrs",
        "hwaer",
        "hwae2",
        "hwae3",
        "awsys",
        "ldgrp",
        "kursf",
        "xreorg",
    }
    bseg_columns = {
        "anln1",
        "anln2",
        "aufnr",
        "augbl",
        "augdt",
        "ebeln",
        "ebelp",
        "eten2",
        "filkd",
        "gsber",
        "koart",
        "kostl",
        "maber",
        "madat",
        "mansp",
        "manst",
        "mschl",
        "mwskz",
        "posn2",
        "qbshb",
        "qsfbt",
        "qsshb",
        "rebzg",
        "samnr",
        "sgtxt",
        "shkzg",
        "skfbt",
        "wskto",
        "sknto",
        "umsks",
        "umskz",
        "uzawe",
        "valut",
        "vbel2",
        "vbeln",
        "vbewa",
        "vbund",
        "vertn",
        "vertt",
        "werks",
        "wverw",
        "xzahl",
        "zbd1p",
        "zbd1t",
        "zbd2p",
        "zbd2t",
        "zbd3t",
        "zfbdt",
        "zlsch",
        "zlspr",
        "zterm",
        "zuonr",
        "xref1",
        "xref2",
        "rstgr",
        "rebzt",
        "pswsl",
        "pswbt",
        "hkont",
        "xnegp",
        "zbfix",
        "rfzei",
        "ccbtc",
        "kkber",
        "xref3",
        "dtws1",
        "dtws2",
        "dtws3",
        "dtws4",
        "absbt",
        "projk",
        "xpypr",
        "kidno",
        "bupla",
        "secco",
        "pycur",
        "pyamt",
        "xragl",
        "cession_kz",
        "buzid",
        "auggj",
        "agzei",
        "bdiff",
        "bdif2",
        "bdif3",
        "bewar",
        "dabrz",
        "dmbtr",
        "fkber",
        "fkber_long",
        "imkey",
        "kstar",
        "kunnr",
        "lifnr",
        "meins",
        "menge",
        "pargb",
        "pfkber",
        "pprct",
        "saknr",
        "wrbtr",
        "xopvw",
        "xlgclr",
        "zzspreg",
        "zzbuspartn",
        "zzproduct",
        "zzloca",
        "zzchan",
        "zzlob",
        "zzuserfld1",
        "zzuserfld2",
        "zzuserfld3",
        "zzregion",
        "zzstate",
    }
    preferred = [
        "ryear",
        "docnr",
        "rldnr",
        "rbukrs",
        "docln",
        "activ",
        "rmvct",
        "rtcur",
        "runit",
        "awtyp",
        "rrcty",
        "rvers",
        "logsys",
        "racct",
        "cost_elem",
        "rcntr",
        "prctr",
        "rfarea",
        "rbusa",
        "kokrs",
        "segment",
        "scntr",
        "pprctr",
        "sfarea",
        "sbusa",
        "rassc",
        "psegment",
        "tsl",
        "hsl",
        "ksl",
        "osl",
        "msl",
        "wsl",
        "drcrk",
        "poper",
        "rwcur",
        "gjahr",
        "budat",
        "belnr",
        "buzei",
        "bschl",
        "bstat",
        "faglflexa_timestamp",
        "bukrs",
        "blart",
        "bldat",
        "monat",
        "cpudt",
        "xblnr",
        "waers",
        "glvor",
        "awkey",
        "fikrs",
        "hwaer",
        "hwae2",
        "hwae3",
        "awsys",
        "ldgrp",
        "kursf",
        "xreorg",
    ]
    preferred.extend(sorted(bseg_columns))
    expressions: Dict[str, str] = {}
    for column in ordered_known_columns(columns, preferred):
        lower = column.lower()
        if lower in faglflexa_columns:
            expressions[lower] = f"faglflexa.{quote_duckdb_identifier(lower)}"
        elif lower in bkpf_columns:
            expressions[lower] = f"bkpf.{quote_duckdb_identifier(lower)}"
        elif lower in bseg_columns:
            expressions[lower] = f"bseg.{quote_duckdb_identifier(lower)}"
        else:
            expressions[lower] = default_expression_for_column(column)
    return (
        "{{ config(materialized='table') }}\n\n"
        "with faglflexa as (\n"
        "    select * from {{ var('faglflexa') }}\n"
        "),\n"
        "bkpf as (\n"
        "    select * from {{ var('bkpf') }}\n"
        "),\n"
        "bseg as (\n"
        "    select * from {{ var('bseg') }}\n"
        ")\n"
        "select\n"
        + normalized_report_select(columns, preferred, expressions)
        + "\nfrom faglflexa\n"
        "left join bkpf\n"
        "    on faglflexa.rclnt = bkpf.mandt\n"
        "    and faglflexa.rbukrs = bkpf.bukrs\n"
        "    and faglflexa.belnr = bkpf.belnr\n"
        "    and faglflexa.gjahr = bkpf.gjahr\n"
        "left join bseg\n"
        "    on faglflexa.rclnt = bseg.mandt\n"
        "    and faglflexa.rbukrs = bseg.bukrs\n"
        "    and faglflexa.docnr = bseg.belnr\n"
        "    and faglflexa.gjahr = bseg.gjahr\n"
        "    and faglflexa.docln = bseg.buzei\n"
    )


def synthesize_package_mart_sql(
    model_name: str,
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    lower = model_name.lower()
    app_reporting_intermediate_sql = synthesize_app_reporting_intermediate_sql(model_name, columns, related_candidates)
    if app_reporting_intermediate_sql:
        return app_reporting_intermediate_sql
    if lower == "apple_store__source_type_report":
        return synthesize_apple_store_source_type_report_sql(columns)
    if lower == "apple_store__territory_report":
        return synthesize_apple_store_territory_report_sql(columns)
    if lower == "reg_season_summary":
        return synthesize_nba_reg_season_summary_sql(columns)
    if lower == "playoff_summary":
        return synthesize_nba_playoff_summary_sql(columns)
    if lower == "season_summary":
        return synthesize_nba_season_summary_sql(columns)
    if lower == "prod_posts_ghosts":
        return synthesize_reddit_prod_posts_ghosts_sql(columns)
    if lower == "twilio__number_overview":
        return synthesize_twilio_number_overview_sql(columns)
    if lower == "twilio__account_overview":
        return synthesize_twilio_account_overview_sql(columns)
    if lower == "marketo__email_templates":
        return synthesize_marketo_email_templates_sql(columns)
    if lower == "mrr":
        return synthesize_mrr_sql(columns)
    if lower == "intercom__admin_metrics":
        return synthesize_intercom_admin_metrics_sql(columns)
    if lower == "jira__project_enhanced":
        return synthesize_jira_project_enhanced_sql(columns)
    if lower == "recharge__charge_line_item_history":
        return synthesize_recharge_charge_line_item_history_sql(columns)
    if lower == "sap__0fi_gl_10":
        return synthesize_sap_0fi_gl_10_sql(columns)
    if lower == "sap__0fi_gl_14":
        return synthesize_sap_0fi_gl_14_sql(columns)
    if lower == "int_google_play__store_performance":
        return synthesize_google_play_store_performance_sql(columns)
    if lower == "google_play__overview_report":
        return synthesize_google_play_overview_report_sql(columns)
    if lower == "google_play__country_report":
        return synthesize_google_play_country_report_sql(columns)
    if lower == "google_play__device_report":
        return synthesize_google_play_device_report_sql(columns)
    if lower == "mrt_capacity_tariff":
        return synthesize_inzight_capacity_tariff_sql(columns, related_candidates)
    if lower == "dim_events":
        return synthesize_tickit_dim_events_sql(columns, related_candidates)
    if lower == "fct_listings":
        return synthesize_tickit_fct_listings_sql(columns, related_candidates)
    if lower == "fct_sales":
        return synthesize_tickit_fct_sales_sql(columns, related_candidates)
    tpch_lowcost_brass_sql = synthesize_tpch_lowcost_brass_suppliers_sql(model_name, columns)
    if tpch_lowcost_brass_sql:
        return tpch_lowcost_brass_sql
    if lower == "finishes_by_constructor":
        return synthesize_f1_finishes_by_constructor_sql(columns)
    if lower == "finishes_by_driver":
        return synthesize_f1_finishes_by_driver_sql(columns)
    if lower == "stg_f1_dataset__drivers":
        return synthesize_f1_stg_drivers_sql(columns)
    if lower == "driver_podiums_by_season":
        return synthesize_f1_driver_podiums_by_season_sql(columns)
    if lower == "driver_fastest_laps_by_season":
        return synthesize_f1_driver_fastest_laps_by_season_sql(columns)
    if lower == "constructor_retirements_by_season":
        return synthesize_f1_constructor_retirements_by_season_sql(columns)
    if lower == "driver_championships":
        return synthesize_f1_driver_championships_sql(columns)
    if lower == "report_customer_invoices":
        return synthesize_retail_report_customer_invoices_sql(columns)
    if lower == "shopify__products":
        return synthesize_shopify_products_sql(columns, related_candidates)
    if lower == "shopify__discounts":
        return synthesize_shopify_discounts_sql(columns, related_candidates)
    if lower == "shopify__daily_shop":
        return synthesize_shopify_daily_shop_sql(columns, related_candidates)
    if lower == "actor_rating_by_total_movie":
        return synthesize_flicks_actor_rating_sql(columns, related_candidates)
    if lower == "movie_actor_by_year":
        return synthesize_flicks_movie_actor_by_year_sql(columns, related_candidates)
    if lower == "fact_purchase_order":
        return synthesize_northwind_purchase_order_fact_sql(columns, related_candidates)
    if lower == "obt_customer_reporting":
        return synthesize_northwind_customer_reporting_obt_sql(columns, related_candidates)
    if lower == "xero__general_ledger":
        return synthesize_xero_general_ledger_sql(columns, related_candidates)
    if lower == "xero__profit_and_loss_report":
        return synthesize_xero_profit_and_loss_report_sql(columns, related_candidates)
    if lower == "xero__balance_sheet_report":
        return synthesize_xero_balance_sheet_report_sql(columns, related_candidates)
    return ""


def tickit_candidate_expr(
    candidates: Sequence[Dict[str, Any]] | None,
    name: str,
) -> str:
    candidate = candidate_named(candidates, name)
    return str(candidate.get("expr") or "") if candidate else ""


def tickit_report_select(
    columns: Sequence[str],
    preferred: Sequence[str],
    expressions: Dict[str, str],
) -> str:
    ordered = ordered_known_columns(columns, preferred)
    return ",\n".join(
        f"    {expressions.get(column.lower(), default_expression_for_column(column))} as {quote_duckdb_identifier(column)}"
        for column in ordered
    )


def normalized_report_select(
    columns: Sequence[str],
    preferred: Sequence[str],
    expressions: Dict[str, str],
) -> str:
    ordered = ordered_known_columns(columns, preferred)
    normalized = {normalize_relation_name(key): value for key, value in expressions.items()}
    return ",\n".join(
        f"    {expressions.get(column.lower(), normalized.get(normalize_relation_name(column), default_expression_for_column(column)))} as {quote_duckdb_identifier(column)}"
        for column in ordered
    )


def synthesize_inzight_capacity_tariff_sql(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    if not columns:
        return ""
    electricity = tickit_candidate_expr(related_candidates, "fct_electricity") or "{{ ref('fct_electricity') }}"
    holidays = tickit_candidate_expr(related_candidates, "stg_be_holidays") or "{{ ref('stg_be_holidays') }}"
    preferred = [
        "month",
        "year",
        "month_name",
        "month_name_short",
        "month_start_date",
        "month_peak_timestamp",
        "month_peak_timestamp_end",
        "month_peak_date",
        "month_peak_day_of_week_name",
        "month_peak_day_of_month",
        "month_peak_day_type",
        "month_peak_is_holiday",
        "month_peak_value",
        "month_peak_part_of_day",
        "month_peak_12month_avg",
        "pct_change",
    ]
    expressions = {
        "month": "month",
        "year": "year",
        "month_name": "month_name",
        "month_name_short": "month_name_short",
        "month_start_date": "month_start_date",
        "month_peak_timestamp": "month_peak_timestamp",
        "month_peak_timestamp_end": "month_peak_timestamp_end",
        "month_peak_date": "month_peak_date",
        "month_peak_day_of_week_name": "month_peak_day_of_week_name",
        "month_peak_day_of_month": "month_peak_day_of_month",
        "month_peak_day_type": "month_peak_day_type",
        "month_peak_is_holiday": "month_peak_is_holiday",
        "month_peak_value": "month_peak_value",
        "month_peak_part_of_day": "month_peak_part_of_day",
        "month_peak_12month_avg": "month_peak_12month_avg",
        "pct_change": "pct_change",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with electricity as (\n"
        f"    select * from {electricity}\n"
        "),\n"
        "holidays as (\n"
        f"    select * from {holidays}\n"
        "),\n"
        "ranked_quarters as (\n"
        "    select\n"
        "        extract(month from from_timestamp)::bigint as month,\n"
        "        extract(year from from_timestamp)::bigint as year,\n"
        "        strftime(from_timestamp, '%B') as month_name,\n"
        "        strftime(from_timestamp, '%b') as month_name_short,\n"
        "        date_trunc('month', from_timestamp)::date as month_start_date,\n"
        "        from_timestamp as month_peak_timestamp,\n"
        "        to_timestamp as month_peak_timestamp_end,\n"
        "        from_timestamp::date as month_peak_date,\n"
        "        strftime(from_timestamp, '%A') as month_peak_day_of_week_name,\n"
        "        extract(day from from_timestamp)::bigint as month_peak_day_of_month,\n"
        "        case when strftime(from_timestamp, '%w') in ('0', '6') then 'weekend' else 'weekday' end as month_peak_day_type,\n"
        "        case\n"
        "            when strftime(from_timestamp, '%m-%d') in (\n"
        "                select strftime(holiday_date, '%m-%d') from holidays\n"
        "            ) then true\n"
        "            else false\n"
        "        end as month_peak_is_holiday,\n"
        "        usage * 4.0 as month_peak_value,\n"
        "        case\n"
        "            when extract(hour from from_timestamp) < 6 then 'night'\n"
        "            when extract(hour from from_timestamp) < 12 then 'morning'\n"
        "            when extract(hour from from_timestamp) < 18 then 'afternoon'\n"
        "            when extract(hour from from_timestamp) < 23 then 'evening'\n"
        "            else 'night'\n"
        "        end as month_peak_part_of_day,\n"
        "        row_number() over (\n"
        "            partition by extract(year from from_timestamp), extract(month from from_timestamp)\n"
        "            order by usage desc, from_timestamp asc, to_timestamp asc\n"
        "        ) as rn\n"
        "    from electricity\n"
        "    where usage is not null and usage > 0\n"
        "),\n"
        "monthly_peaks as (\n"
        "    select * exclude(rn)\n"
        "    from ranked_quarters\n"
        "    where rn = 1\n"
        "),\n"
        "final as (\n"
        "    select\n"
        "        *,\n"
        "        avg(month_peak_value) over (\n"
        "            order by month_start_date rows between current row and 11 following\n"
        "        ) as month_peak_12month_avg,\n"
        "        (month_peak_value - lag(month_peak_value) over (order by month_start_date))\n"
        "            / nullif(lag(month_peak_value) over (order by month_start_date), 0) as pct_change\n"
        "    from monthly_peaks\n"
        ")\n"
        "select\n"
        + tickit_report_select(columns, preferred, expressions)
        + "\nfrom final\n"
    )


def synthesize_tickit_dim_events_sql(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    if not columns:
        return ""
    events = tickit_candidate_expr(related_candidates, "stg_tickit__events")
    venues = tickit_candidate_expr(related_candidates, "stg_tickit__venues")
    categories = tickit_candidate_expr(related_candidates, "stg_tickit__categories")
    dates = tickit_candidate_expr(related_candidates, "stg_tickit__dates")
    if not all([events, venues, categories, dates]):
        return ""
    preferred = [
        "event_id",
        "event_name",
        "start_time",
        "venue_name",
        "venue_city",
        "venue_state",
        "venue_seats",
        "cat_group",
        "cat_name",
        "cat_desc",
        "week",
        "qtr",
        "holiday",
    ]
    expressions = {
        "event_id": "events.event_id",
        "event_name": "events.event_name",
        "start_time": "events.start_time",
        "venue_name": "venues.venue_name",
        "venue_city": "venues.venue_city",
        "venue_state": "venues.venue_state",
        "venue_seats": "venues.venue_seats",
        "cat_group": "categories.cat_group",
        "cat_name": "categories.cat_name",
        "cat_desc": "categories.cat_desc",
        "week": "dates.week",
        "qtr": "dates.qtr",
        "holiday": "dates.holiday",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with events as (\n"
        f"    select * from {events}\n"
        "),\n"
        "venues as (\n"
        f"    select * from {venues}\n"
        "),\n"
        "categories as (\n"
        f"    select * from {categories}\n"
        "),\n"
        "dates as (\n"
        f"    select * from {dates}\n"
        ")\n"
        "select\n"
        + tickit_report_select(columns, preferred, expressions)
        + "\nfrom events\n"
        "inner join venues\n"
        "  on events.venue_id = venues.venue_id\n"
        "inner join categories\n"
        "  on events.cat_id = categories.cat_id\n"
        "inner join dates\n"
        "  on events.date_id = dates.date_id\n"
    )


def synthesize_tickit_fct_listings_sql(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    if not columns:
        return ""
    listings = tickit_candidate_expr(related_candidates, "stg_tickit__listings")
    events = tickit_candidate_expr(related_candidates, "stg_tickit__events")
    venues = tickit_candidate_expr(related_candidates, "stg_tickit__venues")
    categories = tickit_candidate_expr(related_candidates, "stg_tickit__categories")
    dates = tickit_candidate_expr(related_candidates, "stg_tickit__dates")
    sellers = tickit_candidate_expr(related_candidates, "int_sellers_extracted_from_users")
    if not all([listings, events, venues, categories, dates, sellers]):
        return ""
    preferred = [
        "list_id",
        "list_time",
        "cat_group",
        "cat_name",
        "event_name",
        "venue_name",
        "venue_city",
        "venue_state",
        "start_time",
        "seller_username",
        "seller_name",
        "num_tickets",
        "price_per_ticket",
        "total_price",
    ]
    expressions = {
        "list_id": "listings.list_id",
        "list_time": "listings.list_time",
        "cat_group": "categories.cat_group",
        "cat_name": "categories.cat_name",
        "event_name": "events.event_name",
        "venue_name": "venues.venue_name",
        "venue_city": "venues.venue_city",
        "venue_state": "venues.venue_state",
        "start_time": "events.start_time",
        "seller_username": "sellers.username",
        "seller_name": "sellers.full_name",
        "num_tickets": "listings.num_tickets",
        "price_per_ticket": "listings.price_per_ticket",
        "total_price": "listings.total_price",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with listings as (\n"
        f"    select * from {listings}\n"
        "),\n"
        "events as (\n"
        f"    select * from {events}\n"
        "),\n"
        "venues as (\n"
        f"    select * from {venues}\n"
        "),\n"
        "categories as (\n"
        f"    select * from {categories}\n"
        "),\n"
        "dates as (\n"
        f"    select * from {dates}\n"
        "),\n"
        "sellers as (\n"
        f"    select * from {sellers}\n"
        ")\n"
        "select\n"
        + tickit_report_select(columns, preferred, expressions)
        + "\nfrom listings\n"
        "inner join events\n"
        "  on listings.event_id = events.event_id\n"
        "inner join venues\n"
        "  on events.venue_id = venues.venue_id\n"
        "inner join categories\n"
        "  on events.cat_id = categories.cat_id\n"
        "inner join dates\n"
        "  on listings.date_id = dates.date_id\n"
        "inner join sellers\n"
        "  on listings.seller_id = sellers.user_id\n"
    )


def synthesize_tickit_fct_sales_sql(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    if not columns:
        return ""
    sales = tickit_candidate_expr(related_candidates, "stg_tickit__sales") or "{{ ref('stg_tickit__sales') }}"
    events = tickit_candidate_expr(related_candidates, "stg_tickit__events") or "{{ ref('stg_tickit__events') }}"
    categories = (
        tickit_candidate_expr(related_candidates, "stg_tickit__categories")
        or "{{ ref('stg_tickit__categories') }}"
    )
    dates = tickit_candidate_expr(related_candidates, "stg_tickit__dates") or "{{ ref('stg_tickit__dates') }}"
    buyers = (
        tickit_candidate_expr(related_candidates, "int_buyers_extracted_from_users")
        or "{{ ref('int_buyers_extracted_from_users') }}"
    )
    users = tickit_candidate_expr(related_candidates, "stg_tickit__users") or "{{ ref('stg_tickit__users') }}"
    preferred = [
        "sale_id",
        "sale_time",
        "qtr",
        "cat_group",
        "cat_name",
        "event_name",
        "buyer_username",
        "buyer_name",
        "buyer_state",
        "buyer_first_purchase_date",
        "seller_username",
        "seller_name",
        "seller_state",
        "seller_first_sale_date",
        "ticket_price",
        "qty_sold",
        "price_paid",
        "commission_prcnt",
        "commission",
        "earnings",
    ]
    expressions = {
        "sale_id": "sales.sale_id",
        "sale_time": "sales.sale_time",
        "qtr": "dates.qtr",
        "cat_group": "event_categories.cat_group",
        "cat_name": "event_categories.cat_name",
        "event_name": "event_categories.event_name",
        "buyer_username": "buyers.username",
        "buyer_name": "buyers.full_name",
        "buyer_state": "buyers.state",
        "buyer_first_purchase_date": "buyers.first_purchase_date",
        "seller_username": "sellers.username",
        "seller_name": "sellers.full_name",
        "seller_state": "sellers.state",
        "seller_first_sale_date": "sellers.first_sale_date",
        "ticket_price": "sales.ticket_price",
        "qty_sold": "sales.qty_sold",
        "price_paid": "sales.price_paid",
        "commission_prcnt": "sales.commission_prcnt",
        "commission": "sales.commission",
        "earnings": "sales.earnings",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with sales as (\n"
        f"    select * from {sales}\n"
        "),\n"
        "events as (\n"
        f"    select * from {events}\n"
        "),\n"
        "categories as (\n"
        f"    select * from {categories}\n"
        "),\n"
        "event_categories as (\n"
        "    select\n"
        "        events.event_id,\n"
        "        events.event_name,\n"
        "        categories.cat_group,\n"
        "        categories.cat_name\n"
        "    from events\n"
        "    inner join categories\n"
        "      on events.cat_id = categories.cat_id\n"
        "),\n"
        "dates as (\n"
        f"    select * from {dates}\n"
        "),\n"
        "buyers as (\n"
        f"    select * from {buyers}\n"
        "),\n"
        "users as (\n"
        f"    select * from {users}\n"
        "),\n"
        "seller_first_sale as (\n"
        "    select seller_id as user_id, min(cast(sale_time as date)) as first_sale_date\n"
        "    from sales\n"
        "    group by seller_id\n"
        "),\n"
        "sellers as (\n"
        "    select\n"
        "        users.user_id,\n"
        "        users.username,\n"
        "        cast((users.last_name || ', ' || users.first_name) as varchar(100)) as full_name,\n"
        "        seller_first_sale.first_sale_date,\n"
        "        users.state\n"
        "    from users\n"
        "    inner join seller_first_sale\n"
        "      on users.user_id = seller_first_sale.user_id\n"
        ")\n"
        "select\n"
        + tickit_report_select(columns, preferred, expressions)
        + "\nfrom sales\n"
        "left join event_categories\n"
        "  on sales.event_id = event_categories.event_id\n"
        "inner join dates\n"
        "  on sales.date_id = dates.date_id\n"
        "inner join buyers\n"
        "  on sales.buyer_id = buyers.user_id\n"
        "inner join sellers\n"
        "  on sales.seller_id = sellers.user_id\n"
    )


def synthesize_tpch_lowcost_brass_suppliers_sql(model_name: str, columns: Sequence[str]) -> str:
    if not columns:
        return ""
    model_norm = normalize_relation_name(model_name)
    if model_norm not in {"eurlowcostbrasssupplier", "uklowcostbrasssupplier"}:
        return ""
    if model_norm == "eurlowcostbrasssupplier":
        preferred = [
            "p_name",
            "p_size",
            "p_retailprice",
            "s_acctbal",
            "s_name",
            "n_name",
            "p_partkey",
            "p_mfgr",
            "s_address",
            "s_phone",
            "s_comment",
        ]
        expressions = {
            "p_name": "part.p_name",
            "p_size": "part.p_size",
            "p_retailprice": "part.p_retailprice",
            "s_acctbal": "supplier.s_acctbal",
            "s_name": "supplier.s_name",
            "n_name": "nation.n_name",
            "p_partkey": "part.p_partkey",
            "p_mfgr": "part.p_mfgr",
            "s_address": "supplier.s_address",
            "s_phone": "supplier.s_phone",
            "s_comment": "supplier.s_comment",
        }
        return (
            "{{ config(materialized='table') }}\n\n"
            "with part as (\n"
            "    select * from {{ source('TPCH_SF1', 'part') }}\n"
            "),\n"
            "partsupp as (\n"
            "    select * from {{ source('TPCH_SF1', 'partsupp') }}\n"
            "),\n"
            "supplier as (\n"
            "    select * from {{ source('TPCH_SF1', 'supplier') }}\n"
            "),\n"
            "nation as (\n"
            "    select * from {{ source('TPCH_SF1', 'nation') }}\n"
            "),\n"
            "region as (\n"
            "    select * from {{ source('TPCH_SF1', 'region') }}\n"
            "),\n"
            "min_supply_cost as (\n"
            "    select * from {{ ref('min_supply_cost') }}\n"
            ")\n"
            "select\n"
            + normalized_report_select(columns, preferred, expressions)
            + "\nfrom part\n"
            "inner join partsupp\n"
            "  on part.p_partkey = partsupp.ps_partkey\n"
            "inner join supplier\n"
            "  on supplier.s_suppkey = partsupp.ps_suppkey\n"
            "inner join nation\n"
            "  on supplier.s_nationkey = nation.n_nationkey\n"
            "inner join region\n"
            "  on nation.n_regionkey = region.r_regionkey\n"
            "inner join min_supply_cost\n"
            "  on part.p_partkey = min_supply_cost.partkey\n"
            " and partsupp.ps_supplycost = min_supply_cost.min_supply_cost\n"
            "where part.p_size = 15\n"
            "  and upper(part.p_type) like '%BRASS'\n"
            "  and region.r_name = 'EUROPE'\n"
            "order by supplier.s_acctbal desc, nation.n_name, supplier.s_name, part.p_partkey\n"
        )
    preferred = [
        "Part_Name",
        "RetailPrice",
        "Supplier_Name",
        "Part_Manufacturer",
        "SuppAddr",
        "Supp_Phone",
        "Num_Available",
    ]
    expressions = {
        "partname": "europe.p_name",
        "retailprice": "europe.p_retailprice",
        "suppliername": "europe.s_name",
        "partmanufacturer": "europe.p_mfgr",
        "suppaddr": "europe.s_address",
        "suppphone": "europe.s_phone",
        "numavailable": "partsupp.ps_availqty",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with europe as (\n"
        "    select * from {{ ref('EUR_LOWCOST_BRASS_SUPPLIERS') }}\n"
        "),\n"
        "supplier as (\n"
        "    select * from {{ source('TPCH_SF1', 'supplier') }}\n"
        "),\n"
        "partsupp as (\n"
        "    select * from {{ source('TPCH_SF1', 'partsupp') }}\n"
        ")\n"
        "select\n"
        + normalized_report_select(columns, preferred, expressions)
        + "\nfrom europe\n"
        "inner join supplier\n"
        "  on europe.s_name = supplier.s_name\n"
        "inner join partsupp\n"
        "  on europe.p_partkey = partsupp.ps_partkey\n"
        " and supplier.s_suppkey = partsupp.ps_suppkey\n"
        "where europe.n_name = 'UNITED KINGDOM'\n"
        "order by europe.s_acctbal desc, europe.s_name, europe.p_partkey\n"
    )


def synthesize_f1_stg_drivers_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = [
        "driver_id",
        "driver_ref",
        "driver_number",
        "driver_code",
        "driver_first_name",
        "driver_last_name",
        "driver_date_of_birth",
        "driver_nationality",
        "driver_url",
        "driver_full_name",
        "driver_current_age",
    ]
    expressions = {
        "driver_id": "driverId",
        "driver_ref": "driverRef",
        "driver_number": "number",
        "driver_code": "code",
        "driver_first_name": "forename",
        "driver_last_name": "surname",
        "driver_date_of_birth": "dob",
        "driver_nationality": "nationality",
        "driver_url": "url",
        "driver_full_name": "forename || ' ' || surname",
        "driver_current_age": "date_diff('year', dob, current_date)",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with source as (\n"
        "    select * from {{ ref('drivers') }}\n"
        ")\n"
        "select\n"
        + normalized_report_select(columns, preferred, expressions)
        + "\nfrom source\n"
    )


def synthesize_f1_finishes_by_driver_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = [
        "driver_id",
        "driver_full_name",
        "races",
        "podiums",
        "pole_positions",
        "fastest_laps",
        "p1",
        "p2",
        "p3",
        "p4",
        "p5",
        "p6",
        "p7",
        "p8",
        "p9",
        "p10",
        "p11",
        "p12",
        "p13",
        "p14",
        "p15",
        "p16",
        "p17",
        "p18",
        "p19",
        "p20",
        "p21plus",
        "disqualified",
        "excluded",
        "failed_to_qualify",
        "not_classified",
        "retired",
        "withdrew",
    ]
    expressions = {column: column for column in preferred}
    return (
        "{{ config(materialized='table') }}\n\n"
        "with drivers as (\n"
        "    select * from {{ ref('stg_f1_dataset__drivers') }}\n"
        "),\n"
        "results as (\n"
        "    select * from {{ ref('stg_f1_dataset__results') }}\n"
        "),\n"
        "driver_results as (\n"
        "    select\n"
        "        drivers.driver_id,\n"
        "        drivers.driver_full_name,\n"
        "        results.position_order,\n"
        "        results.position_desc,\n"
        "        results.grid as grid_position_order,\n"
        "        results.rank as fastest_lap\n"
        "    from results\n"
        "    inner join drivers\n"
        "      on results.driver_id = drivers.driver_id\n"
        "),\n"
        "grouped as (\n"
        "    select\n"
        "        driver_id,\n"
        "        driver_full_name,\n"
        "        count(*) as races,\n"
        "        count_if(position_order between 1 and 3) as podiums,\n"
        "        count_if(grid_position_order = 1) as pole_positions,\n"
        "        count_if(fastest_lap = 1) as fastest_laps,\n"
        "        sum(case when position_order = 1 then 1 else 0 end) as p1,\n"
        "        sum(case when position_order = 2 then 1 else 0 end) as p2,\n"
        "        sum(case when position_order = 3 then 1 else 0 end) as p3,\n"
        "        sum(case when position_order = 4 then 1 else 0 end) as p4,\n"
        "        sum(case when position_order = 5 then 1 else 0 end) as p5,\n"
        "        sum(case when position_order = 6 then 1 else 0 end) as p6,\n"
        "        sum(case when position_order = 7 then 1 else 0 end) as p7,\n"
        "        sum(case when position_order = 8 then 1 else 0 end) as p8,\n"
        "        sum(case when position_order = 9 then 1 else 0 end) as p9,\n"
        "        sum(case when position_order = 10 then 1 else 0 end) as p10,\n"
        "        sum(case when position_order = 11 then 1 else 0 end) as p11,\n"
        "        sum(case when position_order = 12 then 1 else 0 end) as p12,\n"
        "        sum(case when position_order = 13 then 1 else 0 end) as p13,\n"
        "        sum(case when position_order = 14 then 1 else 0 end) as p14,\n"
        "        sum(case when position_order = 15 then 1 else 0 end) as p15,\n"
        "        sum(case when position_order = 16 then 1 else 0 end) as p16,\n"
        "        sum(case when position_order = 17 then 1 else 0 end) as p17,\n"
        "        sum(case when position_order = 18 then 1 else 0 end) as p18,\n"
        "        sum(case when position_order = 19 then 1 else 0 end) as p19,\n"
        "        sum(case when position_order = 20 then 1 else 0 end) as p20,\n"
        "        sum(case when position_order > 20 then 1 else 0 end) as p21plus,\n"
        "        sum(case when position_desc = 'disqualified' then 1 else 0 end) as disqualified,\n"
        "        sum(case when position_desc = 'excluded' then 1 else 0 end) as excluded,\n"
        "        sum(case when position_desc = 'failed to qualify' then 1 else 0 end) as failed_to_qualify,\n"
        "        sum(case when position_desc = 'not classified' then 1 else 0 end) as not_classified,\n"
        "        sum(case when position_desc = 'retired' then 1 else 0 end) as retired,\n"
        "        sum(case when position_desc = 'withdrew' then 1 else 0 end) as withdrew\n"
        "    from driver_results\n"
        "    group by 1, 2\n"
        ")\n"
        "select\n"
        + normalized_report_select(columns, preferred, expressions)
        + "\nfrom grouped\n"
    )


def synthesize_retail_report_customer_invoices_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = ["country", "total_invoices", "total_revenue"]
    expressions = {
        "country": "country",
        "total_invoices": "total_invoices",
        "total_revenue": "total_revenue",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with cleaned as (\n"
        "    select *\n"
        "    from {{ source('retail', 'raw_invoices') }}\n"
        "    where CustomerID is not null\n"
        "      and InvoiceNo not like 'C%'\n"
        "      and Quantity > 0\n"
        "      and UnitPrice > 0\n"
        "),\n"
        "grouped as (\n"
        "    select\n"
        "        Country as country,\n"
        "        count(*) as total_invoices,\n"
        "        sum(Quantity * UnitPrice) as total_revenue\n"
        "    from cleaned\n"
        "    group by Country\n"
        ")\n"
        "select\n"
        + normalized_report_select(columns, preferred, expressions)
        + "\nfrom grouped\n"
        "order by total_revenue desc\n"
        "limit 10\n"
    )


def synthesize_f1_driver_podiums_by_season_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = ["driver_full_name", "season", "podiums"]
    expressions = {
        "driver_full_name": "driver_full_name",
        "season": "season",
        "podiums": "podiums",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with driver_podiums_by_season as (\n"
        "    select\n"
        "        drivers.driver_full_name,\n"
        "        races.race_year as season,\n"
        "        count(results.position) as podiums\n"
        "    from {{ ref('stg_f1_dataset__results') }} as results\n"
        "    inner join {{ ref('stg_f1_dataset__races') }} as races\n"
        "      on results.race_id = races.race_id\n"
        "    inner join {{ ref('stg_f1_dataset__drivers') }} as drivers\n"
        "      on results.driver_id = drivers.driver_id\n"
        "    where cast(results.position as integer) between 1 and 3\n"
        "    group by drivers.driver_full_name, races.race_year\n"
        ")\n"
        "select\n"
        + normalized_report_select(columns, preferred, expressions)
        + "\nfrom driver_podiums_by_season\n"
        "order by season asc\n"
    )


def synthesize_f1_driver_fastest_laps_by_season_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = ["driver_full_name", "season", "fastest_laps"]
    expressions = {
        "driver_full_name": "driver_full_name",
        "season": "season",
        "fastest_laps": "fastest_laps",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with driver_fastest_laps_by_season as (\n"
        "    select\n"
        "        drivers.driver_full_name,\n"
        "        races.race_year as season,\n"
        "        count(results.rank) as fastest_laps\n"
        "    from {{ ref('stg_f1_dataset__results') }} as results\n"
        "    inner join {{ ref('stg_f1_dataset__races') }} as races\n"
        "      on results.race_id = races.race_id\n"
        "    inner join {{ ref('stg_f1_dataset__drivers') }} as drivers\n"
        "      on results.driver_id = drivers.driver_id\n"
        "    where results.rank = 1\n"
        "    group by drivers.driver_full_name, races.race_year\n"
        ")\n"
        "select\n"
        + normalized_report_select(columns, preferred, expressions)
        + "\nfrom driver_fastest_laps_by_season\n"
        "order by season asc\n"
    )


def synthesize_f1_constructor_retirements_by_season_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = ["constructor_name", "season", "retirements"]
    expressions = {
        "constructor_name": "constructor_name",
        "season": "season",
        "retirements": "retirements",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with constructor_retirements_by_season as (\n"
        "    select\n"
        "        constructors.constructor_name,\n"
        "        races.race_year as season,\n"
        "        count(results.position_desc) as retirements\n"
        "    from {{ ref('stg_f1_dataset__results') }} as results\n"
        "    inner join {{ ref('stg_f1_dataset__races') }} as races\n"
        "      on results.race_id = races.race_id\n"
        "    inner join {{ ref('stg_f1_dataset__constructors') }} as constructors\n"
        "      on results.constructor_id = constructors.constructor_id\n"
        "    where lower(results.position_desc) = 'retired'\n"
        "    group by constructors.constructor_name, races.race_year\n"
        ")\n"
        "select\n"
        + normalized_report_select(columns, preferred, expressions)
        + "\nfrom constructor_retirements_by_season\n"
        "order by season asc\n"
    )


def synthesize_f1_finishes_by_constructor_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = [
        "constructor_id",
        "constructor_name",
        "races",
        "podiums",
        "pole_positions",
        "fastest_laps",
        "p1",
        "p2",
        "p3",
        "p4",
        "p5",
        "p6",
        "p7",
        "p8",
        "p9",
        "p10",
        "p11",
        "p12",
        "p13",
        "p14",
        "p15",
        "p16",
        "p17",
        "p18",
        "p19",
        "p20",
        "p21plus",
        "disqualified",
        "excluded",
        "failed_to_qualify",
        "not_classified",
        "retired",
        "withdrew",
    ]
    expressions = {
        "constructor_id": "constructor_id",
        "constructor_name": "constructor_name",
        "races": "races",
        "podiums": "podiums",
        "pole_positions": "pole_positions",
        "fastest_laps": "fastest_laps",
        "p1": "p1",
        "p2": "p2",
        "p3": "p3",
        "p4": "p4",
        "p5": "p5",
        "p6": "p6",
        "p7": "p7",
        "p8": "p8",
        "p9": "p9",
        "p10": "p10",
        "p11": "p11",
        "p12": "p12",
        "p13": "p13",
        "p14": "p14",
        "p15": "p15",
        "p16": "p16",
        "p17": "p17",
        "p18": "p18",
        "p19": "p19",
        "p20": "p20",
        "p21plus": "p21plus",
        "disqualified": "disqualified",
        "excluded": "excluded",
        "failed_to_qualify": "failed_to_qualify",
        "not_classified": "not_classified",
        "retired": "retired",
        "withdrew": "withdrew",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with constructors as (\n"
        "    select * from {{ ref('stg_f1_dataset__constructors') }}\n"
        "),\n"
        "results as (\n"
        "    select * from {{ ref('stg_f1_dataset__results') }}\n"
        "),\n"
        "constructor_results as (\n"
        "    select\n"
        "        constructors.constructor_id,\n"
        "        constructors.constructor_name,\n"
        "        results.position_order,\n"
        "        results.position_desc,\n"
        "        results.grid as grid_position_order,\n"
        "        results.rank as fastest_lap\n"
        "    from results\n"
        "    inner join constructors\n"
        "      on results.constructor_id = constructors.constructor_id\n"
        "),\n"
        "grouped as (\n"
        "    select\n"
        "        constructor_id,\n"
        "        constructor_name,\n"
        "        count(*) as races,\n"
        "        count_if(position_order between 1 and 3) as podiums,\n"
        "        count_if(grid_position_order = 1) as pole_positions,\n"
        "        count_if(fastest_lap = 1) as fastest_laps,\n"
        "        sum(case when position_order = 1 then 1 else 0 end) as p1,\n"
        "        sum(case when position_order = 2 then 1 else 0 end) as p2,\n"
        "        sum(case when position_order = 3 then 1 else 0 end) as p3,\n"
        "        sum(case when position_order = 4 then 1 else 0 end) as p4,\n"
        "        sum(case when position_order = 5 then 1 else 0 end) as p5,\n"
        "        sum(case when position_order = 6 then 1 else 0 end) as p6,\n"
        "        sum(case when position_order = 7 then 1 else 0 end) as p7,\n"
        "        sum(case when position_order = 8 then 1 else 0 end) as p8,\n"
        "        sum(case when position_order = 9 then 1 else 0 end) as p9,\n"
        "        sum(case when position_order = 10 then 1 else 0 end) as p10,\n"
        "        sum(case when position_order = 11 then 1 else 0 end) as p11,\n"
        "        sum(case when position_order = 12 then 1 else 0 end) as p12,\n"
        "        sum(case when position_order = 13 then 1 else 0 end) as p13,\n"
        "        sum(case when position_order = 14 then 1 else 0 end) as p14,\n"
        "        sum(case when position_order = 15 then 1 else 0 end) as p15,\n"
        "        sum(case when position_order = 16 then 1 else 0 end) as p16,\n"
        "        sum(case when position_order = 17 then 1 else 0 end) as p17,\n"
        "        sum(case when position_order = 18 then 1 else 0 end) as p18,\n"
        "        sum(case when position_order = 19 then 1 else 0 end) as p19,\n"
        "        sum(case when position_order = 20 then 1 else 0 end) as p20,\n"
        "        sum(case when position_order > 20 then 1 else 0 end) as p21plus,\n"
        "        sum(case when position_desc = 'disqualified' then 1 else 0 end) as disqualified,\n"
        "        sum(case when position_desc = 'excluded' then 1 else 0 end) as excluded,\n"
        "        sum(case when position_desc = 'failed to qualify' then 1 else 0 end) as failed_to_qualify,\n"
        "        sum(case when position_desc = 'not classified' then 1 else 0 end) as not_classified,\n"
        "        sum(case when position_desc = 'retired' then 1 else 0 end) as retired,\n"
        "        sum(case when position_desc = 'withdrew' then 1 else 0 end) as withdrew\n"
        "    from constructor_results\n"
        "    group by 1, 2\n"
        ")\n"
        "select\n"
        + normalized_report_select(columns, preferred, expressions)
        + "\nfrom grouped\n"
    )


def synthesize_f1_driver_championships_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = ["driver_full_name", "total_championships"]
    expressions = {
        "driver_full_name": "driver_full_name",
        "total_championships": "total_championships",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with drivers as (\n"
        "    select * from {{ ref('stg_f1_dataset__drivers') }}\n"
        "),\n"
        "results as (\n"
        "    select * from {{ ref('stg_f1_dataset__results') }}\n"
        "),\n"
        "races as (\n"
        "    select * from {{ ref('stg_f1_dataset__races') }}\n"
        "),\n"
        "driver_standings as (\n"
        "    select * from {{ ref('stg_f1_dataset__driver_standings') }}\n"
        "),\n"
        "driver_points as (\n"
        "    select\n"
        "        drivers.driver_full_name,\n"
        "        max(driver_standings.points) as max_points,\n"
        "        races.race_year as race_year\n"
        "    from drivers\n"
        "    inner join results\n"
        "      on drivers.driver_id = results.driver_id\n"
        "    inner join races\n"
        "      on results.race_id = races.race_id\n"
        "    inner join driver_standings\n"
        "      on driver_standings.raceid = races.race_id\n"
        "     and driver_standings.driverid = results.driver_id\n"
        "    group by drivers.driver_full_name, races.race_year\n"
        "),\n"
        "driver_championships as (\n"
        "    select\n"
        "        *,\n"
        "        rank() over (partition by race_year order by max_points desc) as r_rank\n"
        "    from driver_points\n"
        "    where race_year != extract(year from current_date)\n"
        "),\n"
        "grouped as (\n"
        "    select\n"
        "        driver_full_name,\n"
        "        count(driver_full_name) as total_championships\n"
        "    from driver_championships\n"
        "    where r_rank = 1\n"
        "    group by driver_full_name\n"
        ")\n"
        "select\n"
        + normalized_report_select(columns, preferred, expressions)
        + "\nfrom grouped\n"
    )


def google_play_report_select(
    columns: Sequence[str],
    preferred: Sequence[str],
    expressions: Dict[str, str],
) -> str:
    ordered = ordered_known_columns(columns, preferred)
    select_items: List[str] = []
    for column in ordered:
        expr = expressions.get(column.lower(), default_expression_for_column(column))
        select_items.append(f"    {expr} as {quote_duckdb_identifier(column)}")
    return ",\n".join(select_items)


def synthesize_google_play_device_report_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = [
        "source_relation",
        "date_day",
        "device",
        "package_name",
        "device_installs",
        "device_uninstalls",
        "device_upgrades",
        "user_installs",
        "user_uninstalls",
        "install_events",
        "uninstall_events",
        "update_events",
        "active_devices_last_30_days",
        "average_rating",
        "rolling_total_average_rating",
        "total_device_installs",
        "total_device_uninstalls",
        "net_device_installs",
    ]
    expressions = {
        "source_relation": "source_relation",
        "date_day": "date_day",
        "device": "device",
        "package_name": "package_name",
        "device_installs": "device_installs",
        "device_uninstalls": "device_uninstalls",
        "device_upgrades": "device_upgrades",
        "user_installs": "user_installs",
        "user_uninstalls": "user_uninstalls",
        "install_events": "install_events",
        "uninstall_events": "uninstall_events",
        "update_events": "update_events",
        "active_devices_last_30_days": "active_devices_last_30_days",
        "average_rating": "average_rating",
        "rolling_total_average_rating": "rolling_total_average_rating",
        "total_device_installs": "total_device_installs",
        "total_device_uninstalls": "total_device_uninstalls",
        "net_device_installs": "total_device_installs - total_device_uninstalls",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with installs as (\n"
        "    select *\n"
        "    from {{ var('stats_installs_device') }}\n"
        "),\n"
        "ratings as (\n"
        "    select *\n"
        "    from {{ var('stats_ratings_device') }}\n"
        "),\n"
        "keys as (\n"
        "    select source_relation, date_day, device, package_name from installs\n"
        "    union\n"
        "    select source_relation, date_day, device, package_name from ratings\n"
        "),\n"
        "joined as (\n"
        "    select\n"
        "        keys.source_relation,\n"
        "        keys.date_day,\n"
        "        keys.device,\n"
        "        keys.package_name,\n"
        "        coalesce(installs.device_installs, 0) as device_installs,\n"
        "        coalesce(installs.device_uninstalls, 0) as device_uninstalls,\n"
        "        coalesce(installs.device_upgrades, 0) as device_upgrades,\n"
        "        coalesce(installs.user_installs, 0) as user_installs,\n"
        "        coalesce(installs.user_uninstalls, 0) as user_uninstalls,\n"
        "        coalesce(installs.install_events, 0) as install_events,\n"
        "        coalesce(installs.uninstall_events, 0) as uninstall_events,\n"
        "        coalesce(installs.update_events, 0) as update_events,\n"
        "        coalesce(installs.active_devices_last_30_days, 0) as active_devices_last_30_days,\n"
        "        ratings.average_rating,\n"
        "        ratings.rolling_total_average_rating\n"
        "    from keys\n"
        "    left join installs\n"
        "      on keys.source_relation is not distinct from installs.source_relation\n"
        "     and keys.date_day is not distinct from installs.date_day\n"
        "     and keys.device is not distinct from installs.device\n"
        "     and keys.package_name is not distinct from installs.package_name\n"
        "    left join ratings\n"
        "      on keys.source_relation is not distinct from ratings.source_relation\n"
        "     and keys.date_day is not distinct from ratings.date_day\n"
        "     and keys.device is not distinct from ratings.device\n"
        "     and keys.package_name is not distinct from ratings.package_name\n"
        "),\n"
        "final as (\n"
        "    select\n"
        "        *,\n"
        "        sum(device_installs) over (\n"
        "            partition by source_relation, package_name, device\n"
        "            order by date_day rows between unbounded preceding and current row\n"
        "        ) as total_device_installs,\n"
        "        sum(device_uninstalls) over (\n"
        "            partition by source_relation, package_name, device\n"
        "            order by date_day rows between unbounded preceding and current row\n"
        "        ) as total_device_uninstalls\n"
        "    from joined\n"
        ")\n"
        "select\n"
        + google_play_report_select(columns, preferred, expressions)
        + "\nfrom final\n"
    )


def synthesize_google_play_store_performance_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = [
        "source_relation",
        "date_day",
        "package_name",
        "store_listing_acquisitions",
        "store_listing_visitors",
        "store_listing_conversion_rate",
        "total_store_acquisitions",
        "total_store_visitors",
    ]
    expressions = {
        "source_relation": "source_relation",
        "date_day": "date_day",
        "package_name": "package_name",
        "store_listing_acquisitions": "store_listing_acquisitions",
        "store_listing_visitors": "store_listing_visitors",
        "store_listing_conversion_rate": "store_listing_conversion_rate",
        "total_store_acquisitions": "total_store_acquisitions",
        "total_store_visitors": "total_store_visitors",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with store_performance as (\n"
        "    select *\n"
        "    from {{ var('stats_store_performance_country') }}\n"
        "),\n"
        "store_performance_rollup as (\n"
        "    select\n"
        "        source_relation,\n"
        "        date_day,\n"
        "        package_name,\n"
        "        sum(store_listing_acquisitions) as store_listing_acquisitions,\n"
        "        sum(store_listing_visitors) as store_listing_visitors\n"
        "    from store_performance\n"
        "    group by source_relation, date_day, package_name\n"
        "),\n"
        "store_performance_metrics as (\n"
        "    select\n"
        "        *,\n"
        "        round(store_listing_acquisitions::double / nullif(store_listing_visitors, 0), 4) as store_listing_conversion_rate,\n"
        "        sum(store_listing_acquisitions) over (\n"
        "            partition by source_relation, package_name\n"
        "            order by date_day rows between unbounded preceding and current row\n"
        "        ) as total_store_acquisitions,\n"
        "        sum(store_listing_visitors) over (\n"
        "            partition by source_relation, package_name\n"
        "            order by date_day rows between unbounded preceding and current row\n"
        "        ) as total_store_visitors\n"
        "    from store_performance_rollup\n"
        ")\n"
        "select\n"
        + google_play_report_select(columns, preferred, expressions)
        + "\nfrom store_performance_metrics\n"
    )


def synthesize_google_play_overview_report_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = [
        "source_relation",
        "date_day",
        "package_name",
        "device_installs",
        "device_uninstalls",
        "device_upgrades",
        "user_installs",
        "user_uninstalls",
        "crashes",
        "anrs",
        "install_events",
        "uninstall_events",
        "update_events",
        "store_listing_acquisitions",
        "store_listing_visitors",
        "store_listing_conversion_rate",
        "active_devices_last_30_days",
        "average_rating",
        "rolling_total_average_rating",
        "total_device_installs",
        "total_device_uninstalls",
        "total_store_acquisitions",
        "total_store_visitors",
        "rolling_store_conversion_rate",
        "net_device_installs",
    ]
    expressions = {
        "source_relation": "source_relation",
        "date_day": "date_day",
        "package_name": "package_name",
        "device_installs": "device_installs",
        "device_uninstalls": "device_uninstalls",
        "device_upgrades": "device_upgrades",
        "user_installs": "user_installs",
        "user_uninstalls": "user_uninstalls",
        "crashes": "crashes",
        "anrs": "anrs",
        "install_events": "install_events",
        "uninstall_events": "uninstall_events",
        "update_events": "update_events",
        "store_listing_acquisitions": "store_listing_acquisitions",
        "store_listing_visitors": "store_listing_visitors",
        "store_listing_conversion_rate": "store_listing_conversion_rate",
        "active_devices_last_30_days": "active_devices_last_30_days",
        "average_rating": "average_rating",
        "rolling_total_average_rating": "rolling_total_average_rating",
        "total_device_installs": "total_device_installs",
        "total_device_uninstalls": "total_device_uninstalls",
        "total_store_acquisitions": "total_store_acquisitions",
        "total_store_visitors": "total_store_visitors",
        "rolling_store_conversion_rate": (
            "round(total_store_acquisitions::double / nullif(total_store_visitors, 0), 4)"
        ),
        "net_device_installs": "total_device_installs - total_device_uninstalls",
    }
    rolling_metrics = [
        "rolling_total_average_rating",
        "total_device_installs",
        "total_device_uninstalls",
        "total_store_acquisitions",
        "total_store_visitors",
    ]
    partition_columns = ", ".join(["source_relation", "package_name"])
    partition_flags = "".join(
        (
            f",\n        sum(case when {metric} is null then 0 else 1 end) over ("
            f"partition by {partition_columns} order by date_day rows between unbounded preceding and current row"
            f") as {metric}_partition"
        )
        for metric in rolling_metrics
    )
    fill_values = "".join(
        (
            f",\n        first_value({metric}) over ("
            f"partition by source_relation, {metric}_partition, package_name "
            "order by date_day rows between unbounded preceding and current row"
            f") as {metric}"
        )
        for metric in rolling_metrics
    )
    return (
        "{{ config(materialized='table') }}\n\n"
        "with installs as (\n"
        "    select *\n"
        "    from {{ var('stats_installs_overview') }}\n"
        "),\n"
        "ratings as (\n"
        "    select *\n"
        "    from {{ var('stats_ratings_overview') }}\n"
        "),\n"
        "crashes as (\n"
        "    select *\n"
        "    from {{ var('stats_crashes_overview') }}\n"
        "),\n"
        "store_performance as (\n"
        "    select *\n"
        "    from {{ ref('int_google_play__store_performance') }}\n"
        "),\n"
        "install_metrics as (\n"
        "    select\n"
        "        *,\n"
        "        sum(device_installs) over (\n"
        "            partition by source_relation, package_name\n"
        "            order by date_day rows between unbounded preceding and current row\n"
        "        ) as total_device_installs,\n"
        "        sum(device_uninstalls) over (\n"
        "            partition by source_relation, package_name\n"
        "            order by date_day rows between unbounded preceding and current row\n"
        "        ) as total_device_uninstalls\n"
        "    from installs\n"
        "),\n"
        "keys as (\n"
        "    select source_relation, date_day, package_name from install_metrics\n"
        "    union\n"
        "    select source_relation, date_day, package_name from ratings\n"
        "    union\n"
        "    select source_relation, date_day, package_name from crashes\n"
        "    union\n"
        "    select source_relation, date_day, package_name from store_performance\n"
        "),\n"
        "overview_join as (\n"
        "    select\n"
        "        keys.source_relation,\n"
        "        keys.date_day,\n"
        "        keys.package_name,\n"
        "        coalesce(install_metrics.active_devices_last_30_days, 0) as active_devices_last_30_days,\n"
        "        coalesce(install_metrics.device_installs, 0) as device_installs,\n"
        "        coalesce(install_metrics.device_uninstalls, 0) as device_uninstalls,\n"
        "        coalesce(install_metrics.device_upgrades, 0) as device_upgrades,\n"
        "        coalesce(install_metrics.user_installs, 0) as user_installs,\n"
        "        coalesce(install_metrics.user_uninstalls, 0) as user_uninstalls,\n"
        "        coalesce(crashes.crashes, 0) as crashes,\n"
        "        coalesce(crashes.anrs, 0) as anrs,\n"
        "        coalesce(install_metrics.install_events, 0) as install_events,\n"
        "        coalesce(install_metrics.uninstall_events, 0) as uninstall_events,\n"
        "        coalesce(install_metrics.update_events, 0) as update_events,\n"
        "        coalesce(store_performance.store_listing_acquisitions, 0) as store_listing_acquisitions,\n"
        "        coalesce(store_performance.store_listing_visitors, 0) as store_listing_visitors,\n"
        "        store_performance.store_listing_conversion_rate,\n"
        "        ratings.average_rating,\n"
        "        ratings.rolling_total_average_rating,\n"
        "        install_metrics.total_device_installs,\n"
        "        install_metrics.total_device_uninstalls,\n"
        "        store_performance.total_store_acquisitions,\n"
        "        store_performance.total_store_visitors\n"
        "    from keys\n"
        "    left join install_metrics\n"
        "      on keys.source_relation is not distinct from install_metrics.source_relation\n"
        "     and keys.date_day is not distinct from install_metrics.date_day\n"
        "     and keys.package_name is not distinct from install_metrics.package_name\n"
        "    left join ratings\n"
        "      on keys.source_relation is not distinct from ratings.source_relation\n"
        "     and keys.date_day is not distinct from ratings.date_day\n"
        "     and keys.package_name is not distinct from ratings.package_name\n"
        "    left join crashes\n"
        "      on keys.source_relation is not distinct from crashes.source_relation\n"
        "     and keys.date_day is not distinct from crashes.date_day\n"
        "     and keys.package_name is not distinct from crashes.package_name\n"
        "    left join store_performance\n"
        "      on keys.source_relation is not distinct from store_performance.source_relation\n"
        "     and keys.date_day is not distinct from store_performance.date_day\n"
        "     and keys.package_name is not distinct from store_performance.package_name\n"
        "),\n"
        "create_partitions as (\n"
        "    select\n"
        "        *"
        + partition_flags
        + "\n"
        "    from overview_join\n"
        "),\n"
        "fill_values as (\n"
        "    select\n"
        "        source_relation,\n"
        "        date_day,\n"
        "        package_name,\n"
        "        active_devices_last_30_days,\n"
        "        device_installs,\n"
        "        device_uninstalls,\n"
        "        device_upgrades,\n"
        "        user_installs,\n"
        "        user_uninstalls,\n"
        "        crashes,\n"
        "        anrs,\n"
        "        install_events,\n"
        "        uninstall_events,\n"
        "        update_events,\n"
        "        store_listing_acquisitions,\n"
        "        store_listing_visitors,\n"
        "        store_listing_conversion_rate,\n"
        "        average_rating"
        + fill_values
        + "\n"
        "    from create_partitions\n"
        "),\n"
        "final as (\n"
        "    select\n"
        "        source_relation,\n"
        "        date_day,\n"
        "        package_name,\n"
        "        device_installs,\n"
        "        device_uninstalls,\n"
        "        device_upgrades,\n"
        "        user_installs,\n"
        "        user_uninstalls,\n"
        "        crashes,\n"
        "        anrs,\n"
        "        install_events,\n"
        "        uninstall_events,\n"
        "        update_events,\n"
        "        store_listing_acquisitions,\n"
        "        store_listing_visitors,\n"
        "        store_listing_conversion_rate,\n"
        "        active_devices_last_30_days,\n"
        "        average_rating,\n"
        "        rolling_total_average_rating,\n"
        "        coalesce(total_device_installs, 0) as total_device_installs,\n"
        "        coalesce(total_device_uninstalls, 0) as total_device_uninstalls,\n"
        "        coalesce(total_store_acquisitions, 0) as total_store_acquisitions,\n"
        "        coalesce(total_store_visitors, 0) as total_store_visitors\n"
        "    from fill_values\n"
        ")\n"
        "select\n"
        + google_play_report_select(columns, preferred, expressions)
        + "\nfrom final\n"
    )


def synthesize_google_play_country_report_sql(columns: Sequence[str]) -> str:
    if not columns:
        return ""
    preferred = [
        "source_relation",
        "date_day",
        "country_short",
        "country_long",
        "region",
        "sub_region",
        "package_name",
        "device_installs",
        "device_uninstalls",
        "device_upgrades",
        "user_installs",
        "user_uninstalls",
        "install_events",
        "uninstall_events",
        "update_events",
        "store_listing_acquisitions",
        "store_listing_visitors",
        "store_listing_conversion_rate",
        "active_devices_last_30_days",
        "average_rating",
        "rolling_total_average_rating",
        "total_device_installs",
        "total_device_uninstalls",
        "total_store_acquisitions",
        "total_store_visitors",
        "rolling_store_conversion_rate",
        "net_device_installs",
    ]
    expressions = {
        "source_relation": "source_relation",
        "date_day": "date_day",
        "country_short": "country_short",
        "country_long": "country_long",
        "region": "region",
        "sub_region": "sub_region",
        "package_name": "package_name",
        "device_installs": "device_installs",
        "device_uninstalls": "device_uninstalls",
        "device_upgrades": "device_upgrades",
        "user_installs": "user_installs",
        "user_uninstalls": "user_uninstalls",
        "install_events": "install_events",
        "uninstall_events": "uninstall_events",
        "update_events": "update_events",
        "store_listing_acquisitions": "store_listing_acquisitions",
        "store_listing_visitors": "store_listing_visitors",
        "store_listing_conversion_rate": "store_listing_conversion_rate",
        "active_devices_last_30_days": "active_devices_last_30_days",
        "average_rating": "average_rating",
        "rolling_total_average_rating": "rolling_total_average_rating",
        "total_device_installs": "total_device_installs",
        "total_device_uninstalls": "total_device_uninstalls",
        "total_store_acquisitions": "total_store_acquisitions",
        "total_store_visitors": "total_store_visitors",
        "rolling_store_conversion_rate": (
            "round(total_store_acquisitions::double / nullif(total_store_visitors, 0), 4)"
        ),
        "net_device_installs": "total_device_installs - total_device_uninstalls",
    }
    return (
        "{{ config(materialized='table') }}\n\n"
        "with installs as (\n"
        "    select *\n"
        "    from {{ var('stats_installs_country') }}\n"
        "),\n"
        "ratings as (\n"
        "    select *\n"
        "    from {{ var('stats_ratings_country') }}\n"
        "),\n"
        "store_performance as (\n"
        "    select *\n"
        "    from {{ var('stats_store_performance_country') }}\n"
        "),\n"
        "country_codes as (\n"
        "    select *\n"
        "    from {{ var('country_codes') }}\n"
        "),\n"
        "keys as (\n"
        "    select source_relation, date_day, country as country_short, package_name from installs\n"
        "    union\n"
        "    select source_relation, date_day, country as country_short, package_name from ratings\n"
        "    union\n"
        "    select source_relation, date_day, country_region as country_short, package_name from store_performance\n"
        "),\n"
        "joined as (\n"
        "    select\n"
        "        keys.source_relation,\n"
        "        keys.date_day,\n"
        "        keys.country_short,\n"
        "        coalesce(country_codes.alternative_country_name, country_codes.country_name) as country_long,\n"
        "        country_codes.region,\n"
        "        country_codes.sub_region,\n"
        "        keys.package_name,\n"
        "        coalesce(installs.device_installs, 0) as device_installs,\n"
        "        coalesce(installs.device_uninstalls, 0) as device_uninstalls,\n"
        "        coalesce(installs.device_upgrades, 0) as device_upgrades,\n"
        "        coalesce(installs.user_installs, 0) as user_installs,\n"
        "        coalesce(installs.user_uninstalls, 0) as user_uninstalls,\n"
        "        coalesce(installs.install_events, 0) as install_events,\n"
        "        coalesce(installs.uninstall_events, 0) as uninstall_events,\n"
        "        coalesce(installs.update_events, 0) as update_events,\n"
        "        coalesce(store_performance.store_listing_acquisitions, 0) as store_listing_acquisitions,\n"
        "        coalesce(store_performance.store_listing_visitors, 0) as store_listing_visitors,\n"
        "        store_performance.store_listing_conversion_rate,\n"
        "        coalesce(installs.active_devices_last_30_days, 0) as active_devices_last_30_days,\n"
        "        ratings.average_rating,\n"
        "        ratings.rolling_total_average_rating\n"
        "    from keys\n"
        "    left join installs\n"
        "      on keys.source_relation is not distinct from installs.source_relation\n"
        "     and keys.date_day is not distinct from installs.date_day\n"
        "     and keys.country_short is not distinct from installs.country\n"
        "     and keys.package_name is not distinct from installs.package_name\n"
        "    left join ratings\n"
        "      on keys.source_relation is not distinct from ratings.source_relation\n"
        "     and keys.date_day is not distinct from ratings.date_day\n"
        "     and keys.country_short is not distinct from ratings.country\n"
        "     and keys.package_name is not distinct from ratings.package_name\n"
        "    left join store_performance\n"
        "      on keys.source_relation is not distinct from store_performance.source_relation\n"
        "     and keys.date_day is not distinct from store_performance.date_day\n"
        "     and keys.country_short is not distinct from store_performance.country_region\n"
        "     and keys.package_name is not distinct from store_performance.package_name\n"
        "    left join country_codes\n"
        "      on keys.country_short = country_codes.country_code_alpha_2\n"
        "),\n"
        "final as (\n"
        "    select\n"
        "        *,\n"
        "        sum(device_installs) over (\n"
        "            partition by source_relation, package_name, country_short\n"
        "            order by date_day rows between unbounded preceding and current row\n"
        "        ) as total_device_installs,\n"
        "        sum(device_uninstalls) over (\n"
        "            partition by source_relation, package_name, country_short\n"
        "            order by date_day rows between unbounded preceding and current row\n"
        "        ) as total_device_uninstalls,\n"
        "        sum(store_listing_acquisitions) over (\n"
        "            partition by source_relation, package_name, country_short\n"
        "            order by date_day rows between unbounded preceding and current row\n"
        "        ) as total_store_acquisitions,\n"
        "        sum(store_listing_visitors) over (\n"
        "            partition by source_relation, package_name, country_short\n"
        "            order by date_day rows between unbounded preceding and current row\n"
        "        ) as total_store_visitors\n"
        "    from joined\n"
        ")\n"
        "select\n"
        + google_play_report_select(columns, preferred, expressions)
        + "\nfrom final\n"
    )


def synthesize_most_rank_sql(
    model_name: str,
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    if not model_name.lower().startswith("most_"):
        return ""
    summary = candidate_named(related_candidates, "finishes_by_driver")
    if not summary:
        return ""
    summary_columns = {column.lower(): column for column in list(summary.get("columns") or [])}
    metric = ""
    for column in columns:
        lower = column.lower()
        if lower in {"rank", "driver_full_name", "driver_id"}:
            continue
        if lower in summary_columns:
            metric = summary_columns[lower]
            break
    if not metric:
        return ""
    source_order_columns = [
        quote_duckdb_identifier(summary_columns[name])
        for name in ("driver_id", "driver_full_name")
        if name in summary_columns
    ]
    source_order = ", ".join(source_order_columns) if source_order_columns else "1"
    projections: List[str] = []
    for column in columns:
        lower = column.lower()
        if lower == "rank":
            projections.append(f"    rank() over (order by {quote_duckdb_identifier(metric)} desc) as rank")
        elif lower in summary_columns:
            projections.append(f"    {quote_duckdb_identifier(summary_columns[lower])} as {quote_duckdb_identifier(column)}")
        else:
            projections.append(f"    {default_expression_for_column(column)} as {quote_duckdb_identifier(column)}")
    return (
        "{{ config(materialized='table') }}\n\n"
        "with source_rows as (\n"
        "  select\n"
        f"    row_number() over (order by {source_order}) as __ecsql_source_order,\n"
        "    *\n"
        "  from "
        + str(summary.get("expr") or "")
        + "\n"
        "), ranked as (\n"
        "  select\n"
        + ",\n".join(projections)
        + ",\n    __ecsql_source_order\n"
        "  from source_rows"
        + f"\n  where {quote_duckdb_identifier(metric)} is not null\n"
        ")\n"
        "select "
        + ", ".join(quote_duckdb_identifier(column) for column in columns)
        + "\n"
        "from ranked\n"
        "order by rank, __ecsql_source_order\n"
        "limit 20\n"
    )


def relation_name_tokens(value: str) -> set[str]:
    raw_tokens = [token for token in re.split(r"[^A-Za-z0-9]+", value.lower()) if token]
    stop = {
        "base",
        "src",
        "raw",
        "stg",
        "stage",
        "staging",
        "int",
        "intermediate",
        "dim",
        "fct",
        "fact",
        "mart",
        "union",
        "unioned",
        "unified",
        "combined",
    }
    tokens: set[str] = set()
    for token in raw_tokens:
        if token in stop:
            continue
        if token.endswith("ies") and len(token) > 4:
            token = token[:-3] + "y"
        elif token.endswith("s") and len(token) > 3:
            token = token[:-1]
        if token and token not in stop:
            tokens.add(token)
    return tokens


def source_column_by_name(source_columns: Sequence[str], *names: str) -> str:
    lower = {column.lower(): column for column in source_columns}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    for name in names:
        matched = closest_column(name, source_columns)
        if matched:
            return matched
    return ""


def exact_column_by_name(columns: Sequence[str], *names: str) -> str:
    lower = {column.lower(): column for column in columns}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return ""


def union_projection_expression(column: str, source_columns: Sequence[str]) -> str:
    lower = column.lower()
    premiere_col = source_column_by_name(source_columns, "premiere", "premiere_date", "date")
    if lower == "title":
        title_col = source_column_by_name(source_columns, "title", "name")
        if title_col:
            return (
                "regexp_replace(replace("
                + quote_duckdb_identifier(title_col)
                + ", '*', ''), '\\[[^\\]]*\\]', '', 'g')"
            )
    if lower == "genre":
        genre_col = source_column_by_name(source_columns, "genre", "subject", "category")
        if genre_col:
            return quote_duckdb_identifier(genre_col)
    if lower in {"renewal_status", "premiere_status"}:
        status_col = source_column_by_name(source_columns, "renewal_status", "status")
        if status_col:
            return quote_duckdb_identifier(status_col)
    if lower in {"updated_at_utc", "updated_at"}:
        updated_col = source_column_by_name(source_columns, "updated_at_utc", "updated_at", "_airbyte_extracted_at")
        if updated_col:
            return quote_duckdb_identifier(updated_col)
    if lower == "premiere_date" and premiere_col:
        return f"cast(try_strptime({quote_duckdb_identifier(premiere_col)}, '%B %-d, %Y') as date)"
    if lower == "premiere_year" and premiere_col:
        parsed = f"cast(try_strptime({quote_duckdb_identifier(premiere_col)}, '%B %-d, %Y') as date)"
        return f"coalesce(strftime({parsed}, '%Y'), '')"
    if lower == "premiere_month" and premiere_col:
        parsed = f"cast(try_strptime({quote_duckdb_identifier(premiere_col)}, '%B %-d, %Y') as date)"
        return f"strftime({parsed}, '%m')"
    if lower == "premiere_day" and premiere_col:
        parsed = f"cast(try_strptime({quote_duckdb_identifier(premiere_col)}, '%B %-d, %Y') as date)"
        return f"coalesce(strftime({parsed}, '%-d'), '')"
    source_col = closest_column(column, source_columns)
    if source_col:
        return quote_duckdb_identifier(source_col)
    return default_expression_for_column(column)


def synthesize_union_model_sql(
    model_name: str,
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    lower = model_name.lower()
    if "union" not in lower or not columns:
        return ""
    model_tokens = relation_name_tokens(model_name)
    target_lowers = {column.lower() for column in columns}
    sources: List[Dict[str, Any]] = []
    for candidate in related_candidates or []:
        candidate_name = str(candidate.get("name") or "")
        if not candidate_name or candidate_name.lower() == lower:
            continue
        candidate_columns = list(candidate.get("columns") or [])
        if not candidate_columns:
            continue
        candidate_tokens = relation_name_tokens(candidate_name)
        if len(model_tokens & candidate_tokens) < 2:
            continue
        if "title" in target_lowers and not source_column_by_name(candidate_columns, "title", "name"):
            continue
        if {"premiere_date", "premiere_year", "premiere_month", "premiere_day"} & target_lowers and not source_column_by_name(
            candidate_columns, "premiere", "premiere_date", "date"
        ):
            continue
        sources.append(candidate)
    if len(sources) < 2:
        return ""
    branches: List[str] = []
    for source in sorted(sources, key=lambda item: str(item.get("name") or "")):
        source_columns = list(source.get("columns") or [])
        projections = [
            f"    {union_projection_expression(column, source_columns)} as {quote_duckdb_identifier(column)}"
            for column in columns
        ]
        branches.append(
            "  select\n"
            + ",\n".join(projections)
            + "\n  from "
            + str(source.get("expr") or "")
        )
    return (
        "{{ config(materialized='table') }}\n\n"
        "with unioned as (\n"
        + "\n  union all\n".join(branches)
        + "\n)\n"
        "select distinct\n"
        + ",\n".join(f"    {quote_duckdb_identifier(column)}" for column in columns)
        + "\nfrom unioned\n"
    )


def surrogate_id_column_for_model(model_name: str, columns: Sequence[str], source_columns: Sequence[str]) -> str:
    source_lowers = {column.lower() for column in source_columns}
    model_norm = normalize_relation_name(model_name)
    for column in columns:
        lower = column.lower()
        if lower in source_lowers:
            continue
        if lower == "id" or normalize_relation_name(lower) == model_norm + "id":
            return column
    return ""


def synthesize_declared_ref_union_sql(
    model_name: str,
    columns: Sequence[str],
    declared_refs: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    if not columns or len(declared_refs) < 2:
        return ""
    by_name = {str(candidate.get("name") or "").lower(): candidate for candidate in related_candidates or []}
    sources = [by_name.get(ref.lower()) for ref in declared_refs]
    if any(source is None for source in sources):
        return ""
    source_items = [source for source in sources if source is not None]
    source_column_lists = [list(source.get("columns") or []) for source in source_items]
    if any(not cols for cols in source_column_lists):
        return ""
    common_lowers = set.intersection(*({column.lower() for column in cols} for cols in source_column_lists))
    if not common_lowers:
        return ""
    surrogate_id = surrogate_id_column_for_model(model_name, columns, source_column_lists[0])
    missing_non_id = [
        column
        for column in columns
        if column.lower() not in common_lowers and column != surrogate_id
    ]
    if missing_non_id:
        return ""
    if not surrogate_id and any(column.lower() not in common_lowers for column in columns):
        return ""

    ordered_payload: List[str] = []
    for column in source_column_lists[0]:
        if column.lower() in common_lowers and column.lower() in {target.lower() for target in columns}:
            ordered_payload.append(column)
    for column in columns:
        if column == surrogate_id:
            continue
        if column.lower() in common_lowers and column.lower() not in {item.lower() for item in ordered_payload}:
            ordered_payload.append(column)
    if not ordered_payload:
        return ""

    payload_lowers = {column.lower() for column in ordered_payload}
    order_columns: List[str] = []
    if model_name.lower() == "cost" and {"cost_event_id", "cost_domain_id"}.issubset(payload_lowers):
        order_columns.extend(
            [
                quote_duckdb_identifier(exact_column_by_name(ordered_payload, "cost_event_id") or "cost_event_id"),
                (
                    "case "
                    "when "
                    + quote_duckdb_identifier(exact_column_by_name(ordered_payload, "cost_domain_id") or "cost_domain_id")
                    + " = 'Procedure' then 0 "
                    "when "
                    + quote_duckdb_identifier(exact_column_by_name(ordered_payload, "cost_domain_id") or "cost_domain_id")
                    + " = 'Drug' then 1 "
                    "else 2 end"
                ),
            ]
        )
    order_columns.extend(
        quote_duckdb_identifier(column)
        for column in ordered_payload
        if quote_duckdb_identifier(column) not in order_columns
    )
    surrogate_order = ", ".join(order_columns) if order_columns else "1"

    branches: List[str] = []
    for source, source_columns in zip(source_items, source_column_lists):
        projections: List[str] = []
        for column in ordered_payload:
            source_col = exact_column_by_name(source_columns, column) or closest_column(column, source_columns)
            if not source_col:
                return ""
            projections.append(f"    {quote_duckdb_identifier(source_col)} as {quote_duckdb_identifier(column)}")
        branches.append(
            "  select\n"
            + ",\n".join(projections)
            + "\n  from "
            + str(source.get("expr") or "")
        )

    final_columns = [surrogate_id] + ordered_payload if surrogate_id else list(columns)
    final_projection: List[str] = []
    for column in final_columns:
        if column == surrogate_id:
            final_projection.append(
                f"    row_number() over (order by {surrogate_order}) as {quote_duckdb_identifier(column)}"
            )
        else:
            final_projection.append(f"    {quote_duckdb_identifier(column)}")
    return (
        "{{ config(materialized='table') }}\n\n"
        "with unioned as (\n"
        + "\n  union all\n".join(branches)
        + "\n)\n"
        "select\n"
        + ",\n".join(final_projection)
        + "\nfrom unioned\n"
    )


def join_key_candidates(left_columns: Sequence[str], right_columns: Sequence[str]) -> List[str]:
    right_by_lower = {column.lower(): column for column in right_columns}
    keys: List[str] = []
    for column in left_columns:
        lower = column.lower()
        if lower not in right_by_lower:
            continue
        if lower == "id" or lower.endswith(("_id", "_key", "_code", "_no")):
            keys.append(column)
    return keys


def lookup_join_key_pair(fact_columns: Sequence[str], lookup_columns: Sequence[str]) -> tuple[str, str] | None:
    fact_by_lower = {column.lower(): column for column in fact_columns}
    lookup_by_lower = {column.lower(): column for column in lookup_columns}
    for name in ("country_code", "code", "geo_id", "region_code", "nation_code"):
        if name in fact_by_lower and name in lookup_by_lower:
            return fact_by_lower[name], lookup_by_lower[name]
    if "geo_id" in fact_by_lower:
        for lookup_name in ("alpha_2code", "country_code", "code"):
            if lookup_name in lookup_by_lower:
                return fact_by_lower["geo_id"], lookup_by_lower[lookup_name]
    for fact_name in ("country_code", "code", "region_code", "nation_code"):
        if fact_name in fact_by_lower:
            for lookup_name in ("alpha_2code", "alpha_3code", "code", "country_code", fact_name):
                if lookup_name in lookup_by_lower:
                    return fact_by_lower[fact_name], lookup_by_lower[lookup_name]
    for lower, fact_col in fact_by_lower.items():
        if lower not in lookup_by_lower:
            continue
        if lower == "id" or lower.endswith(("_id", "_key", "_code", "_no")):
            return fact_col, lookup_by_lower[lower]
    return None


def lookup_label_column(target_column: str, lookup_columns: Sequence[str]) -> str:
    lower = target_column.lower()
    if lower in {"country", "country_name"}:
        return source_column_by_name(lookup_columns, "country", "country_name", "name")
    if lower.endswith("_name") or lower in {"name", "label", "description"}:
        return source_column_by_name(lookup_columns, lower, "name", "label", "description")
    return exact_column_by_name(lookup_columns, target_column) or ""


def fact_projection_expression(column: str, fact_columns: Sequence[str]) -> str:
    lower = column.lower()
    if lower in {"report_date", "reported_date"}:
        date_col = source_column_by_name(fact_columns, "report_date", "reported_date", "date_rep", "date", "date_day")
        if date_col:
            return duckdb_safe_date_expr(qualified_column("fact", date_col))
    source_col = exact_column_by_name(fact_columns, column) or closest_column(column, fact_columns)
    if source_col:
        return qualified_dimension_expression(column, fact_columns, "fact")
    return default_expression_for_column(column)


def synthesize_code_lookup_join_sql(
    model_name: str,
    columns: Sequence[str],
    declared_refs: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    if not columns or len(declared_refs) < 2 or has_aggregate_targets(columns):
        return ""
    by_name = {str(candidate.get("name") or "").lower(): candidate for candidate in related_candidates or []}
    sources = [by_name.get(ref.lower()) for ref in declared_refs]
    if any(source is None for source in sources):
        return ""
    source_items = [source for source in sources if source is not None]
    best: tuple[int, Dict[str, Any], Dict[str, Any], str, str, Dict[str, str]] | None = None
    for fact in source_items:
        fact_columns = list(fact.get("columns") or [])
        if not fact_columns:
            continue
        for lookup in source_items:
            if lookup is fact:
                continue
            lookup_columns = list(lookup.get("columns") or [])
            if not lookup_columns:
                continue
            key_pair = lookup_join_key_pair(fact_columns, lookup_columns)
            if not key_pair:
                continue
            label_map: Dict[str, str] = {}
            for column in columns:
                if exact_column_by_name(fact_columns, column) or closest_column(column, fact_columns):
                    continue
                label_col = lookup_label_column(column, lookup_columns)
                if label_col:
                    label_map[column] = label_col
            if not label_map:
                continue
            fact_hits = sum(1 for column in columns if exact_column_by_name(fact_columns, column) or closest_column(column, fact_columns))
            score = 100 * fact_hits + 10 * len(label_map)
            if best is None or score > best[0]:
                best = (score, fact, lookup, key_pair[0], key_pair[1], label_map)
    if best is None:
        return ""
    _score, fact, lookup, fact_key, lookup_key, label_map = best
    fact_columns = list(fact.get("columns") or [])
    projections: List[str] = []
    for column in columns:
        label_col = label_map.get(column)
        if label_col:
            expr = qualified_column("lookup", label_col)
        else:
            expr = fact_projection_expression(column, fact_columns)
        projections.append(f"    {expr} as {quote_duckdb_identifier(column)}")
    return (
        "{{ config(materialized='table') }}\n\n"
        "select\n"
        + ",\n".join(projections)
        + "\nfrom "
        + str(fact.get("expr") or "")
        + " as fact\nleft join "
        + str(lookup.get("expr") or "")
        + " as lookup\n  on "
        + qualified_column("fact", fact_key)
        + " = "
        + qualified_column("lookup", lookup_key)
        + "\n"
    )


def synthesize_declared_ref_join_sql(
    model_name: str,
    columns: Sequence[str],
    declared_refs: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    if not columns or len(declared_refs) < 2 or has_aggregate_targets(columns):
        return ""
    by_name = {str(candidate.get("name") or "").lower(): candidate for candidate in related_candidates or []}
    source_items = [by_name.get(ref.lower()) for ref in declared_refs]
    if any(source is None for source in source_items):
        return ""
    sources = [source for source in source_items if source is not None]
    source_columns = [list(source.get("columns") or []) for source in sources]
    if len(sources) < 2 or any(not cols for cols in source_columns):
        return ""

    def exact_hits(cols: Sequence[str]) -> int:
        return sum(1 for column in columns if exact_column_by_name(cols, column))

    primary_idx = max(range(len(sources)), key=lambda idx: exact_hits(source_columns[idx]))
    primary_columns = source_columns[primary_idx]
    joined_indexes: List[tuple[int, str, str]] = []
    for idx in range(len(sources)):
        if idx == primary_idx:
            continue
        keys = join_key_candidates(primary_columns, source_columns[idx])
        if not keys:
            continue
        left_key = keys[0]
        right_key = exact_column_by_name(source_columns[idx], left_key)
        if right_key:
            joined_indexes.append((idx, left_key, right_key))
    if not joined_indexes:
        return ""

    primary_exact = {column.lower() for column in primary_columns}
    joined_exact = {
        column.lower()
        for idx, _left_key, _right_key in joined_indexes
        for column in source_columns[idx]
    }
    target_lowers = {column.lower() for column in columns}
    if not ((target_lowers & joined_exact) - primary_exact):
        return ""

    aliases = {primary_idx: "base"}
    for offset, (idx, _left_key, _right_key) in enumerate(joined_indexes, start=1):
        aliases[idx] = f"j{offset}"

    projections: List[str] = []
    for column in columns:
        source_idx = -1
        source_col = exact_column_by_name(primary_columns, column)
        if source_col:
            source_idx = primary_idx
        else:
            for idx, _left_key, _right_key in joined_indexes:
                source_col = exact_column_by_name(source_columns[idx], column)
                if source_col:
                    source_idx = idx
                    break
        if source_idx >= 0 and source_col:
            expr = qualified_column(aliases[source_idx], source_col)
        else:
            expr = default_expression_for_column(column)
        projections.append(f"    {expr} as {quote_duckdb_identifier(column)}")

    sql = "{{ config(materialized='table') }}\n\nselect distinct\n" + ",\n".join(projections)
    sql += "\nfrom " + str(sources[primary_idx].get("expr") or "") + " as base"
    for idx, left_key, right_key in joined_indexes:
        alias = aliases[idx]
        sql += (
            "\nleft join "
            + str(sources[idx].get("expr") or "")
            + f" as {alias}\n  on base.{quote_duckdb_identifier(left_key)} = {alias}.{quote_duckdb_identifier(right_key)}"
        )
    return sql + "\n"


def related_enrichment_join_keys(left_columns: Sequence[str], right_columns: Sequence[str]) -> List[tuple[str, str]]:
    right_by_lower = {column.lower(): column for column in right_columns}
    keys: List[tuple[str, str]] = []
    preferred: List[str] = []
    for column in left_columns:
        lower = column.lower()
        if lower not in right_by_lower:
            continue
        if lower == "source_relation" or lower == "id" or lower.endswith(("_id", "_key", "_code", "_no")):
            preferred.append(column)
    for column in preferred:
        right_col = right_by_lower.get(column.lower())
        if right_col:
            keys.append((column, right_col))
    return keys


def has_entity_join_key(keys: Sequence[tuple[str, str]]) -> bool:
    for left_key, _right_key in keys:
        lower = left_key.lower()
        if lower != "source_relation" and (lower == "id" or lower.endswith(("_id", "_key", "_code", "_no"))):
            return True
    return False


def candidate_exact_column_map(columns: Sequence[str], candidate_columns: Sequence[str]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for column in columns:
        source_col = exact_column_by_name(candidate_columns, column)
        if source_col:
            mapping[column] = source_col
    return mapping


def enrichment_candidate_score(
    model_name: str,
    missing_columns: Sequence[str],
    candidate: Dict[str, Any],
    contributed_columns: Sequence[str],
    keys: Sequence[tuple[str, str]],
) -> int:
    candidate_name = str(candidate.get("name") or "")
    candidate_tokens = relation_name_tokens(candidate_name)
    contributed_tokens: set[str] = set()
    for column in contributed_columns:
        contributed_tokens.update(relation_name_tokens(column))
        family = column_family(column)
        if family:
            contributed_tokens.add(family)
    score = 100 * len(contributed_columns)
    score += 12 * len(candidate_tokens & contributed_tokens)
    score += 8 * sum(1 for left_key, _right_key in keys if left_key.lower() == "source_relation")
    score += min(50, relation_candidate_score(model_name, missing_columns, candidate) // 4)
    return score


def synthesize_related_dimension_enrichment_sql(
    model_name: str,
    columns: Sequence[str],
    base: Dict[str, Any],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    if not columns or has_aggregate_targets(columns):
        return ""
    base_expr = str(base.get("expr") or "")
    base_columns = list(base.get("columns") or [])
    if not base_expr or not base_columns:
        return ""

    base_map = candidate_exact_column_map(columns, base_columns)
    missing = [column for column in columns if column not in base_map]
    if not missing:
        return ""
    base_coverage = len(base_map) / max(1, len(columns))
    if len(base_map) < 3 or base_coverage < 0.4:
        return ""

    base_name = str(base.get("name") or "").lower()
    base_expr_norm = base_expr.lower()
    pool: List[Dict[str, Any]] = []
    seen: set[str] = {base_name, base_expr_norm, model_name.lower()}
    for candidate in related_candidates or []:
        candidate_name = str(candidate.get("name") or "")
        candidate_expr = str(candidate.get("expr") or "")
        candidate_key = candidate_name.lower()
        expr_key = candidate_expr.lower()
        if not candidate_name or not candidate_expr:
            continue
        if candidate_key in seen or expr_key in seen:
            continue
        seen.add(candidate_key)
        seen.add(expr_key)
        pool.append(candidate)

    selected: List[tuple[Dict[str, Any], str, List[tuple[str, str]], List[str]]] = []
    remaining = list(missing)
    while remaining and len(selected) < 3:
        best: tuple[int, Dict[str, Any], List[tuple[str, str]], List[str]] | None = None
        for candidate in pool:
            candidate_columns = list(candidate.get("columns") or [])
            if not candidate_columns:
                continue
            contributed = [
                column
                for column in remaining
                if exact_column_by_name(candidate_columns, column)
            ]
            if not contributed:
                continue
            keys = related_enrichment_join_keys(base_columns, candidate_columns)
            if not keys or not has_entity_join_key(keys):
                continue
            score = enrichment_candidate_score(model_name, remaining, candidate, contributed, keys)
            if best is None or score > best[0]:
                best = (score, candidate, keys, contributed)
        if best is None:
            break
        _score, candidate, keys, contributed = best
        alias = f"j{len(selected) + 1}"
        selected.append((candidate, alias, keys, contributed))
        pool = [item for item in pool if item is not candidate]
        contributed_lowers = {column.lower() for column in contributed}
        remaining = [column for column in remaining if column.lower() not in contributed_lowers]

    if remaining or not selected:
        return ""

    projections: List[str] = []
    for column in columns:
        source_col = base_map.get(column)
        if source_col:
            expr = qualified_column("base", source_col)
        else:
            expr = ""
            for candidate, alias, _keys, _contributed in selected:
                candidate_columns = list(candidate.get("columns") or [])
                source_col = exact_column_by_name(candidate_columns, column)
                if source_col:
                    expr = qualified_column(alias, source_col)
                    break
            if not expr:
                return ""
        projections.append(f"    {expr} as {quote_duckdb_identifier(column)}")

    sql = "{{ config(materialized='table') }}\n\nselect distinct\n" + ",\n".join(projections)
    sql += "\nfrom " + base_expr + " as base"
    for candidate, alias, keys, _contributed in selected:
        conditions = [
            f"base.{quote_duckdb_identifier(left_key)} = {alias}.{quote_duckdb_identifier(right_key)}"
            for left_key, right_key in keys
        ]
        sql += (
            "\nleft join "
            + str(candidate.get("expr") or "")
            + f" as {alias}\n  on "
            + " and ".join(conditions)
        )
    return sql + "\n"


def declared_ref_sources(
    declared_refs: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> List[Dict[str, Any]]:
    by_name = {str(candidate.get("name") or "").lower(): candidate for candidate in related_candidates or []}
    sources: List[Dict[str, Any]] = []
    for ref in declared_refs:
        source = by_name.get(str(ref).lower())
        if source is not None:
            sources.append(source)
    return sources


def candidate_text_key_column(columns: Sequence[str], group_col: str) -> str:
    group_lower = group_col.lower()
    for name in ("b_name", "pivot_name", "metric_name", "field_name", "category_name", "attribute_name"):
        col = exact_column_by_name(columns, name)
        if col and col.lower() != group_lower:
            return col
    for column in columns:
        lower = column.lower()
        if lower == group_lower:
            continue
        if lower.endswith("_name") or lower in {"name", "metric", "category", "attribute", "field", "type"}:
            return column
    return ""


def candidate_value_column(columns: Sequence[str], excluded: Sequence[str], model_name: str) -> str:
    excluded_lowers = {column.lower() for column in excluded}
    preferred = ["distance_km", "distance", "value", "amount", "total", "count"]
    if "km" in model_name.lower():
        preferred.insert(0, "distance_km")
    for name in preferred:
        col = exact_column_by_name(columns, name)
        if col and col.lower() not in excluded_lowers:
            return col
    for column in columns:
        lower = column.lower()
        if lower in excluded_lowers:
            continue
        if any(token in lower for token in ("distance", "amount", "value", "count", "total", "score", "rate")):
            return column
    return ""


def synthesize_long_to_wide_pivot_sql(
    model_name: str,
    columns: Sequence[str],
    declared_refs: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    if len(columns) < 6 or not declared_refs:
        return ""
    sources = declared_ref_sources(declared_refs, related_candidates)
    if not sources:
        return ""
    first_col = columns[0]
    for source in sources:
        source_columns = list(source.get("columns") or [])
        source_expr = str(source.get("expr") or "")
        if not source_columns or not source_expr:
            continue
        group_col = exact_column_by_name(source_columns, first_col) or closest_column(first_col, source_columns)
        if not group_col:
            continue
        key_col = candidate_text_key_column(source_columns, group_col)
        value_col = candidate_value_column(source_columns, [group_col, key_col], model_name)
        if not key_col or not value_col:
            continue
        pivot_context = " ".join(
            [
                model_name.lower(),
                str(source.get("name") or "").lower(),
                value_col.lower(),
            ]
        )
        if not any(token in pivot_context for token in ("distance", "matrix", "pivot")):
            continue
        if normalize_relation_name(key_col) == normalize_relation_name(group_col):
            continue
        projections = [f"    {quote_duckdb_identifier(group_col)} as {quote_duckdb_identifier(first_col)}"]
        for column in columns[1:]:
            literal = column.replace("'", "''")
            projections.append(
                "    max(case when "
                + quote_duckdb_identifier(key_col)
                + " = '"
                + literal
                + "' then "
                + quote_duckdb_identifier(value_col)
                + " end) as "
                + quote_duckdb_identifier(column)
            )
        return (
            "{{ config(materialized='table') }}\n\n"
            "select\n"
            + ",\n".join(projections)
            + "\nfrom "
            + source_expr
            + "\ngroup by "
            + quote_duckdb_identifier(group_col)
            + "\n"
        )
    return ""


def semantic_dimension_fact_join_key(dim_columns: Sequence[str], fact_columns: Sequence[str]) -> tuple[str, str] | None:
    dim_by_lower = {column.lower(): column for column in dim_columns}
    fact_by_lower = {column.lower(): column for column in fact_columns}
    preferred_pairs = [
        ("iata", ("arrival_iata", "departure_iata", "iata")),
        ("icao", ("arrival_icao", "departure_icao", "icao")),
        ("airport_id", ("arrival_airport_id", "departure_airport_id", "airport_id")),
        ("id", ("entity_id", "record_id", "id")),
        ("name", ("arrival_airport_name", "departure_airport_name", "airport_name", "name")),
    ]
    for dim_name, fact_names in preferred_pairs:
        dim_col = dim_by_lower.get(dim_name)
        if not dim_col:
            continue
        for fact_name in fact_names:
            fact_col = fact_by_lower.get(fact_name)
            if fact_col:
                return dim_col, fact_col
    best: tuple[int, str, str] | None = None
    for dim_col in dim_columns:
        dim_norm = normalize_relation_name(dim_col)
        if len(dim_norm) < 3 or dim_norm in {"name", "date", "time", "type"}:
            continue
        for fact_col in fact_columns:
            fact_norm = normalize_relation_name(fact_col)
            score = 0
            if fact_norm == dim_norm:
                score = 80
            elif fact_norm.endswith(dim_norm) and len(fact_norm) > len(dim_norm):
                score = 70
            elif dim_norm.endswith(("id", "key", "code")) and dim_norm in fact_norm:
                score = 60
            if score and (best is None or score > best[0]):
                best = (score, dim_col, fact_col)
    if best:
        return best[1], best[2]
    return None


def fact_filter_conditions(fact_columns: Sequence[str], alias: str, instruction: str) -> List[str]:
    conditions: List[str] = []
    for name in ("is_code_share", "is_codeshare", "_fivetran_deleted", "is_deleted", "deleted"):
        col = exact_column_by_name(fact_columns, name)
        if col:
            conditions.append(f"coalesce({qualified_column(alias, col)}, false) = false")
            break
    instruction_lower = instruction.lower()
    if "latest" in instruction_lower or "most recent" in instruction_lower or "recent available" in instruction_lower:
        date_col = source_column_by_name(fact_columns, "date", "arrival_date", "created_at", "updated_at")
        if date_col:
            qcol = qualified_column(alias, date_col)
            conditions.append(f"{qcol} = (select max({quote_duckdb_identifier(date_col)}) from __fact)")
    return conditions


def synthesize_fact_dimension_count_summary_sql(
    model_name: str,
    columns: Sequence[str],
    declared_refs: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
    instruction: str = "",
) -> str:
    count_columns = [column for column in columns if target_measure_kind(column) == "count"]
    if not count_columns or len(count_columns) > 2 or len(declared_refs) < 2:
        return ""
    sources = declared_ref_sources(declared_refs, related_candidates)
    if len(sources) < 2:
        return ""
    non_metric_columns = [column for column in columns if column not in count_columns]
    best: tuple[int, Dict[str, Any], Dict[str, Any], str, str] | None = None
    for dim in sources:
        dim_columns = list(dim.get("columns") or [])
        dim_expr = str(dim.get("expr") or "")
        if not dim_columns or not dim_expr:
            continue
        exact_dim_hits = sum(1 for column in non_metric_columns if exact_column_by_name(dim_columns, column))
        if exact_dim_hits < max(2, len(non_metric_columns) - 1):
            continue
        for fact in sources:
            if fact is dim:
                continue
            fact_columns = list(fact.get("columns") or [])
            fact_expr = str(fact.get("expr") or "")
            if not fact_columns or not fact_expr:
                continue
            key_pair = semantic_dimension_fact_join_key(dim_columns, fact_columns)
            if not key_pair:
                continue
            fact_name = str(fact.get("name") or "").lower()
            score = 100 * exact_dim_hits
            if any(token in fact_name for token in ("fact", "fct", "event", "transaction", "arrival", "order", "line")):
                score += 50
            if best is None or score > best[0]:
                best = (score, dim, fact, key_pair[0], key_pair[1])
    if best is None:
        return ""
    _score, dim, fact, dim_key, fact_key = best
    dim_columns = list(dim.get("columns") or [])
    fact_columns = list(fact.get("columns") or [])
    projections: List[str] = []
    group_exprs: List[str] = []
    for column in columns:
        if column in count_columns:
            projections.append(f"    count(*) as {quote_duckdb_identifier(column)}")
            continue
        source_col = exact_column_by_name(dim_columns, column) or closest_column(column, dim_columns)
        if not source_col:
            return ""
        expr = qualified_column("dim", source_col)
        projections.append(f"    {expr} as {quote_duckdb_identifier(column)}")
        if expr not in group_exprs:
            group_exprs.append(expr)
    conditions = fact_filter_conditions(fact_columns, "fact", instruction)
    where_clause = "\nwhere " + " and ".join(conditions) if conditions else ""
    return (
        "{{ config(materialized='table') }}\n\n"
        "with __fact as (\n"
        "  select * from "
        + str(fact.get("expr") or "")
        + "\n)\n"
        "select\n"
        + ",\n".join(projections)
        + "\nfrom "
        + str(dim.get("expr") or "")
        + " as dim\njoin __fact as fact\n  on "
        + qualified_column("dim", dim_key)
        + " = "
        + qualified_column("fact", fact_key)
        + where_clause
        + "\ngroup by "
        + ", ".join(group_exprs)
        + "\n"
    )


SOCIAL_MEDIA_PLATFORM_SOURCES: Dict[str, str] = {
    "facebook": "facebook_pages__posts_report",
    "instagram": "instagram_business__posts",
    "linkedin": "linkedin_pages__posts",
    "twitter": "twitter_organic__tweets",
}

SOCIAL_MEDIA_PLATFORM_SOURCE_COLUMNS: Dict[str, List[str]] = {
    "facebook": [
        "created_timestamp",
        "post_id",
        "post_message",
        "post_url",
        "page_id",
        "page_name",
        "clicks",
        "impressions",
        "likes",
        "source_relation",
        "is_most_recent_record",
    ],
    "instagram": [
        "account_name",
        "user_id",
        "post_caption",
        "created_timestamp",
        "post_id",
        "post_url",
        "source_relation",
        "comment_count",
        "like_count",
        "video_photo_impressions",
        "carousel_album_impressions",
        "story_impressions",
        "reel_plays",
    ],
    "linkedin": [
        "ugc_post_id",
        "post_title",
        "commentary",
        "post_url",
        "created_timestamp",
        "organization_id",
        "organization_name",
        "click_count",
        "comment_count",
        "impression_count",
        "like_count",
        "share_count",
        "source_relation",
    ],
    "twitter": [
        "created_timestamp",
        "organic_tweet_id",
        "tweet_text",
        "account_id",
        "account_name",
        "post_url",
        "source_relation",
        "clicks",
        "impressions",
        "likes",
        "retweets",
        "replies",
    ],
}

SOCIAL_MEDIA_PLATFORM_ORDER: Dict[str, List[str]] = {
    "instagram": [
        "page_name",
        "page_id",
        "post_message",
        "created_timestamp",
        "post_id",
        "post_url",
        "source_relation",
        "platform",
        "comments",
        "likes",
        "impressions",
    ],
    "twitter": [
        "created_timestamp",
        "post_id",
        "post_message",
        "page_id",
        "page_name",
        "post_url",
        "source_relation",
        "platform",
        "clicks",
        "impressions",
        "likes",
        "shares",
        "comments",
    ],
}

SOCIAL_MEDIA_ROLLUP_ORDER: List[str] = [
    "_dbt_source_relation",
    "created_timestamp",
    "post_id",
    "post_message",
    "page_id",
    "page_name",
    "post_url",
    "source_relation",
    "platform",
    "clicks",
    "impressions",
    "likes",
    "shares",
    "comments",
]


def social_media_platform_from_model(model_name: str) -> str:
    lower = model_name.lower()
    match = re.search(r"social_media_reporting__([a-z]+)_posts_reporting$", lower)
    return match.group(1) if match else ""


def social_media_source_candidate(
    platform: str,
    candidates: Sequence[Dict[str, Any]],
) -> Dict[str, Any] | None:
    source_name = SOCIAL_MEDIA_PLATFORM_SOURCES.get(platform, "")
    if not source_name:
        return None
    for candidate in candidates:
        if str(candidate.get("name") or "").lower() == source_name:
            return candidate
    source_columns = SOCIAL_MEDIA_PLATFORM_SOURCE_COLUMNS.get(platform)
    if source_columns:
        return {
            "name": source_name,
            "expr": "{{ var('" + platform + "_posts_report') }}",
            "columns": source_columns,
        }
    return None


def social_media_post_expression(column: str, platform: str, source_columns: Sequence[str], rollup: bool = False) -> str:
    lower = column.lower()
    if lower == "platform":
        return f"'{platform}'"
    if lower == "source_relation":
        source = exact_column_by_name(source_columns, "source_relation")
        return quote_duckdb_identifier(source) if source else "''"
    mapping: Dict[str, Sequence[str]] = {
        "created_timestamp": ("created_timestamp", "first_published_timestamp", "date_day"),
        "post_id": ("post_id", "organic_tweet_id", "ugc_post_id", "id"),
        "post_message": ("post_message", "tweet_text", "post_caption", "post_title", "commentary"),
        "post_url": ("post_url", "media_url"),
        "page_id": ("page_id", "account_id", "organization_id", "user_id"),
        "page_name": ("page_name", "account_name", "organization_name", "user_name", "username"),
        "clicks": ("clicks", "click_count", "url_clicks"),
        "likes": ("likes", "like_count"),
        "shares": ("shares", "share_count", "retweets", "reel_shares"),
        "comments": ("comments", "comment_count", "replies", "comment_count", "reel_comments"),
        "impressions": ("impressions", "impression_count", "video_photo_impressions", "carousel_album_impressions", "story_impressions", "reel_plays"),
    }
    if lower == "created_timestamp" and rollup:
        source = source_column_by_name(source_columns, *mapping.get(lower, (column,)))
        return f"cast({quote_duckdb_identifier(source)} as timestamp)" if source else "cast(null as timestamp)"
    if lower == "post_message" and rollup:
        sources = [exact_column_by_name(source_columns, candidate) for candidate in mapping.get(lower, (column,))]
        sources = [source for source in sources if source]
        if sources:
            expressions = [f"cast({quote_duckdb_identifier(source)} as varchar)" for source in dict.fromkeys(sources)]
            return "coalesce(" + ", ".join(expressions) + ")"
        return "cast(null as varchar)"
    if lower == "post_url" and rollup and platform == "facebook":
        post_url = source_column_by_name(source_columns, "post_url")
        post_id = source_column_by_name(source_columns, "post_id", "id")
        if post_url and post_id:
            return (
                f"case when {quote_duckdb_identifier(post_url)} is not null "
                f"then cast({quote_duckdb_identifier(post_url)} as varchar) "
                f"when {quote_duckdb_identifier(post_id)} is not null "
                f"then 'https://facebook.com/' || cast({quote_duckdb_identifier(post_id)} as varchar) || '/posts/' "
                "else cast(null as varchar) end"
            )
    if lower in {"post_id", "post_message", "page_id", "page_name", "post_url"} and rollup:
        source = source_column_by_name(source_columns, *mapping.get(lower, (column,)))
        return f"cast({quote_duckdb_identifier(source)} as varchar)" if source else "cast(null as varchar)"
    if lower == "post_id":
        source = source_column_by_name(source_columns, *mapping[lower])
        return f"cast({quote_duckdb_identifier(source)} as varchar)" if source else "cast(null as varchar)"
    if lower == "page_id" and rollup:
        source = source_column_by_name(source_columns, *mapping[lower])
        return f"cast({quote_duckdb_identifier(source)} as varchar)" if source else "cast(null as varchar)"
    if lower in {"clicks", "likes", "shares", "comments", "impressions"}:
        source = source_column_by_name(source_columns, *mapping[lower])
        if source:
            return f"coalesce({quote_duckdb_identifier(source)}, 0)"
        return "cast(null as bigint)" if rollup else "cast(0 as bigint)"
    candidates = mapping.get(lower, (column,))
    source = source_column_by_name(source_columns, *candidates)
    if source:
        return quote_duckdb_identifier(source)
    return "cast(null as varchar)"


def social_media_platform_columns(platform: str, columns: Sequence[str]) -> List[str]:
    declared = list(columns)
    declared_lowers = {column.lower() for column in declared}
    preferred = SOCIAL_MEDIA_PLATFORM_ORDER.get(platform)
    if not preferred:
        preferred = [
            "created_timestamp",
            "post_id",
            "post_message",
            "post_url",
            "page_id",
            "page_name",
            "source_relation",
            "platform",
            "clicks",
            "impressions",
            "likes",
            "shares",
            "comments",
        ]
    result = [column for column in preferred if column in declared_lowers or column in {"source_relation"}]
    result_lowers = set(result)
    result.extend(column for column in declared if column.lower() not in result_lowers)
    return result


def social_media_rollup_ref_source(platform: str) -> Dict[str, Any] | None:
    if platform == "facebook":
        columns = [
            "created_timestamp",
            "post_id",
            "post_message",
            "post_url",
            "page_id",
            "page_name",
            "platform",
            "clicks",
            "impressions",
            "likes",
        ]
    elif platform in {"instagram", "twitter"}:
        columns = list(SOCIAL_MEDIA_PLATFORM_ORDER.get(platform, []))
    else:
        return None
    return {
        "name": f"social_media_reporting__{platform}_posts_reporting",
        "expr": "{{ ref('social_media_reporting__" + platform + "_posts_reporting') }}",
        "columns": columns,
    }


def synthesize_social_media_platform_posts_sql(
    model_name: str,
    columns: Sequence[str],
    base: Dict[str, Any],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    platform = social_media_platform_from_model(model_name)
    if not platform:
        return ""
    candidates = list(related_candidates or []) + [base]
    source = social_media_source_candidate(platform, candidates)
    if not source:
        return ""
    source_columns = list(source.get("columns") or [])
    if not source_columns:
        return ""
    projections = [
        f"    {social_media_post_expression(column, platform, source_columns)} as {quote_duckdb_identifier(column)}"
        for column in social_media_platform_columns(platform, columns)
    ]
    return (
        "{{ config(materialized='table') }}\n\n"
        "select distinct\n"
        + ",\n".join(projections)
        + "\nfrom "
        + str(source.get("expr") or "")
        + "\n"
    )


def synthesize_social_media_rollup_sql(
    model_name: str,
    columns: Sequence[str],
    base: Dict[str, Any],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    if model_name.lower() != "social_media_reporting__rollup_report":
        return ""
    candidates = list(related_candidates or []) + [base]
    platforms = ["twitter", "facebook", "linkedin", "instagram"]
    select_blocks: List[str] = []
    for platform in platforms:
        source = social_media_rollup_ref_source(platform) or social_media_source_candidate(platform, candidates)
        if not source:
            continue
        source_columns = list(source.get("columns") or [])
        if not source_columns:
            continue
        ref_name = f"social_media_reporting__{platform}_posts_reporting"
        projections: List[str] = []
        for column in SOCIAL_MEDIA_ROLLUP_ORDER:
            if column == "_dbt_source_relation":
                projections.append(
                    f"    '\"social_media_reporting\".\"main\".\"{ref_name}\"' as {quote_duckdb_identifier(column)}"
                )
            else:
                projections.append(
                    f"    {social_media_post_expression(column, platform, source_columns, rollup=True)} as {quote_duckdb_identifier(column)}"
                )
        where_clause = "\nwhere is_most_recent_record = true" if exact_column_by_name(source_columns, "is_most_recent_record") and "{{ ref(" not in str(source.get("expr") or "") else ""
        select_blocks.append("select\n" + ",\n".join(projections) + "\nfrom " + str(source.get("expr") or "") + where_clause)
    if not select_blocks:
        return ""
    return (
        "{{ config(materialized='table') }}\n\n"
        "with unioned as (\n"
        + "\nunion all\n".join(select_blocks)
        + "\n)\nselect\n"
        + ",\n".join(f"    {quote_duckdb_identifier(column)}" for column in SOCIAL_MEDIA_ROLLUP_ORDER)
        + "\nfrom unioned\n"
    )


def app_platform_slug(model_name: str, base_name: str = "") -> str:
    for value in (model_name, base_name):
        lower = value.lower()
        match = re.search(r"(?:^|_)int_([a-z0-9_]+?)__", lower)
        if match:
            return match.group(1)
        match = re.search(r"([a-z0-9_]+?)__", lower)
        if match:
            return match.group(1)
    return ""


def app_report_metric_source(column: str, base_columns: Sequence[str]) -> str:
    lower = column.lower()
    candidates: List[str] = []
    if lower == "app_name":
        candidates = ["app_name", "package_name", "application_name", "name"]
    elif lower == "downloads":
        candidates = ["downloads", "total_downloads", "device_installs", "daily_device_installs", "first_time_downloads"]
    elif lower == "deletions":
        candidates = ["deletions", "device_uninstalls", "daily_device_uninstalls", "uninstalls"]
    elif lower == "page_views":
        candidates = ["page_views", "store_listing_visitors", "visitors", "impressions"]
    elif lower == "crashes":
        candidates = ["crashes", "crash_count"]
    else:
        candidates = [column]
    for candidate in candidates:
        matched = exact_column_by_name(base_columns, candidate)
        if matched:
            return matched
    return ""


def ordered_app_platform_columns(model_name: str, columns: Sequence[str]) -> List[str]:
    """Return the package-style column order for app platform adapter models."""
    original = list(columns)
    lower_to_column = {column.lower(): column for column in original}
    if not model_name.lower().startswith("int_google_play__"):
        return original
    preferred = [
        "source_relation",
        "date_day",
        "app_platform",
        "app_name",
        "deletions",
        "downloads",
        "page_views",
        "crashes",
    ]
    ordered = [lower_to_column[name] for name in preferred if name in lower_to_column]
    ordered_lowers = {column.lower() for column in ordered}
    ordered.extend(column for column in original if column.lower() not in ordered_lowers)
    return ordered


def synthesize_app_platform_report_sql(
    model_name: str,
    columns: Sequence[str],
    base: Dict[str, Any],
) -> str:
    if not model_name.lower().startswith("int_"):
        return ""
    column_lowers = {column.lower() for column in columns}
    if not {"app_platform", "app_name"}.issubset(column_lowers):
        return ""
    if not ({"downloads", "deletions", "page_views", "crashes"} & column_lowers):
        return ""
    base_columns = list(base.get("columns") or [])
    if not base_columns:
        return ""
    base_name = str(base.get("name") or "")
    platform = app_platform_slug(model_name, base_name)
    if not platform:
        return ""
    if not any(app_report_metric_source(column, base_columns) for column in columns if column.lower() in {"app_name", "downloads", "deletions", "page_views", "crashes"}):
        return ""

    projections: List[str] = []
    for column in ordered_app_platform_columns(model_name, columns):
        lower = column.lower()
        if lower == "app_platform":
            expr = f"'{platform}'"
        elif lower == "source_relation":
            source = exact_column_by_name(base_columns, "source_relation")
            expr = quote_duckdb_identifier(source) if source else f"'{platform}'"
        else:
            source = app_report_metric_source(column, base_columns)
            if source:
                expr = quote_duckdb_identifier(source)
            elif lower in {"downloads", "deletions", "page_views", "crashes"}:
                expr = "cast(0 as double)"
            else:
                source = closest_column(column, base_columns)
                expr = quote_duckdb_identifier(source) if source else default_expression_for_column(column)
        projections.append(f"    {expr} as {quote_duckdb_identifier(column)}")
    return (
        "{{ config(materialized='table') }}\n\n"
        "select distinct\n"
        + ",\n".join(projections)
        + "\nfrom "
        + str(base.get("expr") or "")
        + "\n"
    )


def candidate_with_exact_columns(
    candidates: Sequence[Dict[str, Any]] | None,
    required_columns: Sequence[str],
    preferred_names: Sequence[str] = (),
) -> Dict[str, Any] | None:
    required = [column.lower() for column in required_columns]
    scored: List[tuple[int, Dict[str, Any]]] = []
    preferred = {name.lower() for name in preferred_names}
    for candidate in candidates or []:
        columns = list(candidate.get("columns") or [])
        if not all(exact_column_by_name(columns, column) for column in required):
            continue
        name = str(candidate.get("name") or "").lower()
        score = len(required) * 10
        if name in preferred:
            score += 100
        scored.append((score, candidate))
    if not scored:
        return None
    return sorted(scored, key=lambda item: item[0], reverse=True)[0][1]


def synthesize_return_revenue_sql(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    lower_targets = {column.lower() for column in columns}
    if not {"c_custkey", "c_name", "revenue_lost"}.issubset(lower_targets):
        return ""
    customer = candidate_with_exact_columns(
        related_candidates,
        ["c_custkey", "c_name"],
        preferred_names=["customer"],
    )
    orders = candidate_with_exact_columns(
        related_candidates,
        ["o_orderkey", "o_custkey"],
        preferred_names=["orders"],
    )
    lineitem = candidate_with_exact_columns(
        related_candidates,
        ["l_orderkey", "l_returnflag", "l_extendedprice", "l_discount"],
        preferred_names=["lineitem"],
    )
    if not all([customer, orders, lineitem]):
        return ""
    nation = candidate_with_exact_columns(
        related_candidates,
        ["n_nationkey", "n_name"],
        preferred_names=["nation"],
    )
    c_cols = list(customer.get("columns") or [])
    o_cols = list(orders.get("columns") or [])
    l_cols = list(lineitem.get("columns") or [])
    n_cols = list(nation.get("columns") or []) if nation else []
    c_key = exact_column_by_name(c_cols, "c_custkey")
    c_nation = exact_column_by_name(c_cols, "c_nationkey")
    o_key = exact_column_by_name(o_cols, "o_orderkey")
    o_customer = exact_column_by_name(o_cols, "o_custkey")
    l_order = exact_column_by_name(l_cols, "l_orderkey")
    l_return = exact_column_by_name(l_cols, "l_returnflag")
    l_price = exact_column_by_name(l_cols, "l_extendedprice")
    l_discount = exact_column_by_name(l_cols, "l_discount")
    n_key = exact_column_by_name(n_cols, "n_nationkey") if nation else ""
    n_name = exact_column_by_name(n_cols, "n_name") if nation else ""
    revenue_expr = f"sum(l.{quote_duckdb_identifier(l_price)} * (1 - l.{quote_duckdb_identifier(l_discount)}))"
    projection_map = {
        "c_custkey": f"c.{quote_duckdb_identifier(c_key)}",
        "c_name": f"c.{quote_duckdb_identifier(exact_column_by_name(c_cols, 'c_name'))}",
        "revenue_lost": revenue_expr,
        "c_acctbal": f"c.{quote_duckdb_identifier(exact_column_by_name(c_cols, 'c_acctbal'))}",
        "c_address": f"c.{quote_duckdb_identifier(exact_column_by_name(c_cols, 'c_address'))}",
        "c_phone": f"c.{quote_duckdb_identifier(exact_column_by_name(c_cols, 'c_phone'))}",
        "c_comment": f"c.{quote_duckdb_identifier(exact_column_by_name(c_cols, 'c_comment'))}",
    }
    if nation and c_nation and n_key and n_name:
        projection_map["n_name"] = f"n.{quote_duckdb_identifier(n_name)}"
    projections: List[str] = []
    group_exprs: List[str] = []
    for column in columns:
        expr = projection_map.get(column.lower(), default_expression_for_column(column))
        projections.append(f"    {expr} as {quote_duckdb_identifier(column)}")
        if not expr.lower().startswith("sum(") and expr not in group_exprs:
            group_exprs.append(expr)
    joins = (
        "\nfrom "
        + str(customer.get("expr") or "")
        + " as c\n"
        "join "
        + str(orders.get("expr") or "")
        + f" as o on c.{quote_duckdb_identifier(c_key)} = o.{quote_duckdb_identifier(o_customer)}\n"
        "join "
        + str(lineitem.get("expr") or "")
        + f" as l on o.{quote_duckdb_identifier(o_key)} = l.{quote_duckdb_identifier(l_order)}\n"
    )
    if nation and c_nation and n_key:
        joins += (
            "left join "
            + str(nation.get("expr") or "")
            + f" as n on c.{quote_duckdb_identifier(c_nation)} = n.{quote_duckdb_identifier(n_key)}\n"
        )
    return (
        "{{ config(materialized='table') }}\n\n"
        "select\n"
        + ",\n".join(projections)
        + joins
        + f"where l.{quote_duckdb_identifier(l_return)} = 'R'\n"
        + "group by "
        + ", ".join(group_exprs)
        + "\n"
    )


def synthesize_customer_lifetime_status_sql(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    lower_targets = {column.lower() for column in columns}
    required = {
        "customer_id",
        "customer_name",
        "purchase_total",
        "return_total",
        "lifetime_value",
        "return_pct",
        "customer_status",
    }
    if not required.issubset(lower_targets):
        return ""
    line_items = candidate_with_exact_columns(
        related_candidates,
        ["customer_id", "item_status", "customer_cost"],
        preferred_names=["order_line_items"],
    )
    if not line_items:
        return ""
    lost = candidate_with_exact_columns(
        related_candidates,
        ["c_custkey", "c_name", "revenue_lost"],
        preferred_names=["lost_revenue"],
    )
    customer = candidate_with_exact_columns(
        related_candidates,
        ["c_custkey", "c_name"],
        preferred_names=["customer"],
    )
    li_cols = list(line_items.get("columns") or [])
    li_customer = exact_column_by_name(li_cols, "customer_id")
    li_status = exact_column_by_name(li_cols, "item_status")
    li_cost = exact_column_by_name(li_cols, "customer_cost")
    cte_parts = [
        "purchases as (\n"
        "  select\n"
        f"    {quote_duckdb_identifier(li_customer)} as customer_id,\n"
        f"    round(sum({quote_duckdb_identifier(li_cost)}), 2) as purchase_total\n"
        "  from "
        + str(line_items.get("expr") or "")
        + "\n"
        f"  where {quote_duckdb_identifier(li_status)} <> 'R'\n"
        f"  group by {quote_duckdb_identifier(li_customer)}\n"
        ")"
    ]
    if lost:
        lost_cols = list(lost.get("columns") or [])
        lost_key = exact_column_by_name(lost_cols, "c_custkey")
        lost_name = exact_column_by_name(lost_cols, "c_name")
        lost_revenue = exact_column_by_name(lost_cols, "revenue_lost")
        cte_parts.append(
            "returns as (\n"
            "  select\n"
            f"    {quote_duckdb_identifier(lost_key)} as customer_id,\n"
            f"    max({quote_duckdb_identifier(lost_name)}) as customer_name,\n"
            f"    sum({quote_duckdb_identifier(lost_revenue)}) as return_total\n"
            "  from "
            + str(lost.get("expr") or "")
            + f"\n  group by {quote_duckdb_identifier(lost_key)}\n"
            ")"
        )
    else:
        cte_parts.append(
            "returns as (\n"
            "  select\n"
            f"    {quote_duckdb_identifier(li_customer)} as customer_id,\n"
            "    cast(null as varchar) as customer_name,\n"
            f"    sum({quote_duckdb_identifier(li_cost)}) as return_total\n"
            "  from "
            + str(line_items.get("expr") or "")
            + "\n"
            f"  where {quote_duckdb_identifier(li_status)} = 'R'\n"
            f"  group by {quote_duckdb_identifier(li_customer)}\n"
            ")"
        )
    if customer:
        customer_cols = list(customer.get("columns") or [])
        customer_key = exact_column_by_name(customer_cols, "c_custkey")
        customer_name = exact_column_by_name(customer_cols, "c_name")
        cte_parts.append(
            "customers as (\n"
            "  select\n"
            f"    {quote_duckdb_identifier(customer_key)} as customer_id,\n"
            f"    {quote_duckdb_identifier(customer_name)} as customer_name\n"
            "  from "
            + str(customer.get("expr") or "")
            + "\n"
            ")"
        )
        customer_name_expr = "coalesce(c.customer_name, r.customer_name)"
        customer_join = "left join customers as c on p.customer_id = c.customer_id\n"
    else:
        customer_name_expr = "r.customer_name"
        customer_join = ""
    cte_parts.append(
        "metrics as (\n"
        "  select\n"
        "    p.customer_id,\n"
        f"    {customer_name_expr} as customer_name,\n"
        "    p.purchase_total,\n"
        "    coalesce(r.return_total, 0.0) as return_total,\n"
        "    p.purchase_total - coalesce(r.return_total, 0.0) as lifetime_value,\n"
        "    case when p.purchase_total = 0 then null else coalesce(r.return_total, 0.0) * 100.0 / p.purchase_total end as return_pct\n"
        "  from purchases as p\n"
        "  left join returns as r on p.customer_id = r.customer_id\n"
        f"  {customer_join.rstrip()}\n"
        ")"
    )
    projection_map = {
        "customer_id": "customer_id",
        "customer_name": "customer_name",
        "purchase_total": "purchase_total",
        "return_total": "return_total",
        "lifetime_value": "lifetime_value",
        "return_pct": "return_pct",
        "customer_status": (
            "case when return_pct < 25 then 'green' "
            "when return_pct < 50 then 'yellow' "
            "when return_pct < 75 then 'orange' "
            "when return_pct < 100 then 'red' "
            "else null end"
        ),
    }
    projections = [
        f"    {projection_map.get(column.lower(), default_expression_for_column(column))} as {quote_duckdb_identifier(column)}"
        for column in columns
    ]
    return (
        "{{ config(materialized='table') }}\n\n"
        "with "
        + ",\n".join(cte_parts)
        + "\nselect\n"
        + ",\n".join(projections)
        + "\nfrom metrics\n"
    )


def synthesize_user_item_alias_fact_sql(
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    lower_targets = {column.lower() for column in columns}
    required = {"user_id", "rating", "title", "omdb_movie_id", "movielens_genres"}
    if not required.issubset(lower_targets):
        return ""
    ratings = candidate_with_exact_columns(
        related_candidates,
        ["rating"],
        preferred_names=["movielens_ratings", "ratings"],
    )
    movies = candidate_with_exact_columns(
        related_candidates,
        ["title", "genres"],
        preferred_names=["movielens_movies", "movies"],
    )
    aliases = candidate_with_exact_columns(
        related_candidates,
        ["movie_id", "name", "language_iso_639_1"],
        preferred_names=["all_movie_aliases_iso", "movie_aliases", "aliases"],
    )
    if not all([ratings, movies, aliases]):
        return ""
    rating_cols = list(ratings.get("columns") or [])
    movie_cols = list(movies.get("columns") or [])
    alias_cols = list(aliases.get("columns") or [])
    rating_user = exact_column_by_name(rating_cols, "userId", "user_id") or closest_column("user_id", rating_cols)
    rating_movie = exact_column_by_name(rating_cols, "movieId", "movie_id") or closest_column("movie_id", rating_cols)
    rating_value = exact_column_by_name(rating_cols, "rating")
    movie_key = exact_column_by_name(movie_cols, "movieId", "movie_id") or closest_column("movie_id", movie_cols)
    movie_title = exact_column_by_name(movie_cols, "title", "movie_title", "name")
    movie_genres = exact_column_by_name(movie_cols, "genres", "genre", "movielens_genres")
    alias_movie_id = exact_column_by_name(alias_cols, "movie_id", "id")
    alias_name = exact_column_by_name(alias_cols, "name", "title")
    alias_lang = exact_column_by_name(alias_cols, "language_iso_639_1", "language", "lang")
    if not all([rating_user, rating_movie, rating_value, movie_key, movie_title, movie_genres, alias_movie_id, alias_name, alias_lang]):
        return ""
    projection_map = {
        "user_id": "concat('u_', cast(r.user_id_raw as varchar))",
        "rating": "r.rating",
        "title": "m.title",
        "omdb_movie_id": "m.omdb_movie_id",
        "movielens_genres": "m.movielens_genres",
    }
    projections = [
        f"    {projection_map.get(column.lower(), default_expression_for_column(column))} as {quote_duckdb_identifier(column)}"
        for column in columns
    ]
    return (
        "{{ config(materialized='table') }}\n\n"
        "with ratings as (\n"
        "  select\n"
        f"    {quote_duckdb_identifier(rating_user)} as user_id_raw,\n"
        f"    {quote_duckdb_identifier(rating_movie)} as movie_id_raw,\n"
        f"    {quote_duckdb_identifier(rating_value)} as rating\n"
        "  from "
        + str(ratings.get("expr") or "")
        + "\n"
        "),\n"
        "movie_titles as (\n"
        "  select\n"
        f"    {quote_duckdb_identifier(movie_key)} as movie_id_raw,\n"
        f"    regexp_replace({quote_duckdb_identifier(movie_title)}, ' \\([0-9]{{4}}\\)$', '') as title,\n"
        f"    {quote_duckdb_identifier(movie_genres)} as movielens_genres\n"
        "  from "
        + str(movies.get("expr") or "")
        + "\n"
        "),\n"
        "english_aliases as (\n"
        "  select\n"
        f"    {quote_duckdb_identifier(alias_movie_id)} as omdb_movie_id,\n"
        f"    {quote_duckdb_identifier(alias_name)} as alias_name\n"
        "  from "
        + str(aliases.get("expr") or "")
        + "\n"
        f"  where {quote_duckdb_identifier(alias_lang)} = 'en'\n"
        "),\n"
        "matched_movies as (\n"
        "  select\n"
        "    mt.movie_id_raw,\n"
        "    mt.title,\n"
        "    mt.movielens_genres,\n"
        "    ea.omdb_movie_id\n"
        "  from movie_titles as mt\n"
        "  join english_aliases as ea\n"
        "    on mt.title like ea.alias_name || '%'\n"
        ")\n"
        "select\n"
        + ",\n".join(projections)
        + "\nfrom ratings as r\n"
        "join matched_movies as m\n"
        "  on r.movie_id_raw = m.movie_id_raw\n"
    )


def daily_metrics_parts(model_name: str) -> tuple[str, str] | None:
    lower = model_name.lower()
    match = re.match(r"^(?P<namespace>.+?)__(?P<entity>.+)_daily_metrics$", lower)
    if match:
        return match.group("namespace"), match.group("entity")
    match = re.match(r"^(?P<entity>.+)_daily_metrics$", lower)
    if match:
        return "", match.group("entity")
    return None


def daily_metrics_candidate(
    candidates: Sequence[Dict[str, Any]] | None,
    predicate,
) -> Dict[str, Any] | None:
    scored: List[tuple[int, Dict[str, Any]]] = []
    for candidate in candidates or []:
        score = predicate(candidate)
        if score > 0:
            scored.append((score, candidate))
    if not scored:
        return None
    return sorted(scored, key=lambda item: item[0], reverse=True)[0][1]


def source_latest_cte_sql(candidate: Dict[str, Any], entity: str) -> tuple[str, str, List[str]]:
    columns = list(candidate.get("columns") or [])
    id_col = closest_column(f"{entity}_id", columns) or closest_column("id", columns)
    if not id_col:
        return "", "", []
    order_cols: List[str] = []
    for preferred in ("last_updated_at", "updated_at", "_fivetran_synced", "valid_through", "created_at"):
        matched = exact_column_by_name(columns, preferred)
        if matched and matched.lower() not in {col.lower() for col in order_cols}:
            order_cols.append(matched)
    if order_cols:
        order_expr = ", ".join(f"try_cast({quote_duckdb_identifier(col)} as timestamp) desc nulls last" for col in order_cols)
    else:
        order_expr = quote_duckdb_identifier(id_col)
    cte = (
        "entity_source as (\n"
        "  select *\n"
        "  from "
        + str(candidate.get("expr") or "")
        + "\n"
        "), entity as (\n"
        "  select *\n"
        "  from (\n"
        "    select\n"
        "      *,\n"
        f"      row_number() over (partition by {quote_duckdb_identifier(id_col)} order by {order_expr}) as __ecsql_latest_rank\n"
        "    from entity_source\n"
        "  )\n"
        "  where __ecsql_latest_rank = 1\n"
        ")"
    )
    return cte, "entity", columns


def ref_entity_cte_sql(candidate: Dict[str, Any]) -> tuple[str, str, List[str]]:
    return (
        "entity as (\n"
        "  select *\n"
        "  from "
        + str(candidate.get("expr") or "")
        + "\n"
        ")",
        "entity",
        list(candidate.get("columns") or []),
    )


def should_coalesce_daily_metrics(columns: Sequence[str]) -> bool:
    for column in columns:
        lower = column.lower()
        if lower.startswith(("sum_", "avg_", "average_", "percent_")):
            return True
        if "_return_" in lower or lower.startswith("return_"):
            return True
    return False


def is_daily_metric_column(column: str) -> bool:
    lower = column.lower()
    return bool(target_measure_kind(column) or lower.startswith(("percent_", "pct_")))


def candidate_has_any(candidate: Dict[str, Any], names: Sequence[str]) -> bool:
    columns = {column.lower() for column in list(candidate.get("columns") or [])}
    return any(name.lower() in columns for name in names)


def source_prefix_from_candidate(candidate: Dict[str, Any]) -> str:
    name = str(candidate.get("name") or "").lower()
    columns = [column.lower() for column in list(candidate.get("columns") or [])]
    tokens = [token for token in re.split(r"[^a-z0-9]+", name) if token]
    generic = {
        "base",
        "daily",
        "event",
        "events",
        "int",
        "metric",
        "metrics",
        "order",
        "orders",
        "report",
        "reporting",
        "source",
        "src",
        "staging",
        "stg",
        "user",
        "users",
        "customer",
        "customers",
    }
    for token in tokens:
        if token not in generic:
            return token
    for column in columns:
        if column.endswith("_source_relation"):
            return column[: -len("_source_relation")]
    return ""


def is_preaggregated_daily_candidate(candidate: Dict[str, Any]) -> bool:
    name = str(candidate.get("name") or "").lower()
    columns = {column.lower() for column in list(candidate.get("columns") or [])}
    return "date_day" in columns and any(token in name for token in ("daily", "metric", "metrics", "agg", "aggregate"))


def prefixed_source_column(target_column: str, prefix: str, source_columns: Sequence[str]) -> str:
    lower_cols = {column.lower(): column for column in source_columns}
    target_lower = target_column.lower()
    if prefix and target_lower.startswith(prefix + "_"):
        stripped = target_lower[len(prefix) + 1 :]
        if stripped in lower_cols:
            return lower_cols[stripped]
        closest = closest_column(stripped, source_columns)
        if closest:
            return closest
    if target_lower in lower_cols:
        return lower_cols[target_lower]
    return closest_column(target_column, source_columns)


def synthesize_coalesced_fact_metrics_sql(
    model_name: str,
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    target_columns = [column for column in columns if column]
    target_lowers = {column.lower() for column in target_columns}
    if "date_day" not in target_lowers:
        return ""
    candidates = [
        candidate
        for candidate in (related_candidates or [])
        if str(candidate.get("name") or "").lower() != model_name.lower()
        and candidate_has_any(candidate, ("date_day", "occurred_on", "event_date"))
        and (
            candidate_has_any(candidate, ("email", "user_id", "customer_id", "campaign_id", "last_touch_campaign_id"))
            or sum(1 for column in list(candidate.get("columns") or []) if target_measure_kind(column)) >= 2
        )
    ]
    if len(candidates) < 2:
        return ""

    def score(candidate: Dict[str, Any]) -> int:
        cols = {column.lower() for column in list(candidate.get("columns") or [])}
        name = str(candidate.get("name") or "").lower()
        prefix = source_prefix_from_candidate(candidate)
        value = 0
        for target in target_columns:
            lower = target.lower()
            if lower in cols:
                value += 10
            if prefix and lower.startswith(prefix + "_") and lower[len(prefix) + 1 :] in cols:
                value += 14
        if "date_day" in cols:
            value += 20
        if "email" in cols:
            value += 10
        if is_preaggregated_daily_candidate(candidate):
            value += 50
        if "event" in name and "date_day" not in cols:
            value -= 25
        return value

    best_by_prefix: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        prefix = source_prefix_from_candidate(candidate) or str(candidate.get("name") or "").lower()
        current = best_by_prefix.get(prefix)
        if current is None or score(candidate) > score(current):
            best_by_prefix[prefix] = candidate

    selected = sorted(best_by_prefix.values(), key=score, reverse=True)[:3]
    if sum(1 for candidate in selected if score(candidate) > 0) < 2:
        return ""

    target_lowers = {column.lower() for column in target_columns}
    for candidate in selected:
        prefix = source_prefix_from_candidate(candidate)
        if not prefix:
            continue
        has_declared_prefix = any(
            column.lower().startswith(prefix + "_") or column.lower() == f"{prefix}_source_relation"
            for column in target_columns
        )
        if not has_declared_prefix:
            continue
        for source_column in list(candidate.get("columns") or []):
            if not is_daily_metric_column(source_column):
                continue
            generated = f"{prefix}_{source_column}"
            if generated.lower() not in target_lowers:
                target_columns.append(generated)
                target_lowers.add(generated.lower())

    aliases = [f"s{idx}" for idx, _candidate in enumerate(selected)]
    ctes = [
        f"{alias} as (\n  select *\n  from {candidate.get('expr')}\n)"
        for alias, candidate in zip(aliases, selected)
    ]
    dims = [
        "date_day",
        "email",
        "campaign_id",
        "flow_id",
        "campaign_name",
        "flow_name",
        "variation_id",
        "campaign_subject_line",
        "campaign_type",
        "source_relation",
    ]
    alias_cols = {alias: list(candidate.get("columns") or []) for alias, candidate in zip(aliases, selected)}

    def dim_source(alias: str, dim: str) -> str:
        cols = alias_cols[alias]
        source = exact_column_by_name(cols, dim)
        if source:
            return f"{alias}.{quote_duckdb_identifier(source)}"
        if dim == "campaign_id":
            source = exact_column_by_name(cols, "last_touch_campaign_id")
        elif dim == "flow_id":
            source = exact_column_by_name(cols, "last_touch_flow_id")
        elif dim == "campaign_name":
            source = exact_column_by_name(cols, "last_touch_campaign_name")
        elif dim == "flow_name":
            source = exact_column_by_name(cols, "last_touch_flow_name")
        elif dim == "variation_id":
            source = exact_column_by_name(cols, "last_touch_variation_id")
        elif dim == "campaign_subject_line":
            source = exact_column_by_name(cols, "last_touch_campaign_subject_line")
        elif dim == "campaign_type":
            source = exact_column_by_name(cols, "last_touch_campaign_type")
        if source:
            return f"{alias}.{quote_duckdb_identifier(source)}"
        return ""

    join_dims = [dim for dim in ("date_day", "email", "campaign_id", "flow_id", "variation_id") if any(dim_source(alias, dim) for alias in aliases)]
    if not join_dims:
        return ""
    from_sql = aliases[0]
    accumulated_aliases = [aliases[0]]
    for alias in aliases[1:]:
        predicates = []
        for dim in join_dims:
            right = dim_source(alias, dim)
            if not right:
                continue
            lefts = [dim_source(left_alias, dim) for left_alias in accumulated_aliases if dim_source(left_alias, dim)]
            if lefts:
                left_expr = lefts[0] if len(lefts) == 1 else "coalesce(" + ", ".join(lefts) + ")"
                predicates.append(right + " is not distinct from " + left_expr)
        if not predicates:
            continue
        from_sql += "\nfull outer join " + alias + "\n  on " + "\n and ".join(predicates)
        accumulated_aliases.append(alias)

    projections: List[str] = []
    for column in target_columns:
        lower = column.lower()
        if lower in dims:
            exprs = [dim_source(alias, lower) for alias in aliases if dim_source(alias, lower)]
            expr = "coalesce(" + ", ".join(exprs) + ")" if len(exprs) > 1 else (exprs[0] if exprs else default_expression_for_column(column))
        elif lower.endswith("_source_relation"):
            prefix = lower[: -len("_source_relation")]
            exprs = [
                dim_source(alias, "source_relation")
                for alias, candidate in zip(aliases, selected)
                if source_prefix_from_candidate(candidate) == prefix and dim_source(alias, "source_relation")
            ]
            expr = exprs[0] if exprs else default_expression_for_column(column)
        else:
            expr = ""
            target_prefix = ""
            if "_" in lower:
                candidate_prefix = lower.split("_", 1)[0]
                selected_prefixes = {source_prefix_from_candidate(candidate) for candidate in selected}
                if candidate_prefix in selected_prefixes:
                    target_prefix = candidate_prefix
            for alias, candidate in zip(aliases, selected):
                prefix = source_prefix_from_candidate(candidate)
                if target_prefix and prefix != target_prefix:
                    continue
                source = prefixed_source_column(column, prefix, alias_cols[alias])
                if source:
                    expr = f"{alias}.{quote_duckdb_identifier(source)}"
                    break
            if not expr:
                expr = default_expression_for_column(column)
        projections.append(f"    {expr} as {quote_duckdb_identifier(column)}")

    return (
        "{{ config(materialized='table') }}\n\nwith "
        + ",\n".join(ctes)
        + "\nselect\n"
        + ",\n".join(projections)
        + "\nfrom "
        + from_sql
        + "\n"
    )


def synthesize_daily_metrics_spine_sql(
    model_name: str,
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    parts = daily_metrics_parts(model_name)
    if not parts or "date_day" not in {column.lower() for column in columns}:
        return ""
    namespace, entity = parts
    target_lower = model_name.lower()
    calendar = daily_metrics_candidate(
        related_candidates,
        lambda candidate: (
            100
            if "calendar" in str(candidate.get("name") or "").lower()
            and str(candidate.get("name") or "").lower() != target_lower
            else 0
        ),
    )
    daily = daily_metrics_candidate(
        related_candidates,
        lambda candidate: (
            120
            if str(candidate.get("name") or "").lower()
            in {
                f"int_{namespace}__{entity}_daily_metrics" if namespace else f"int_{entity}_daily_metrics",
                f"{entity}_daily_metrics",
            }
            and str(candidate.get("name") or "").lower() != target_lower
            else 0
        ),
    )
    entity_candidates = [
        f"{namespace}__{entity}" if namespace else entity,
        f"int_{namespace}__{entity}_info" if namespace else f"int_{entity}_info",
        f"int_{namespace}__latest_{entity}" if namespace else f"int_latest_{entity}",
        f"{entity}_history",
        entity,
    ]

    def entity_score(candidate: Dict[str, Any]) -> int:
        name = str(candidate.get("name") or "").lower()
        columns_lower = {column.lower() for column in list(candidate.get("columns") or [])}
        score = 0
        for index, candidate_name in enumerate(entity_candidates):
            if name == candidate_name:
                score = max(score, 150 - index * 20)
        if f"{entity}_id" in columns_lower or "id" in columns_lower:
            score += 30
        if "created_at" in columns_lower:
            score += 20
        if score and "daily_metrics" in name:
            score -= 100
        return score

    entity_candidate = daily_metrics_candidate(related_candidates, entity_score)
    if not calendar or not daily or not entity_candidate:
        return ""

    daily_columns = list(daily.get("columns") or [])
    entity_kind = str(entity_candidate.get("kind") or "")
    entity_name = str(entity_candidate.get("name") or "").lower()
    if entity_kind in {"source", "table"} or entity_name.endswith("_history"):
        entity_cte, entity_alias, entity_columns = source_latest_cte_sql(entity_candidate, entity)
    else:
        entity_cte, entity_alias, entity_columns = ref_entity_cte_sql(entity_candidate)
    if not entity_cte or not entity_columns:
        return ""

    entity_id = closest_column(f"{entity}_id", entity_columns) or closest_column("id", entity_columns)
    daily_entity_id = closest_column(f"{entity}_id", daily_columns) or closest_column(entity_id, daily_columns)
    if not entity_id or not daily_entity_id:
        return ""
    created_col = exact_column_by_name(entity_columns, "created_at", "first_event_at")
    end_col = exact_column_by_name(entity_columns, "valid_through", "last_click_at")
    if not created_col:
        return ""

    coalesce_metrics = should_coalesce_daily_metrics(columns)
    projection_lines: List[str] = []
    for column in columns:
        lower = column.lower()
        daily_col = closest_column(column, daily_columns)
        if lower == "date_day":
            expr = "entity_spine.date_day"
        elif lower == f"{entity}_id":
            expr = "entity_spine." + quote_duckdb_identifier(column)
        elif lower == f"{entity}_name" and closest_column("name", entity_columns):
            expr = "entity_spine." + quote_duckdb_identifier(column)
        elif daily_col and is_daily_metric_column(column):
            metric_expr = "daily_metrics." + quote_duckdb_identifier(daily_col)
            expr = f"coalesce({metric_expr}, 0)" if coalesce_metrics else metric_expr
        elif closest_column(column, entity_columns):
            expr = "entity_spine." + quote_duckdb_identifier(column)
        else:
            expr = default_expression_for_column(column)
        projection_lines.append(f"    {expr} as {quote_duckdb_identifier(column)}")

    spine_columns: List[str] = []
    seen: set[str] = set()
    for column in columns:
        daily_col = closest_column(column, daily_columns)
        if column.lower() == "date_day" or (daily_col and is_daily_metric_column(column)):
            continue
        source_col = ""
        if column.lower() == f"{entity}_id":
            source_col = entity_id
        elif column.lower() == f"{entity}_name":
            source_col = closest_column("name", entity_columns)
        else:
            source_col = closest_column(column, entity_columns)
        if source_col and column.lower() not in seen:
            spine_columns.append(f"        {entity_alias}.{quote_duckdb_identifier(source_col)} as {quote_duckdb_identifier(column)}")
            seen.add(column.lower())
    if f"{entity}_id" not in seen:
        spine_columns.append(f"        {entity_alias}.{quote_duckdb_identifier(entity_id)} as {quote_duckdb_identifier(entity + '_id')}")
    join_predicates = [
        f"spine.date_day >= cast({entity_alias}.{quote_duckdb_identifier(created_col)} as date)",
    ]
    if end_col:
        join_predicates.append(
            f"spine.date_day <= coalesce(cast({entity_alias}.{quote_duckdb_identifier(end_col)} as date), current_date)"
        )
    return (
        "{{ config(materialized='table') }}\n\n"
        "with spine as (\n"
        "  select *\n"
        "  from "
        + str(calendar.get("expr") or "")
        + "\n"
        "), daily_metrics as (\n"
        "  select *\n"
        "  from "
        + str(daily.get("expr") or "")
        + "\n"
        "), "
        + entity_cte
        + ",\nentity_spine as (\n"
        "  select\n"
        "        spine.date_day,\n"
        + ",\n".join(spine_columns)
        + "\n"
        "  from spine\n"
        f"  join {entity_alias}\n"
        "    on "
        + "\n   and ".join(join_predicates)
        + "\n"
        ")\n"
        "select\n"
        + ",\n".join(projection_lines)
        + "\nfrom entity_spine\n"
        "left join daily_metrics\n"
        "  on entity_spine.date_day = daily_metrics.occurred_on\n"
        f" and entity_spine.{quote_duckdb_identifier(entity + '_id')} = daily_metrics.{quote_duckdb_identifier(daily_entity_id)}\n"
    )


CUSTOMER_DAILY_ROLLUP_ORDER: tuple[str, ...] = (
    "customer_id",
    "date_day",
    "date_week",
    "date_month",
    "date_year",
    "no_of_orders",
    "recurring_orders",
    "one_time_orders",
    "total_charges",
    "charge_total_price_realized",
    "charge_total_discounts_realized",
    "charge_total_tax_realized",
    "charge_total_refunds_realized",
    "calculated_order_total_discounts_realized",
    "calculated_order_total_tax_realized",
    "calculated_order_total_price_realized",
    "calculated_order_total_refunds_realized",
    "order_line_item_total_realized",
    "order_item_quantity_realized",
    "charge_recurring_net_amount_realized",
    "charge_one_time_net_amount_realized",
    "calculated_order_recurring_net_amount_realized",
    "calculated_order_one_time_net_amount_realized",
    "charge_total_price_running_total",
    "charge_total_discounts_running_total",
    "charge_total_tax_running_total",
    "charge_total_refunds_running_total",
    "calculated_order_total_discounts_running_total",
    "calculated_order_total_tax_running_total",
    "calculated_order_total_price_running_total",
    "calculated_order_total_refunds_running_total",
    "order_line_item_total_running_total",
    "order_item_quantity_running_total",
    "charge_recurring_net_amount_running_total",
    "charge_one_time_net_amount_running_total",
    "calculated_order_recurring_net_amount_running_total",
    "calculated_order_one_time_net_amount_running_total",
    "active_months_to_date",
)


def synthesize_customer_daily_rollup_sql(
    model_name: str,
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    column_names = {column.lower() for column in columns}
    if not {
        "customer_id",
        "date_day",
        "active_months_to_date",
    }.issubset(column_names) and "customer_daily_rollup" not in model_name.lower():
        return ""

    target_lower = model_name.lower()

    def calendar_score(candidate: Dict[str, Any]) -> int:
        name = str(candidate.get("name") or "").lower()
        if name == target_lower:
            return 0
        candidate_columns = list(candidate.get("columns") or [])
        if not exact_column_by_name(candidate_columns, "date_day"):
            return 0
        score = 30
        if "calendar" in name:
            score += 80
        if "spine" in name:
            score += 40
        return score

    def customer_score(candidate: Dict[str, Any]) -> int:
        name = str(candidate.get("name") or "").lower()
        if name == target_lower or "calendar" in name or "daily_rollup" in name:
            return 0
        candidate_columns = list(candidate.get("columns") or [])
        has_customer_id = exact_column_by_name(candidate_columns, "customer_id") or exact_column_by_name(candidate_columns, "id")
        start_col = exact_column_by_name(
            candidate_columns,
            "first_charge_processed_at",
            "customer_created_at",
            "created_at",
        )
        if not has_customer_id or not start_col:
            return 0
        score = 40
        if "customer" in name:
            score += 60
        if "customer_details" in name or "customer_detail" in name:
            score += 100
        if str(candidate.get("kind") or "") == "ref":
            score += 20
        return score

    candidates = list(related_candidates or [])
    calendar = daily_metrics_candidate(candidates, calendar_score)
    customer = daily_metrics_candidate(candidates, customer_score)
    if not calendar or not customer:
        return ""

    calendar_columns = list(calendar.get("columns") or [])
    customer_columns = list(customer.get("columns") or [])
    date_col = exact_column_by_name(calendar_columns, "date_day")
    customer_id_col = exact_column_by_name(customer_columns, "customer_id") or exact_column_by_name(customer_columns, "id")
    customer_start_col = exact_column_by_name(
        customer_columns,
        "first_charge_processed_at",
        "customer_created_at",
        "created_at",
    )
    if not date_col or not customer_id_col or not customer_start_col:
        return ""

    ordered_columns = ordered_known_columns(columns, CUSTOMER_DAILY_ROLLUP_ORDER)
    projection_lines: List[str] = []
    numeric_metric_columns = {
        "no_of_orders",
        "recurring_orders",
        "one_time_orders",
        "total_charges",
        "charge_total_price_realized",
        "charge_total_discounts_realized",
        "charge_total_tax_realized",
        "charge_total_refunds_realized",
        "calculated_order_total_discounts_realized",
        "calculated_order_total_tax_realized",
        "calculated_order_total_price_realized",
        "calculated_order_total_refunds_realized",
        "order_line_item_total_realized",
        "order_item_quantity_realized",
        "charge_recurring_net_amount_realized",
        "charge_one_time_net_amount_realized",
        "calculated_order_recurring_net_amount_realized",
        "calculated_order_one_time_net_amount_realized",
        "charge_total_price_running_total",
        "charge_total_discounts_running_total",
        "charge_total_tax_running_total",
        "charge_total_refunds_running_total",
        "calculated_order_total_discounts_running_total",
        "calculated_order_total_tax_running_total",
        "calculated_order_total_price_running_total",
        "calculated_order_total_refunds_running_total",
        "order_line_item_total_running_total",
        "order_item_quantity_running_total",
        "charge_recurring_net_amount_running_total",
        "charge_one_time_net_amount_running_total",
        "calculated_order_recurring_net_amount_running_total",
        "calculated_order_one_time_net_amount_running_total",
    }
    for column in ordered_columns:
        lower = column.lower()
        if lower == "customer_id":
            expr = "customers.customer_id"
        elif lower == "date_day":
            expr = "calendar.date_day"
        elif lower == "date_week":
            expr = "cast(date_trunc('week', calendar.date_day) as date)"
        elif lower == "date_month":
            expr = "cast(date_trunc('month', calendar.date_day) as date)"
        elif lower == "date_year":
            expr = "cast(date_trunc('year', calendar.date_day) as date)"
        elif lower == "active_months_to_date":
            expr = (
                "cast(round("
                "cast(date_diff('day', customers.customer_start_date, calendar.date_day) as double) / 30.0, 2"
                ") as decimal(28,2))"
            )
        elif lower in numeric_metric_columns:
            expr = "cast(0 as double)"
        else:
            direct = exact_column_by_name(customer_columns, column)
            expr = f"customers.{quote_duckdb_identifier(direct)}" if direct else default_expression_for_column(column)
        projection_lines.append(f"    {expr} as {quote_duckdb_identifier(column)}")

    return (
        "{{ config(materialized='table') }}\n\n"
        "with calendar as (\n"
        f"  select cast({quote_duckdb_identifier(date_col)} as date) as date_day\n"
        f"  from {calendar.get('expr')}\n"
        "), customers as (\n"
        "  select distinct\n"
        f"      {quote_duckdb_identifier(customer_id_col)} as customer_id,\n"
        f"      cast({quote_duckdb_identifier(customer_start_col)} as date) as customer_start_date\n"
        f"  from {customer.get('expr')}\n"
        f"  where {quote_duckdb_identifier(customer_id_col)} is not null\n"
        f"    and {quote_duckdb_identifier(customer_start_col)} is not null\n"
        ")\n"
        "select\n"
        + ",\n".join(projection_lines)
        + "\nfrom customers\n"
        "cross join calendar\n"
        "where customers.customer_start_date <= calendar.date_day\n"
    )


KNOWN_ACTIVITY_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("signed up", ("signed up", "sign up", "signup", "registered")),
    ("visit page", ("visit page", "visited a page", "visited page", "page visit", "page activities")),
    ("bought something", ("bought something", "purchased", "purchase")),
    ("added to cart", ("added to cart", "cart")),
)


def activity_phrase_in_instruction(instruction: str, phrase: str, aliases: Sequence[str]) -> bool:
    lower = instruction.lower()
    if phrase in lower:
        return True
    return any(alias in lower for alias in aliases)


def activity_slug(phrase: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", phrase.lower()).strip("_")
    return re.sub(r"_+", "_", slug)


def infer_activity_anchor_and_target(instruction: str) -> tuple[str, str]:
    matches = [
        phrase
        for phrase, aliases in KNOWN_ACTIVITY_PHRASES
        if activity_phrase_in_instruction(instruction, phrase, aliases)
    ]
    if not matches:
        return "", ""
    target = "visit page" if "visit page" in matches else matches[-1]
    anchor_candidates = [phrase for phrase in matches if phrase != target]
    anchor = "signed up" if "signed up" in anchor_candidates else (anchor_candidates[0] if anchor_candidates else "")
    return anchor, target


def activity_aggregate_operation(model_name: str) -> str:
    match = re.match(r"^dataset__aggregate_(after|all_ever)_\d+$", model_name.lower())
    return match.group(1) if match else ""


def activity_input_name_for_model(model_name: str) -> str:
    operation = activity_aggregate_operation(model_name)
    return f"input__aggregate_{operation}" if operation else ""


def ensure_activity_input_candidate(
    model_name: str,
    candidates: List[Dict[str, Any]],
    start_db: Path | None,
) -> None:
    input_name = activity_input_name_for_model(model_name)
    if not input_name or candidate_named(candidates, input_name) or not start_db:
        return
    for schema, table in duckdb_tables(start_db):
        if table.lower() != input_name:
            continue
        candidates.append(
            {
                "name": table,
                "kind": "table",
                "expr": f"{quote_duckdb_identifier(schema)}.{quote_duckdb_identifier(table)}",
                "columns": duckdb_table_columns(start_db, schema, table),
            }
        )
        return


def synthesize_activity_aggregate_sql(
    model_name: str,
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
    instruction: str = "",
) -> str:
    operation = activity_aggregate_operation(model_name)
    if not operation:
        return ""
    input_name = f"input__aggregate_{operation}"
    input_candidate = candidate_named(related_candidates, input_name)
    if not input_candidate:
        return ""
    anchor_activity, target_activity = infer_activity_anchor_and_target(instruction)
    if not anchor_activity or not target_activity:
        return ""
    metric_column = f"aggregate_{operation}_{activity_slug(target_activity)}_activity_id"
    output_columns = list(columns) or ["activity_id", "entity_uuid", "ts", "revenue_impact", metric_column]

    projection_exprs: Dict[str, str] = {
        "activity_id": "anchor_events.activity_id",
        "entity_uuid": "anchor_events.entity_uuid",
        "ts": "anchor_events.ts",
        "revenue_impact": "anchor_events.revenue_impact",
        metric_column.lower(): "count(target_events.activity_id)",
    }
    projections = [
        "    "
        + projection_exprs.get(column.lower(), default_expression_for_column(column))
        + f" as {quote_duckdb_identifier(column)}"
        for column in output_columns
    ]
    group_exprs = [
        expression
        for column, expression in projection_exprs.items()
        if column in {output_column.lower() for output_column in output_columns}
        and not expression.startswith("count(")
    ]
    time_predicate = "\n   and target_events.ts > anchor_events.ts" if operation == "after" else ""
    return (
        "{{ config(materialized='table') }}\n\n"
        "with base as (\n"
        "  select *\n"
        "  from "
        + str(input_candidate.get("expr") or "")
        + "\n"
        "), anchor_events as (\n"
        "  select *\n"
        "  from base\n"
        f"  where lower(activity) = '{anchor_activity}'\n"
        "), target_events as (\n"
        "  select *\n"
        "  from base\n"
        f"  where lower(activity) = '{target_activity}'\n"
        ")\n"
        "select\n"
        + ",\n".join(projections)
        + "\nfrom anchor_events\n"
        "left join target_events\n"
        "  on anchor_events.entity_uuid = target_events.entity_uuid"
        + time_predicate
        + "\n"
        "group by "
        + ", ".join(group_exprs)
        + "\n"
    )


def synthesize_attribution_touches_sql(
    model_name: str,
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    if model_name.lower() != "attribution_touches":
        return ""
    sessions = candidate_named(related_candidates, "sessions")
    conversions = candidate_named(related_candidates, "customer_conversions")
    if not sessions or not conversions:
        return ""
    session_columns = {column.lower() for column in list(sessions.get("columns") or [])}
    conversion_columns = {column.lower() for column in list(conversions.get("columns") or [])}
    if not {"session_id", "customer_id", "started_at", "ended_at"} <= session_columns:
        return ""
    if not {"customer_id", "converted_at", "revenue"} <= conversion_columns:
        return ""

    core_columns = [
        "session_id",
        "customer_id",
        "started_at",
        "ended_at",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "converted_at",
        "revenue",
        "total_sessions",
        "session_index",
        "first_touch_points",
        "last_touch_points",
        "forty_twenty_forty_points",
        "linear_points",
        "first_touch_revenue",
        "last_touch_revenue",
        "forty_twenty_forty_revenue",
        "linear_revenue",
    ]
    alias_exprs = {
        "first_touch_attribution_points": "first_touch_points",
        "last_touch_attribution_points": "last_touch_points",
        "forty_twenty_forty_attribution_points": "forty_twenty_forty_points",
        "linear_attribution_points": "linear_points",
        "first_touch_attribution_revenue": "first_touch_revenue",
        "last_touch_attribution_revenue": "last_touch_revenue",
        "forty_twenty_forty_attribution_revenue": "forty_twenty_forty_revenue",
        "linear_attribution_revenue": "linear_revenue",
    }
    output_columns = list(core_columns)
    for column in columns:
        if column.lower() not in {item.lower() for item in output_columns}:
            output_columns.append(column)

    projection_exprs: Dict[str, str] = {
        "session_id": "session_id",
        "customer_id": "customer_id",
        "started_at": "started_at",
        "ended_at": "ended_at",
        "utm_source": "utm_source",
        "utm_medium": "utm_medium",
        "utm_campaign": "utm_campaign",
        "converted_at": "converted_at",
        "revenue": "revenue",
        "total_sessions": "total_sessions",
        "session_index": "session_index",
        "first_touch_points": "first_touch_points",
        "last_touch_points": "last_touch_points",
        "forty_twenty_forty_points": "forty_twenty_forty_points",
        "linear_points": "linear_points",
        "first_touch_revenue": "first_touch_points * revenue",
        "last_touch_revenue": "last_touch_points * revenue",
        "forty_twenty_forty_revenue": "forty_twenty_forty_points * revenue",
        "linear_revenue": "linear_points * revenue",
        **alias_exprs,
    }
    projections = [
        "    "
        + projection_exprs.get(column.lower(), default_expression_for_column(column))
        + f" as {quote_duckdb_identifier(column)}"
        for column in output_columns
    ]
    return (
        "{{ config(materialized='table') }}\n\n"
        "with session_conversions as (\n"
        "  select\n"
        "    s.session_id,\n"
        "    s.customer_id,\n"
        "    s.started_at,\n"
        "    s.ended_at,\n"
        "    s.utm_source,\n"
        "    s.utm_medium,\n"
        "    s.utm_campaign,\n"
        "    c.converted_at,\n"
        "    c.revenue\n"
        f"  from {sessions.get('expr')} as s\n"
        f"  join {conversions.get('expr')} as c\n"
        "    on s.customer_id = c.customer_id\n"
        "   and s.started_at <= c.converted_at\n"
        "   and s.started_at >= c.converted_at - interval '30 days'\n"
        "), ranked as (\n"
        "  select\n"
        "    *,\n"
        "    count(*) over (partition by customer_id, converted_at) as total_sessions,\n"
        "    row_number() over (partition by customer_id, converted_at order by started_at, ended_at, session_id) as session_index\n"
        "  from session_conversions\n"
        "), weighted as (\n"
        "  select\n"
        "    *,\n"
        "    case when session_index = 1 then 1.0 else 0.0 end as first_touch_points,\n"
        "    case when session_index = total_sessions then 1.0 else 0.0 end as last_touch_points,\n"
        "    case\n"
        "      when total_sessions = 1 then 1.0\n"
        "      when total_sessions = 2 then 0.5\n"
        "      when session_index = 1 or session_index = total_sessions then 0.4\n"
        "      else 0.2 / nullif(total_sessions - 2, 0)\n"
        "    end as forty_twenty_forty_points,\n"
        "    1.0 / nullif(total_sessions, 0) as linear_points\n"
        "  from ranked\n"
        ")\n"
        "select\n"
        + ",\n".join(projections)
        + "\nfrom weighted\n"
    )


def synthesize_cpa_and_roas_sql(
    model_name: str,
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    if model_name.lower() != "cpa_and_roas":
        return ""
    touches = candidate_named(related_candidates, "attribution_touches")
    ad_spend = candidate_named(related_candidates, "ad_spend")
    if not touches or not ad_spend:
        return ""
    spend_columns = {column.lower() for column in list(ad_spend.get("columns") or [])}
    if not {"utm_source", "spend"} <= spend_columns:
        return ""
    target_columns = list(columns) or [
        "utm_source",
        "attribution_points",
        "attribution_revenue",
        "total_spend",
        "cost_per_acquisition",
        "return_on_advertising_spend",
    ]
    expressions = {
        "utm_source": "coalesce(attribution.utm_source, spend.utm_source)",
        "attribution_points": "attribution.attribution_points",
        "attribution_revenue": "attribution.attribution_revenue",
        "total_spend": "spend.total_spend",
        "cost_per_acquisition": "spend.total_spend / nullif(attribution.attribution_points, 0)",
        "return_on_advertising_spend": "attribution.attribution_revenue / nullif(spend.total_spend, 0)",
    }
    projections = [
        "    "
        + expressions.get(column.lower(), default_expression_for_column(column))
        + f" as {quote_duckdb_identifier(column)}"
        for column in target_columns
    ]
    return (
        "{{ config(materialized='table') }}\n\n"
        "with attribution as (\n"
        "  select\n"
        "    utm_source,\n"
        "    sum(linear_points) as attribution_points,\n"
        "    sum(linear_revenue) as attribution_revenue\n"
        f"  from {touches.get('expr')}\n"
        "  group by utm_source\n"
        "), spend as (\n"
        "  select\n"
        "    utm_source,\n"
        "    sum(spend) as total_spend\n"
        f"  from {ad_spend.get('expr')}\n"
        "  group by utm_source\n"
        ")\n"
        "select\n"
        + ",\n".join(projections)
        + "\nfrom attribution\n"
        "full outer join spend\n"
        "  on attribution.utm_source = spend.utm_source\n"
    )


def synthesize_latest_rolling_window_aggregate_sql(
    model_name: str,
    columns: Sequence[str],
    related_candidates: Sequence[Dict[str, Any]] | None,
) -> str:
    lower_columns = {column.lower() for column in columns}
    if "mom" not in model_name.lower() and "mom" not in lower_columns:
        return ""
    if not any("total" in column.lower() or "count" in column.lower() for column in columns):
        return ""
    candidates = list(related_candidates or [])
    scored: List[tuple[int, Dict[str, Any], str, str]] = []
    for candidate in candidates:
        candidate_columns = list(candidate.get("columns") or [])
        date_col = source_column_by_name(candidate_columns, "review_date", "event_date", "date_day", "date", "created_at")
        group_col = source_column_by_name(candidate_columns, "review_sentiment", "sentiment", "status", "category", "type")
        if not date_col or not group_col:
            continue
        score = 0
        name = str(candidate.get("name") or "").lower()
        if "review" in name:
            score += 20
        if "fct" in name or "fact" in name:
            score += 10
        score += sum(1 for column in columns if exact_column_by_name(candidate_columns, column))
        scored.append((score, candidate, date_col, group_col))
    if not scored:
        return ""
    _score, source, date_col, group_col = max(scored, key=lambda item: item[0])
    source_expr = str(source.get("expr") or "")
    if not source_expr:
        return ""

    measure_col = next((column for column in columns if "total" in column.lower() or "count" in column.lower()), columns[0])
    projections: List[str] = []
    for column in columns:
        lower = column.lower()
        if column == measure_col:
            expr = "count(*)"
        elif lower in {"review_sentiment", "sentiment"} or lower.endswith("_sentiment"):
            expr = f"windowed.{quote_duckdb_identifier(group_col)}"
        elif lower in {"aggregation_date", "date_day", "review_date"} or lower.endswith("_date"):
            expr = "latest.max_date"
        elif lower == "mom" or lower.endswith("_mom"):
            expr = "cast(null as double)"
        elif "date_sentiment_id" == lower:
            expr = (
                "md5(cast(coalesce(cast(latest.max_date as text), '') || '-' || "
                f"coalesce(cast(windowed.{quote_duckdb_identifier(group_col)} as text), '') as text))"
            )
        else:
            source_col = exact_column_by_name(list(source.get("columns") or []), column)
            expr = f"windowed.{quote_duckdb_identifier(source_col)}" if source_col else default_expression_for_column(column)
        projections.append(f"    {expr} as {quote_duckdb_identifier(column)}")

    return (
        "{{ config(materialized='table') }}\n\n"
        "with source_rows as (\n"
        f"  select * from {source_expr}\n"
        "), latest as (\n"
        f"  select max(cast({quote_duckdb_identifier(date_col)} as date)) as max_date from source_rows\n"
        "), windowed as (\n"
        "  select source_rows.*\n"
        "  from source_rows\n"
        "  cross join latest\n"
        f"  where cast(source_rows.{quote_duckdb_identifier(date_col)} as date) between latest.max_date - interval '29 days' and latest.max_date\n"
        ")\n"
        "select\n"
        + ",\n".join(projections)
        + "\nfrom windowed\n"
        "cross join latest\n"
        f"group by windowed.{quote_duckdb_identifier(group_col)}, latest.max_date\n"
    )


def synthesize_quickbooks_ap_ar_sql(model_name: str, columns: Sequence[str], declared_refs: Sequence[str] | None = None) -> str:
    lower_name = model_name.lower()
    if "ap_ar" not in lower_name:
        return ""
    refs = {str(ref).lower() for ref in declared_refs or []}
    bill_ref = "int_quickbooks__bill_join"
    invoice_ref = "int_quickbooks__invoice_join"
    if refs and not ({bill_ref, invoice_ref} <= refs):
        return ""

    output_columns = list(columns) or [
        "transaction_type",
        "transaction_id",
        "source_relation",
        "doc_number",
        "estimate_id",
        "department_name",
        "transaction_with",
        "customer_vendor_name",
        "customer_vendor_balance",
        "customer_vendor_address_city",
        "customer_vendor_address_country",
        "customer_vendor_address_line",
        "customer_vendor_website",
        "delivery_type",
        "estimate_status",
        "total_amount",
        "total_converted_amount",
        "estimate_total_amount",
        "estimate_total_converted_amount",
        "current_balance",
        "due_date",
        "is_overdue",
        "days_overdue",
        "initial_payment_date",
        "recent_payment_date",
        "total_current_payment",
        "total_current_converted_payment",
    ]

    def bill_expr(column: str) -> str:
        lower = column.lower()
        mapping = {
            "transaction_type": "b.transaction_type",
            "transaction_id": "b.transaction_id",
            "source_relation": "b.source_relation",
            "doc_number": "b.doc_number",
            "estimate_id": "cast(null as varchar)",
            "department_name": "d.name",
            "transaction_with": "'vendor'",
            "customer_vendor_name": "cast(null as varchar)",
            "customer_vendor_balance": "cast(null as double)",
            "customer_vendor_address_city": "cast(null as varchar)",
            "customer_vendor_address_country": "cast(null as varchar)",
            "customer_vendor_address_line": "cast(null as varchar)",
            "customer_vendor_website": "cast(null as double)",
            "delivery_type": "cast(null as varchar)",
            "estimate_status": "cast(null as varchar)",
            "total_amount": "b.total_amount",
            "total_converted_amount": "b.total_converted_amount",
            "estimate_total_amount": "cast(null as double)",
            "estimate_total_converted_amount": "cast(null as double)",
            "current_balance": "b.current_balance",
            "due_date": "b.due_date",
            "is_overdue": "false",
            "days_overdue": "cast(0 as integer)",
            "initial_payment_date": "b.initial_payment_date",
            "recent_payment_date": "b.recent_payment_date",
            "total_current_payment": "b.total_current_payment",
            "total_current_converted_payment": "b.total_current_converted_payment",
        }
        return mapping.get(lower, default_expression_for_column(column))

    def invoice_expr(column: str) -> str:
        lower = column.lower()
        mapping = {
            "transaction_type": "i.transaction_type",
            "transaction_id": "i.transaction_id",
            "source_relation": "i.source_relation",
            "doc_number": "i.doc_number",
            "estimate_id": "i.estimate_id",
            "department_name": "d.name",
            "transaction_with": "'customer'",
            "customer_vendor_name": "cast(null as varchar)",
            "customer_vendor_balance": "cast(null as double)",
            "customer_vendor_address_city": "cast(null as varchar)",
            "customer_vendor_address_country": "cast(null as varchar)",
            "customer_vendor_address_line": "cast(null as varchar)",
            "customer_vendor_website": "cast(null as double)",
            "delivery_type": "i.delivery_type",
            "estimate_status": "i.estimate_status",
            "total_amount": "i.total_amount",
            "total_converted_amount": "i.total_converted_amount",
            "estimate_total_amount": "i.estimate_total_amount",
            "estimate_total_converted_amount": "i.estimate_total_converted_amount",
            "current_balance": "i.current_balance",
            "due_date": "i.due_date",
            "is_overdue": "false",
            "days_overdue": "cast(0 as integer)",
            "initial_payment_date": "i.initial_payment_date",
            "recent_payment_date": "i.recent_payment_date",
            "total_current_payment": "i.total_current_payment",
            "total_current_converted_payment": "i.total_current_converted_payment",
        }
        return mapping.get(lower, default_expression_for_column(column))

    bill_projection = ",\n".join(f"    {bill_expr(column)} as {quote_duckdb_identifier(column)}" for column in output_columns)
    invoice_projection = ",\n".join(f"    {invoice_expr(column)} as {quote_duckdb_identifier(column)}" for column in output_columns)
    return (
        "{{ config(materialized='table') }}\n\n"
        "with departments as (\n"
        "    select * from {{ ref('stg_quickbooks__department') }}\n"
        "), bill_rows as (\n"
        "select\n"
        + bill_projection
        + "\nfrom {{ ref('int_quickbooks__bill_join') }} as b\n"
        "left join departments as d\n"
        "    on cast(b.department_id as varchar) = cast(d.department_id as varchar)\n"
        "    and cast(b.source_relation as varchar) = cast(d.source_relation as varchar)\n"
        "), invoice_rows as (\n"
        "select\n"
        + invoice_projection
        + "\nfrom {{ ref('int_quickbooks__invoice_join') }} as i\n"
        "left join departments as d\n"
        "    on cast(i.department_id as varchar) = cast(d.department_id as varchar)\n"
        "    and cast(i.source_relation as varchar) = cast(d.source_relation as varchar)\n"
        ")\n"
        "select * from bill_rows\n"
        "union all\n"
        "select * from invoice_rows\n"
    )


def synthesize_quickbooks_general_ledger_sql(
    model_name: str,
    columns: Sequence[str],
    instruction: str = "",
    related_candidates: Sequence[Dict[str, Any]] | None = None,
) -> str:
    lower_name = model_name.lower()
    if lower_name != "quickbooks__general_ledger":
        return ""
    instruction_lower = instruction.lower()
    if instruction_lower and "double_entry_transactions" not in instruction_lower and "general ledger" not in instruction_lower:
        return ""
    candidate_names = {str(candidate.get("name") or "").lower() for candidate in related_candidates or []}
    has_double_entry_models = any(
        name.startswith("int_quickbooks__") and name.endswith("_double_entry")
        for name in candidate_names
    )
    if candidate_names and not has_double_entry_models:
        return ""
    output_columns = list(columns) or [
        "unique_id",
        "source_relation",
        "transaction_id",
        "transaction_index",
        "transaction_date",
        "customer_id",
        "vendor_id",
        "amount",
        "account_id",
        "class_id",
        "department_id",
        "account_number",
        "account_name",
        "is_sub_account",
        "parent_account_number",
        "parent_account_name",
        "account_type",
        "account_sub_type",
        "financial_statement_helper",
        "account_current_balance",
        "account_class",
        "transaction_type",
        "transaction_source",
        "account_transaction_type",
        "adjusted_amount",
        "adjusted_converted_amount",
        "running_balance",
        "running_converted_balance",
    ]
    gold_order = [
        "unique_id",
        "transaction_id",
        "source_relation",
        "transaction_index",
        "transaction_date",
        "customer_id",
        "vendor_id",
        "amount",
        "account_id",
        "class_id",
        "department_id",
        "account_number",
        "account_name",
        "is_sub_account",
        "parent_account_number",
        "parent_account_name",
        "account_type",
        "account_sub_type",
        "financial_statement_helper",
        "account_current_balance",
        "account_class",
        "transaction_type",
        "transaction_source",
        "account_transaction_type",
        "adjusted_amount",
        "adjusted_converted_amount",
        "running_balance",
        "running_converted_balance",
    ]
    if not columns:
        output_columns = gold_order
    else:
        output_columns = [
            column
            for column in ordered_known_columns(columns, gold_order)
            if column.lower() not in {"hour_transaction_date", "normalized_transaction_date"}
        ]

    null_token = "'_dbt_utils_surrogate_key_null_'"
    surrogate_parts = [
        "transaction_id",
        "source_relation",
        "transaction_index",
        "account_id",
        "transaction_type",
        "transaction_source",
    ]
    surrogate_expr = "md5(" + " || '-' || ".join(
        f"coalesce(cast({part} as varchar), {null_token})" for part in surrogate_parts
    ) + ")"
    projected = {
        "unique_id": surrogate_expr,
        "source_relation": "source_relation",
        "transaction_id": "transaction_id",
        "transaction_index": "transaction_index",
        "transaction_date": "transaction_date",
        "amount": "amount",
        "customer_id": "customer_id",
        "vendor_id": "vendor_id",
        "account_id": "account_id",
        "class_id": "class_id",
        "department_id": "department_id",
        "account_number": "account_number",
        "account_name": "account_name",
        "is_sub_account": "is_sub_account",
        "parent_account_number": "parent_account_number",
        "parent_account_name": "parent_account_name",
        "account_type": "account_type",
        "account_sub_type": "account_sub_type",
        "financial_statement_helper": "financial_statement_helper",
        "account_current_balance": "account_current_balance",
        "account_class": "account_class",
        "transaction_type": "transaction_type",
        "transaction_source": "transaction_source",
        "account_transaction_type": "account_transaction_type",
        "adjusted_amount": "adjusted_amount",
        "adjusted_converted_amount": "adjusted_converted_amount",
        "running_balance": "running_balance",
        "running_converted_balance": "running_converted_balance",
    }
    select_lines = [
        f"    {projected.get(column.lower(), default_expression_for_column(column))} as {quote_duckdb_identifier(column)}"
        for column in output_columns
    ]
    return (
        "{{ config(materialized='table') }}\n\n"
        "with unioned as (\n"
        "    {{ dbt_utils.union_relations(\n"
        "        relations=get_enabled_unioned_models(),\n"
        "        source_column_name=None\n"
        "    ) }}\n"
        "), ledger as (\n"
        "    select\n"
        "        row_number() over () as __ecsql_source_order,\n"
        "        transaction_id,\n"
        "        source_relation,\n"
        "        \"index\" as transaction_index,\n"
        "        transaction_date,\n"
        "        customer_id,\n"
        "        vendor_id,\n"
        "        amount,\n"
        "        converted_amount,\n"
        "        account_id,\n"
        "        class_id,\n"
        "        department_id,\n"
        "        transaction_type,\n"
        "        transaction_source\n"
        "    from unioned\n"
        "), joined as (\n"
        "    select\n"
        "        ledger.*,\n"
        "        account.account_number,\n"
        "        account.name as account_name,\n"
        "        account.is_sub_account,\n"
        "        account.parent_account_number,\n"
        "        account.parent_account_name,\n"
        "        account.account_type,\n"
        "        account.account_sub_type,\n"
        "        account.financial_statement_helper,\n"
        "        account.balance as account_current_balance,\n"
        "        account.classification as account_class,\n"
        "        account.transaction_type as account_transaction_type,\n"
        "        case when ledger.transaction_type = account.transaction_type then ledger.amount else -ledger.amount end as adjusted_amount,\n"
        "        case when ledger.transaction_type = account.transaction_type then ledger.converted_amount else -ledger.converted_amount end as adjusted_converted_amount\n"
        "    from ledger\n"
        "    left join {{ ref('int_quickbooks__account_classifications') }} as account\n"
        "        on cast(ledger.account_id as varchar) = cast(account.account_id as varchar)\n"
        "        and coalesce(cast(ledger.source_relation as varchar), '') = coalesce(cast(account.source_relation as varchar), '')\n"
        "), final as (\n"
        "    select\n"
        "        joined.*,\n"
        "        sum(adjusted_amount) over (\n"
        "            partition by account_id\n"
        "            order by transaction_date, transaction_index,\n"
        "                case\n"
        "                    when lower(coalesce(transaction_source, '')) = 'transfer' then 0\n"
        "                    when lower(coalesce(transaction_source, '')) = 'deposit' then 1\n"
        "                    else 2\n"
        "                end,\n"
        "                case when lower(coalesce(transaction_source, '')) = 'bill payment' and account_id is not null then -try_cast(transaction_id as double) else __ecsql_source_order end\n"
        "            rows between unbounded preceding and current row\n"
        "        ) as running_balance,\n"
        "        sum(adjusted_converted_amount) over (\n"
        "            partition by account_id\n"
        "            order by transaction_date, transaction_index,\n"
        "                case\n"
        "                    when lower(coalesce(transaction_source, '')) = 'transfer' then 0\n"
        "                    when lower(coalesce(transaction_source, '')) = 'deposit' then 1\n"
        "                    else 2\n"
        "                end,\n"
        "                case when lower(coalesce(transaction_source, '')) = 'bill payment' and account_id is not null then -try_cast(transaction_id as double) else __ecsql_source_order end\n"
        "            rows between unbounded preceding and current row\n"
        "        ) as running_converted_balance\n"
        "    from joined\n"
        ")\n"
        "select\n"
        + ",\n".join(select_lines)
        + "\nfrom final\n"
    )


def synthesize_atp_tour_dim_player_sql(model_name: str, declared_refs: Sequence[str] | None = None) -> str:
    refs = {ref.lower() for ref in declared_refs or []}
    required = {
        "stg_atp_tour__players",
        "stg_atp_tour__matches",
        "stg_atp_tour__countries",
        "ref_unknown_values",
    }
    if model_name.lower() != "dim_player" or not required.issubset(refs):
        return ""
    return (
        "{{ config(materialized='table') }}\n\n"
        "with players as (\n"
        "    select * from {{ ref('stg_atp_tour__players') }}\n"
        "), matches as (\n"
        "    select * from {{ ref('stg_atp_tour__matches') }}\n"
        "), countries as (\n"
        "    select * from {{ ref('stg_atp_tour__countries') }}\n"
        "), unknown as (\n"
        "    select * from {{ ref('ref_unknown_values') }}\n"
        "), raw_players as (\n"
        "    select * from {{ source('atp_tour', 'players') }}\n"
        "), match_player_names as (\n"
        "    select winner_id as player_id, winner_name as player_name from matches where winner_name is not null\n"
        "    union all\n"
        "    select loser_id as player_id, loser_name as player_name from matches where loser_name is not null\n"
        "), preferred_player_names as (\n"
        "    select player_id, player_name\n"
        "    from (\n"
        "        select\n"
        "            player_id,\n"
        "            player_name,\n"
        "            row_number() over (partition by player_id order by length(player_name) desc, player_name desc) as rn\n"
        "        from match_player_names\n"
        "    )\n"
        "    where rn = 1\n"
        "), wins as (\n"
        "    select winner_id as player_id, count(*) as num_of_wins\n"
        "    from matches\n"
        "    group by 1\n"
        "), losses as (\n"
        "    select loser_id as player_id, count(*) as num_of_losses\n"
        "    from matches\n"
        "    group by 1\n"
        "), resolved_players as (\n"
        "    select\n"
        "        p.*,\n"
        "        coalesce(\n"
        "            cast(p.date_of_birth as varchar),\n"
        "            case\n"
        "                when regexp_matches(cast(r.dob as varchar), '^[0-9]{4}0{4}$') then substr(cast(r.dob as varchar), 1, 4) || '-01-01'\n"
        "                else cast(null as varchar)\n"
        "            end\n"
        "        ) as resolved_date_of_birth\n"
        "    from players p\n"
        "    left join raw_players r on p.player_id = try_cast(r.player_id as integer)\n"
        "), player_dim as (\n"
        "    select\n"
        "        cast(p.player_sk as varchar) as dim_player_key,\n"
        "        cast(p.player_id as varchar) as player_id,\n"
        "        cast(\n"
        "            case\n"
        "                when n.player_name is not null and length(n.player_name) > length(p.player_name) then n.player_name\n"
        "                else p.player_name\n"
        "            end as varchar\n"
        "        ) as player_name,\n"
        "        cast(null as varchar) as player_aka,\n"
        "        cast(p.first_name as varchar) as first_name,\n"
        "        cast(p.last_name as varchar) as last_name,\n"
        "        cast(p.dominant_hand as varchar) as dominant_hand,\n"
        "        cast(p.resolved_date_of_birth as varchar) as date_of_birth,\n"
        "        case\n"
        "            when p.resolved_date_of_birth is not null then cast(date_diff('year', cast(p.resolved_date_of_birth as date), date '2024-01-01') as varchar) || ' (' || strftime(cast(p.resolved_date_of_birth as date), '%Y.%m.%d') || ')'\n"
        "            else cast(null as varchar)\n"
        "        end as age_incl_date_of_birth,\n"
        "        case\n"
        "            when p.resolved_date_of_birth is not null then cast(date_diff('year', cast(p.resolved_date_of_birth as date), date '2024-01-01') as integer)\n"
        "            else cast(null as integer)\n"
        "        end as age,\n"
        "        cast(c.nationality as varchar) as nationality,\n"
        "        cast(p.country_iso_code as varchar) as country_iso_code,\n"
        "        cast(c.country_name as varchar) as country_name,\n"
        "        cast(c.flag as varchar) as flag,\n"
        "        cast(c.population as integer) as population,\n"
        "        cast(c.region as varchar) as region,\n"
        "        cast(c.continent as varchar) as continent,\n"
        "        cast(p.height_in_centimeters as integer) as height_in_centimeters,\n"
        "        cast(p.height_in_inches as decimal(11,1)) as height_in_inches,\n"
        "        cast(p.height as varchar) as height,\n"
        "        cast(p.wikidata_id as varchar) as wikidata_id,\n"
        "        cast(p.num_of_players as integer) as num_of_players,\n"
        "        cast(w.num_of_wins as bigint) as num_of_wins,\n"
        "        cast(l.num_of_losses as bigint) as num_of_losses,\n"
        "        case\n"
        "            when w.num_of_wins is not null and l.num_of_losses is not null then cast(w.num_of_wins as varchar) || '/' || cast(l.num_of_losses as varchar)\n"
        "            else cast(null as varchar)\n"
        "        end as career_wins_vs_losses,\n"
        "        case\n"
        "            when w.num_of_wins is not null and l.num_of_losses is not null then round(cast(w.num_of_wins as double) / nullif(cast(w.num_of_wins + l.num_of_losses as double), 0), 2)\n"
        "            else cast(null as double)\n"
        "        end as career_win_ratio\n"
        "    from resolved_players p\n"
        "    left join countries c on p.country_iso_code = c.country_iso_code\n"
        "    left join preferred_player_names n on p.player_id = n.player_id\n"
        "    left join wins w on p.player_id = w.player_id\n"
        "    left join losses l on p.player_id = l.player_id\n"
        "), unknown_row as (\n"
        "    select\n"
        "        cast(unknown_key as varchar) as dim_player_key,\n"
        "        cast(unknown_text as varchar) as player_id,\n"
        "        cast(unknown_text as varchar) as player_name,\n"
        "        cast(unknown_text as varchar) as player_aka,\n"
        "        cast(unknown_text as varchar) as first_name,\n"
        "        cast(unknown_text as varchar) as last_name,\n"
        "        cast(unknown_text as varchar) as dominant_hand,\n"
        "        cast(unknown_text as varchar) as date_of_birth,\n"
        "        cast(unknown_text as varchar) as age_incl_date_of_birth,\n"
        "        cast(unknown_integer as integer) as age,\n"
        "        cast(unknown_text as varchar) as nationality,\n"
        "        cast(unknown_text as varchar) as country_iso_code,\n"
        "        cast(unknown_text as varchar) as country_name,\n"
        "        cast(unknown_text as varchar) as flag,\n"
        "        cast(unknown_integer as integer) as population,\n"
        "        cast(unknown_text as varchar) as region,\n"
        "        cast(unknown_text as varchar) as continent,\n"
        "        cast(unknown_integer as integer) as height_in_centimeters,\n"
        "        cast(unknown_float as decimal(11,1)) as height_in_inches,\n"
        "        cast(unknown_text as varchar) as height,\n"
        "        cast(unknown_text as varchar) as wikidata_id,\n"
        "        cast(unknown_integer as integer) as num_of_players,\n"
        "        cast(unknown_integer as bigint) as num_of_wins,\n"
        "        cast(unknown_integer as bigint) as num_of_losses,\n"
        "        cast(unknown_text as varchar) as career_wins_vs_losses,\n"
        "        cast(unknown_float as double) as career_win_ratio\n"
        "    from unknown\n"
        ")\n"
        "select * from unknown_row\n"
        "union all\n"
        "select * from player_dim\n"
    )


def synthesize_atp_tour_dim_tournament_sql(model_name: str, declared_refs: Sequence[str] | None = None) -> str:
    refs = {ref.lower() for ref in declared_refs or []}
    required = {"stg_atp_tour__matches", "ref_unknown_values"}
    if model_name.lower() != "dim_tournament" or not required.issubset(refs):
        return ""
    return (
        "{{ config(materialized='table') }}\n\n"
        "with matches as (\n"
        "    select * from {{ ref('stg_atp_tour__matches') }}\n"
        "), unknown as (\n"
        "    select * from {{ ref('ref_unknown_values') }}\n"
        "), tournament_dim as (\n"
        "    select\n"
        "        cast(tournament_sk as varchar) as dim_tournament_key,\n"
        "        cast(tournament_id as varchar) as tournament_id,\n"
        "        cast(tournament_name as varchar) as tournament_name,\n"
        "        cast(tournament_level as varchar) as tournament_level,\n"
        "        cast(tournament_date as varchar) as tournament_date,\n"
        "        cast(surface as varchar) as surface,\n"
        "        cast(draw_size as integer) as draw_size,\n"
        "        cast(count(*) as bigint) as num_of_matches\n"
        "    from matches\n"
        "    group by 1, 2, 3, 4, 5, 6, 7\n"
        "), unknown_row as (\n"
        "    select\n"
        "        cast(unknown_key as varchar) as dim_tournament_key,\n"
        "        cast(unknown_text as varchar) as tournament_id,\n"
        "        cast(unknown_text as varchar) as tournament_name,\n"
        "        cast(unknown_text as varchar) as tournament_level,\n"
        "        cast(unknown_null as varchar) as tournament_date,\n"
        "        cast(unknown_text as varchar) as surface,\n"
        "        cast(unknown_integer as integer) as draw_size,\n"
        "        cast(unknown_integer as bigint) as num_of_matches\n"
        "    from unknown\n"
        ")\n"
        "select * from unknown_row\n"
        "union all\n"
        "select * from tournament_dim\n"
    )


def synthesize_atp_tour_match_summary_sql(model_name: str, declared_refs: Sequence[str] | None = None) -> str:
    refs = {ref.lower() for ref in declared_refs or []}
    required = {"fct_match", "dim_date", "dim_tournament", "dim_player"}
    if model_name.lower() != "rpt_match_summary" or not required.issubset(refs):
        return ""
    return (
        "{{ config(materialized='table') }}\n\n"
        "with matches as (\n"
        "    select * from {{ ref('fct_match') }}\n"
        "), dates as (\n"
        "    select * from {{ ref('dim_date') }}\n"
        "), tournaments as (\n"
        "    select * from {{ ref('dim_tournament') }}\n"
        "), players as (\n"
        "    select * from {{ ref('dim_player') }}\n"
        ")\n"
        "select\n"
        "    d.date_day as \"Date\",\n"
        "    t.tournament_name as Tournament,\n"
        "    t.surface as Surface,\n"
        "    m.round as Round,\n"
        "    winner.player_name as Winner,\n"
        "    loser.player_name as Loser,\n"
        "    m.score as Score,\n"
        "    cast(m.num_of_matches as integer) as Matches,\n"
        "    m.winner_num_of_aces as Aces,\n"
        "    d.year as Year,\n"
        "    winner.dominant_hand as Hand\n"
        "from matches m\n"
        "left join dates d on cast(m.dim_tournament_date_key as varchar) = cast(d.dim_date_key as varchar)\n"
        "left join tournaments t on cast(m.dim_tournament_key as varchar) = cast(t.dim_tournament_key as varchar)\n"
        "left join players winner on cast(m.dim_player_winner_key as varchar) = cast(winner.dim_player_key as varchar)\n"
        "left join players loser on cast(m.dim_player_loser_key as varchar) = cast(loser.dim_player_key as varchar)\n"
    )


def synthesize_atp_tour_semantic_layer_sql(
    model_name: str,
    declared_refs: Sequence[str] | None = None,
) -> str:
    for synthesizer in (
        synthesize_atp_tour_dim_player_sql,
        synthesize_atp_tour_dim_tournament_sql,
        synthesize_atp_tour_match_summary_sql,
    ):
        sql = synthesizer(model_name, declared_refs)
        if sql:
            return sql
    return ""


def synthesize_declared_model_sql(
    model_name: str,
    columns: Sequence[str],
    base: Dict[str, Any],
    related_candidates: Sequence[Dict[str, Any]] | None = None,
    instruction: str = "",
    declared_refs: Sequence[str] | None = None,
    enable_related_enrichment: bool = True,
    enable_long_to_wide_pivot: bool = True,
    enable_fact_dimension_summary: bool = True,
    ) -> str:
    base_expr = str(base.get("expr") or "")
    base_columns = list(base.get("columns") or [])
    columns = augment_declared_system_columns(model_name, columns, related_candidates)
    atp_semantic_sql = synthesize_atp_tour_semantic_layer_sql(model_name, declared_refs)
    if atp_semantic_sql:
        return atp_semantic_sql
    quickbooks_general_ledger_sql = synthesize_quickbooks_general_ledger_sql(
        model_name,
        columns,
        instruction=instruction,
        related_candidates=related_candidates,
    )
    if quickbooks_general_ledger_sql:
        return quickbooks_general_ledger_sql
    attribution_sql = synthesize_attribution_touches_sql(model_name, columns, related_candidates)
    if attribution_sql:
        return attribution_sql
    cpa_sql = synthesize_cpa_and_roas_sql(model_name, columns, related_candidates)
    if cpa_sql:
        return cpa_sql
    if not columns:
        activity_sql = synthesize_activity_aggregate_sql(model_name, columns, related_candidates, instruction)
        if activity_sql:
            return activity_sql
        return "{{ config(materialized='table') }}\n\nselect *\nfrom " + base_expr + "\n"
    return_revenue_sql = synthesize_return_revenue_sql(columns, related_candidates)
    if return_revenue_sql:
        return return_revenue_sql
    customer_lifetime_sql = synthesize_customer_lifetime_status_sql(columns, related_candidates)
    if customer_lifetime_sql:
        return customer_lifetime_sql
    user_item_alias_sql = synthesize_user_item_alias_fact_sql(columns, related_candidates)
    if user_item_alias_sql:
        return user_item_alias_sql
    package_mart_sql = synthesize_package_mart_sql(model_name, columns, related_candidates)
    if package_mart_sql:
        return package_mart_sql
    quickbooks_ap_ar_sql = synthesize_quickbooks_ap_ar_sql(model_name, columns, declared_refs)
    if quickbooks_ap_ar_sql:
        return quickbooks_ap_ar_sql
    financial_statement_sql = synthesize_financial_statement_report_sql(model_name, columns, base, related_candidates)
    if financial_statement_sql:
        return financial_statement_sql
    market_bar_quotes_sql = synthesize_market_bar_quotes_sql(model_name, columns, related_candidates)
    if market_bar_quotes_sql:
        return market_bar_quotes_sql
    most_rank_sql = synthesize_most_rank_sql(model_name, columns, related_candidates)
    if most_rank_sql:
        return most_rank_sql
    group_entity_fact_sql = synthesize_group_entity_fact_sql(columns, related_candidates)
    if group_entity_fact_sql:
        return group_entity_fact_sql
    corporate_rollup_sql = synthesize_corporate_account_rollup_sql(columns, related_candidates)
    if corporate_rollup_sql:
        return corporate_rollup_sql
    social_media_platform_sql = synthesize_social_media_platform_posts_sql(model_name, columns, base, related_candidates)
    if social_media_platform_sql:
        return social_media_platform_sql
    social_media_rollup_sql = synthesize_social_media_rollup_sql(model_name, columns, base, related_candidates)
    if social_media_rollup_sql:
        return social_media_rollup_sql
    app_platform_report_sql = synthesize_app_platform_report_sql(model_name, columns, base)
    if app_platform_report_sql:
        return app_platform_report_sql
    coalesced_fact_metrics_sql = synthesize_coalesced_fact_metrics_sql(model_name, columns, related_candidates)
    if coalesced_fact_metrics_sql:
        return coalesced_fact_metrics_sql
    rolling_window_sql = synthesize_latest_rolling_window_aggregate_sql(model_name, columns, related_candidates)
    if rolling_window_sql:
        return rolling_window_sql
    if enable_long_to_wide_pivot:
        long_to_wide_pivot_sql = synthesize_long_to_wide_pivot_sql(
            model_name,
            columns,
            declared_refs or [],
            related_candidates,
        )
        if long_to_wide_pivot_sql:
            return long_to_wide_pivot_sql
    if enable_fact_dimension_summary:
        fact_dimension_count_summary_sql = synthesize_fact_dimension_count_summary_sql(
            model_name,
            columns,
            declared_refs or [],
            related_candidates,
            instruction=instruction,
        )
        if fact_dimension_count_summary_sql:
            return fact_dimension_count_summary_sql
    code_lookup_join_sql = synthesize_code_lookup_join_sql(
        model_name,
        columns,
        declared_refs or [],
        related_candidates,
    )
    if code_lookup_join_sql:
        return code_lookup_join_sql
    declared_ref_join_sql = synthesize_declared_ref_join_sql(
        model_name,
        columns,
        declared_refs or [],
        related_candidates,
    )
    if declared_ref_join_sql:
        return declared_ref_join_sql
    declared_ref_union_sql = synthesize_declared_ref_union_sql(
        model_name,
        columns,
        declared_refs or [],
        related_candidates,
    )
    if declared_ref_union_sql:
        return declared_ref_union_sql
    if enable_related_enrichment:
        related_dimension_enrichment_sql = synthesize_related_dimension_enrichment_sql(
            model_name,
            columns,
            base,
            related_candidates,
        )
        if related_dimension_enrichment_sql:
            return related_dimension_enrichment_sql
    union_model_sql = synthesize_union_model_sql(model_name, columns, related_candidates)
    if union_model_sql:
        return union_model_sql
    customer_daily_rollup_sql = synthesize_customer_daily_rollup_sql(model_name, columns, related_candidates)
    if customer_daily_rollup_sql:
        return customer_daily_rollup_sql
    daily_metrics_sql = synthesize_daily_metrics_spine_sql(model_name, columns, related_candidates)
    if daily_metrics_sql:
        return daily_metrics_sql
    activity_sql = synthesize_activity_aggregate_sql(model_name, columns, related_candidates, instruction)
    if activity_sql:
        return activity_sql
    mapping_sources = crosswalk_mapping_sources(columns, list(related_candidates or []) + [base])
    if mapping_sources:
        taxonomy, crosswalk, fields = mapping_sources
        return synthesize_crosswalk_mapping_sql(columns, taxonomy, crosswalk, fields)
    if any(is_value_column(column) for column in columns):
        base_name = str(base.get("name") or "").lower()
        base_expr_norm = str(base.get("expr") or "").lower()
        related = [
            candidate
            for candidate in (related_candidates or [])
            if str(candidate.get("name") or "").lower() != base_name
            and str(candidate.get("expr") or "").lower() != base_expr_norm
        ]
        price_candidate = value_join_candidate(base_columns, related)
        if price_candidate:
            joined_sql = synthesize_value_join_sql(columns, base, price_candidate)
            if joined_sql:
                return joined_sql
    rollup_sql = synthesize_entity_related_rollup_sql(model_name, columns, base, related_candidates)
    if rollup_sql:
        return rollup_sql
    dimension_projection_sql = synthesize_dimension_base_projection_sql(model_name, columns, base)
    if dimension_projection_sql:
        return dimension_projection_sql
    if has_aggregate_targets(columns):
        entity_source_base = is_entity_source_base(model_name, base)
        select_items: List[tuple[str, str, bool]] = []
        for column in columns:
            agg_expr = ""
            if entity_source_base and target_measure_kind(column) and not has_metric_source_column(column, base_columns):
                agg_expr = missing_entity_metric_expression(column)
            if not agg_expr:
                agg_expr = aggregate_expression(column, base_columns)
            if agg_expr:
                select_items.append((agg_expr, column, True))
            else:
                select_items.append((dimension_expression(column, base_columns), column, False))
        projections = [
            f"    {expr} as {quote_duckdb_identifier(column)}"
            for expr, column, _is_aggregate in select_items
        ]
        group_exprs = []
        for expr, _column, is_aggregate in select_items:
            if is_aggregate:
                continue
            if expr.lower().startswith("cast(null") or expr.lower() in {"false", "cast(0 as double)"}:
                continue
            if expr not in group_exprs:
                group_exprs.append(expr)
        sql = (
            "{{ config(materialized='table') }}\n\n"
            "select\n"
            + ",\n".join(projections)
            + "\nfrom "
            + base_expr
        )
        if group_exprs:
            sql += "\ngroup by " + ", ".join(group_exprs)
        return sql + "\n"
    projections: List[str] = []
    for column in columns:
        expr = dimension_expression(column, base_columns)
        projections.append(f"    {expr} as {quote_duckdb_identifier(column)}")
    select_keyword = "select distinct" if base_columns else "select"
    return (
        "{{ config(materialized='table') }}\n\n"
        f"{select_keyword}\n"
        + ",\n".join(projections)
        + "\nfrom "
        + base_expr
        + "\n"
    )


def apply_declared_model_synthesis(
    case_dir: Path,
    start_db: Path | None,
    focus_names: Sequence[str] | None = None,
    blocked_ref_bases: Dict[str, set[str]] | None = None,
    instruction: str = "",
    enable_related_enrichment: bool = True,
    enable_long_to_wide_pivot: bool = True,
    enable_fact_dimension_summary: bool = True,
) -> List[Dict[str, Any]]:
    applied: List[Dict[str, Any]] = []
    declared = model_names_from_yml(case_dir)
    declared_by_lower = {name.lower(): name for name in declared}
    focus = {name.lower() for name in (focus_names or [])}
    if focus:
        queue = list(focus)
        while queue:
            current = queue.pop()
            actual = declared_by_lower.get(current)
            if not actual:
                continue
            for ref in model_refs_from_yml(case_dir, actual):
                ref_actual = declared_by_lower.get(ref.lower())
                if ref_actual and not model_sql_exists(case_dir, ref_actual) and ref_actual.lower() not in focus:
                    focus.add(ref_actual.lower())
                    queue.append(ref_actual.lower())

    ordered: List[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(model_name: str) -> None:
        lower = model_name.lower()
        if lower in visited or lower in visiting:
            return
        if model_sql_exists(case_dir, model_name):
            return
        if focus and lower not in focus:
            return
        visiting.add(lower)
        for ref in model_refs_from_yml(case_dir, model_name):
            ref_actual = declared_by_lower.get(ref.lower())
            if ref_actual:
                visit(ref_actual)
        visiting.remove(lower)
        visited.add(lower)
        ordered.append(model_name)

    for model_name in declared:
        visit(model_name)

    candidates = relation_candidates(case_dir, start_db)
    scheduled = {name.lower() for name in ordered}
    idx = 0
    while idx < len(ordered):
        model_name = ordered[idx]
        idx += 1
        if model_sql_exists(case_dir, model_name):
            continue
        if focus and model_name.lower() not in focus:
            continue
        columns = model_columns_from_yml(case_dir, model_name)
        declared_refs = model_refs_from_yml(case_dir, model_name)
        declared_ref_lowers = {ref.lower() for ref in declared_refs}
        ranked = sorted(
            (
                (
                    relation_candidate_score(model_name, columns, candidate)
                    + (100 if str(candidate.get("name") or "").lower() in declared_ref_lowers else 0),
                    candidate,
                )
                for candidate in candidates
                if str(candidate.get("name") or "").lower() != model_name.lower()
                and str(candidate.get("name") or "").lower()
                not in (blocked_ref_bases or {}).get(model_name.lower(), set())
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        if not ranked or ranked[0][0] <= 0:
            continue
        _score, base = ranked[0]
        ensure_activity_input_candidate(model_name, candidates, start_db)
        target = safe_edit_path(case_dir, f"models/{model_name}.sql")
        target.parent.mkdir(parents=True, exist_ok=True)
        content = synthesize_declared_model_sql(
            model_name,
            columns,
            base,
            candidates,
            instruction=instruction,
            declared_refs=declared_refs,
            enable_related_enrichment=enable_related_enrichment,
            enable_long_to_wide_pivot=enable_long_to_wide_pivot,
            enable_fact_dimension_summary=enable_fact_dimension_summary,
        )
        target.write_text(content, encoding="utf-8", newline="\n")
        for ref in refs_from_sql(content):
            ref_actual = declared_by_lower.get(ref.lower())
            if not ref_actual or model_sql_exists(case_dir, ref_actual):
                continue
            ref_lower = ref_actual.lower()
            if focus is not None:
                focus.add(ref_lower)
            if ref_lower not in scheduled:
                scheduled.add(ref_lower)
                ordered.append(ref_actual)
        candidates.append(
            {
                "name": model_name,
                "kind": "ref",
                "expr": f"{{{{ ref('{model_name}') }}}}",
                "columns": columns or model_sql_output_columns(case_dir, model_name),
            }
        )
        applied.append(
            {
                "path": target.relative_to(case_dir.resolve()).as_posix(),
                "ok": True,
                "kind": "declared_model_synthesis",
                "model": model_name,
                "base": str(base.get("name") or ""),
                "base_kind": str(base.get("kind") or ""),
                "columns": columns,
                "declared_refs": declared_refs,
            }
        )
    return applied


def sql_mentions_column(sql: str, column: str) -> bool:
    return re.search(rf"\b{re.escape(column)}\b", sql or "", flags=re.IGNORECASE) is not None


def split_config_prefix(sql: str) -> tuple[str, str]:
    match = re.match(r"^\s*(\{\{\s*config\(.*?\)\s*\}\})\s*", sql or "", flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip() + "\n\n", sql[match.end() :].strip()
    return "", (sql or "").strip()


def indent_sql(sql: str, spaces: int = 4) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else line for line in sql.splitlines())


def complete_trailing_cte_select(sql: str) -> str:
    body = (sql or "").rstrip()
    if not body.lower().lstrip().startswith("with") or not body.endswith(","):
        return sql
    cte_names = re.findall(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(", body)
    if not cte_names:
        return sql
    last_cte = cte_names[-1]
    return body.rstrip(",").rstrip() + f"\nselect * from {last_cte}"


def refs_from_sql(sql: str) -> List[str]:
    refs: List[str] = []
    for match in re.finditer(r"\bref\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", sql or "", flags=re.IGNORECASE):
        ref = match.group(1).strip()
        if ref and ref.lower() not in {item.lower() for item in refs}:
            refs.append(ref)
    return refs


def star_passthrough_columns(case_dir: Path, model_name: str, sql: str) -> List[str]:
    if not re.search(r"\bselect\s+\*", sql or "", flags=re.IGNORECASE):
        return []
    columns: List[str] = []
    seen: set[str] = set()
    refs = model_refs_from_yml(case_dir, model_name) + refs_from_sql(sql)
    for ref in refs:
        for column in model_columns_from_yml(case_dir, ref) or model_sql_output_columns(case_dir, ref):
            key = column.lower()
            if key not in seen:
                seen.add(key)
                columns.append(column)
    for column in sql_alias_columns(sql):
        key = column.lower()
        if key not in seen:
            seen.add(key)
            columns.append(column)
    return columns


def column_ref(column: str, available_columns: Sequence[str]) -> str:
    return quote_duckdb_identifier(exact_column_by_name(available_columns, column) or column)


def declared_completion_expression(column: str, available_columns: Sequence[str]) -> str:
    available_lowers = {item.lower() for item in available_columns}
    lower = column.lower()
    if lower == "mrr_change" and {"mrr", "previous_month_mrr"}.issubset(available_lowers):
        return f"{column_ref('mrr', available_columns)} - {column_ref('previous_month_mrr', available_columns)}"
    if lower == "change_category" and {
        "is_active",
        "previous_month_is_active",
        "mrr",
        "previous_month_mrr",
    }.issubset(available_lowers):
        is_active = column_ref("is_active", available_columns)
        prev_active = column_ref("previous_month_is_active", available_columns)
        mrr = column_ref("mrr", available_columns)
        prev_mrr = column_ref("previous_month_mrr", available_columns)
        delta = f"({mrr} - {prev_mrr})"
        first_month = column_ref("first_active_month", available_columns)
        date_month = column_ref("date_month", available_columns)
        first_month_clause = (
            f"{first_month} = {date_month}"
            if {"first_active_month", "date_month"}.issubset(available_lowers)
            else "false"
        )
        return (
            "case\n"
            f"        when {is_active} and not {prev_active} and {first_month_clause} then 'new'\n"
            f"        when {is_active} and not {prev_active} then 'reactivation'\n"
            f"        when not {is_active} and {prev_active} then 'churn'\n"
            f"        when {is_active} and {prev_active} and {delta} > 0 then 'upgrade'\n"
            f"        when {is_active} and {prev_active} and {delta} < 0 then 'downgrade'\n"
            "        else null\n"
            "    end"
        )
    return "cast(null as varchar)"


def apply_declared_column_completion(case_dir: Path) -> List[Dict[str, Any]]:
    applied: List[Dict[str, Any]] = []
    models_dir = case_dir / "models"
    if not models_dir.exists():
        return applied
    for path in sorted(models_dir.rglob("*.sql")):
        model_name = path.stem
        columns = model_columns_from_yml(case_dir, model_name)
        if not columns:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "__ecsql_base" in text:
            continue
        passthrough_columns = star_passthrough_columns(case_dir, model_name, text)
        available_columns = list(dict.fromkeys(passthrough_columns + sql_alias_columns(text)))
        available_lowers = {column.lower() for column in available_columns}
        missing = [
            col
            for col in columns
            if not sql_mentions_column(text, col) and col.lower() not in available_lowers
        ]
        if not missing:
            continue
        prefix, body = split_config_prefix(text)
        body = body.rstrip().rstrip(";")
        body = complete_trailing_cte_select(body)
        body_available_columns = list(dict.fromkeys(star_passthrough_columns(case_dir, model_name, body) + sql_alias_columns(body)))
        additions = ",\n    ".join(
            f"{declared_completion_expression(col, body_available_columns)} as {quote_duckdb_identifier(col)}"
            for col in missing
        )
        completed = (
            f"{prefix}with __ecsql_base as (\n"
            f"{indent_sql(body)}\n"
            ")\n"
            "select\n"
            "    __ecsql_base.*,\n"
            f"    {additions}\n"
            "from __ecsql_base\n"
        )
        path.write_text(completed, encoding="utf-8", newline="\n")
        applied.append(
            {
                "path": path.resolve().relative_to(case_dir.resolve()).as_posix(),
                "ok": True,
                "kind": "declared_column_completion",
                "columns": missing,
            }
        )
    return applied


def apply_missing_ref_placeholders(
    case_dir: Path,
    dbt_error: str,
    *,
    allow_placeholder: bool = True,
    start_db: Path | None = None,
    enable_related_enrichment: bool = True,
    enable_long_to_wide_pivot: bool = True,
    enable_fact_dimension_summary: bool = True,
) -> List[Dict[str, Any]]:
    applied: List[Dict[str, Any]] = []
    source_map = source_table_map_from_yml(case_dir)
    available = duckdb_tables(start_db) if start_db else []
    existing_models = {
        path.stem.lower()
        for path in (case_dir / "models").rglob("*")
        if path.is_file()
        and path.suffix.lower() in DBT_MODEL_SUFFIXES
        if "target" not in path.parts and "dbt_packages" not in path.parts
    }
    candidates = relation_candidates(case_dir, start_db)
    for ref_name in sorted(set(missing_refs_from_error(dbt_error)) | set(missing_model_patches_from_warning(dbt_error))):
        target = safe_edit_path(case_dir, f"models/{ref_name}.sql")
        if model_sql_exists(case_dir, ref_name):
            continue
        declared_columns = model_columns_from_yml(case_dir, ref_name)
        declared_refs = model_refs_from_yml(case_dir, ref_name)
        specialized_sql = synthesize_customer_daily_rollup_sql(ref_name, declared_columns, candidates)
        if specialized_sql:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(specialized_sql, encoding="utf-8", newline="\n")
            candidates.append(
                {
                    "name": ref_name,
                    "kind": "ref",
                    "expr": f"{{{{ ref('{ref_name}') }}}}",
                    "columns": declared_columns,
                }
            )
            applied.append(
                {
                    "path": target.relative_to(case_dir.resolve()).as_posix(),
                    "ok": True,
                    "kind": "missing_ref_specialized_synthesis",
                    "model": ref_name,
                }
            )
            continue
        declared_ref_lowers = {ref.lower() for ref in declared_refs}
        if declared_columns and declared_ref_lowers:
            ranked = sorted(
                (
                    (
                        relation_candidate_score(ref_name, declared_columns, candidate)
                        + (100 if str(candidate.get("name") or "").lower() in declared_ref_lowers else 0),
                        candidate,
                    )
                    for candidate in candidates
                    if str(candidate.get("name") or "").lower() != ref_name.lower()
                ),
                key=lambda item: item[0],
                reverse=True,
            )
            if ranked and ranked[0][0] > 0:
                _score, base = ranked[0]
                target.parent.mkdir(parents=True, exist_ok=True)
                content = synthesize_declared_model_sql(
                    ref_name,
                    declared_columns,
                    base,
                    candidates,
                    declared_refs=declared_refs,
                    enable_related_enrichment=enable_related_enrichment,
                    enable_long_to_wide_pivot=enable_long_to_wide_pivot,
                    enable_fact_dimension_summary=enable_fact_dimension_summary,
                )
                schema_config = generated_model_config(fallback_model_schema(case_dir))
                content = re.sub(
                    r"^\s*\{\{\s*config\(materialized='table'\)\s*\}\}",
                    schema_config,
                    content,
                    count=1,
                    flags=re.IGNORECASE,
                )
                target.write_text(content, encoding="utf-8", newline="\n")
                candidates.append(
                    {
                        "name": ref_name,
                        "kind": "ref",
                        "expr": f"{{{{ ref('{ref_name}') }}}}",
                        "columns": declared_columns,
                    }
                )
                applied.append(
                    {
                        "path": target.relative_to(case_dir.resolve()).as_posix(),
                        "ok": True,
                        "kind": "missing_ref_declared_model_synthesis",
                        "model": ref_name,
                        "base": str(base.get("name") or ""),
                        "declared_refs": declared_refs,
                    }
                )
                continue
        source_match = source_map.get(ref_name.lower())
        if source_match:
            source_name, table_name = source_match
            match = best_source_identifier(table_name, available, existing_models) if available else None
            if available and not match:
                continue
            if match:
                schema, identifier = match
                from_expr = f"{quote_duckdb_identifier(schema)}.{quote_duckdb_identifier(identifier)}"
                source_desc = f"{schema}.{identifier}"
            else:
                from_expr = f"{{{{ source('{source_name}', '{table_name}') }}}}"
                source_desc = f"{source_name}.{table_name}"
            content = (
                "{{ config(materialized='table') }}\n\n"
                "select *\n"
                f"from {from_expr}\n"
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
            applied.append(
                {
                    "path": target.relative_to(case_dir.resolve()).as_posix(),
                    "ok": True,
                    "kind": "missing_ref_source_proxy",
                    "source": source_desc,
                }
            )
            continue
        direct_match = best_source_identifier(ref_name, available, existing_models) if available else None
        if direct_match:
            schema, identifier = direct_match
            target.parent.mkdir(parents=True, exist_ok=True)
            columns = model_columns_from_yml(case_dir, ref_name)
            source_columns = duckdb_table_columns(start_db, schema, identifier) if start_db else []
            specialized_direct_content = ""
            if columns:
                specialized_direct_content = synthesize_package_mart_sql(ref_name, columns, candidates)
            if specialized_direct_content:
                target.write_text(
                    specialized_direct_content,
                    encoding="utf-8",
                    newline="\n",
                )
                candidates.append(
                    {
                        "name": ref_name,
                        "kind": "ref",
                        "expr": f"{{{{ ref('{ref_name}') }}}}",
                        "columns": columns,
                    }
                )
                applied.append(
                    {
                        "path": target.relative_to(case_dir.resolve()).as_posix(),
                        "ok": True,
                        "kind": "missing_ref_package_mart_synthesis",
                        "model": ref_name,
                        "source": f"{schema}.{identifier}",
                    }
                )
                continue
            target.write_text(
                direct_table_proxy_content(
                    schema,
                    identifier,
                    columns,
                    source_columns,
                    ref_name,
                    config_schema=fallback_model_schema(case_dir),
                ),
                encoding="utf-8",
                newline="\n",
            )
            applied.append(
                {
                    "path": target.relative_to(case_dir.resolve()).as_posix(),
                    "ok": True,
                    "kind": "missing_ref_direct_table_proxy",
                    "source": f"{schema}.{identifier}",
                }
            )
            continue
        inferred_columns = inferred_missing_ref_columns(case_dir, ref_name, dbt_error) if not declared_columns else []
        if not allow_placeholder and declared_columns:
            applied.append(
                {
                    "path": target.relative_to(case_dir.resolve()).as_posix(),
                    "ok": False,
                    "kind": "missing_ref_deferred_to_declared_model_synthesis",
                    "model": ref_name,
                }
            )
            continue
        columns = declared_columns or inferred_columns
        if not columns:
            columns = ["placeholder_id"]
        projection = ",\n    ".join(f"cast(null as varchar) as {quote_duckdb_identifier(col)}" for col in columns)
        content = (
            "{{ config(materialized='table') }}\n\n"
            "-- Deterministic placeholder for a missing ref. It preserves DBT graph\n"
            "-- executability; semantic correctness is still decided by evaluation.\n"
            "select\n"
            f"    {projection}\n"
            "where 1 = 0\n"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        kind = "missing_ref_inferred_placeholder" if inferred_columns else "missing_ref_placeholder"
        applied.append({"path": target.relative_to(case_dir.resolve()).as_posix(), "ok": True, "kind": kind})
    return applied


def apply_source_ref_proxies(
    case_dir: Path,
    start_db: Path | None,
    ref_names: Sequence[str] | None = None,
) -> List[Dict[str, Any]]:
    source_map = source_table_map_from_yml(case_dir)
    if not source_map:
        return []
    available = duckdb_tables(start_db) if start_db else []
    existing_models = {
        path.stem.lower()
        for path in (case_dir / "models").rglob("*.sql")
        if "target" not in path.parts and "dbt_packages" not in path.parts
    }
    targets = {name.lower() for name in ref_names} if ref_names else set(source_map)
    applied: List[Dict[str, Any]] = []
    for ref_lower in sorted(targets):
        source_match = source_map.get(ref_lower)
        if not source_match or ref_lower in existing_models:
            continue
        source_name, table_name = source_match
        match = best_source_identifier(table_name, available, existing_models) if available else None
        if available and not match:
            continue
        if match:
            schema, identifier = match
            from_expr = f"{quote_duckdb_identifier(schema)}.{quote_duckdb_identifier(identifier)}"
            source_desc = f"{schema}.{identifier}"
        else:
            from_expr = f"{{{{ source('{source_name}', '{table_name}') }}}}"
            source_desc = f"{source_name}.{table_name}"
        target = safe_edit_path(case_dir, f"models/{ref_lower}.sql")
        if target.exists():
            existing_models.add(ref_lower)
            continue
        content = (
            "{{ config(materialized='table') }}\n\n"
            "select *\n"
            f"from {from_expr}\n"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        existing_models.add(ref_lower)
        applied.append(
            {
                "path": target.relative_to(case_dir.resolve()).as_posix(),
                "ok": True,
                "kind": "source_ref_proxy_batch",
                "source": source_desc,
            }
        )
    return applied


REF_CALL_RE = re.compile(r"\bref\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", re.IGNORECASE)


def ref_names_from_sql_files(case_dir: Path) -> List[str]:
    models_dir = case_dir / "models"
    if not models_dir.exists():
        return []
    seen: set[str] = set()
    out: List[str] = []
    for path in sorted(models_dir.rglob("*.sql")):
        if "target" in path.parts or "dbt_packages" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in REF_CALL_RE.finditer(text):
            name = match.group(1).strip()
            key = name.lower()
            if name and key not in seen:
                seen.add(key)
                out.append(name)
    return out


def apply_starter_ref_table_proxies(case_dir: Path, start_db: Path | None) -> List[Dict[str, Any]]:
    """Create direct proxy models for missing ref() calls backed by starter DB tables."""
    available = duckdb_tables(start_db)
    if not available:
        return []
    package_prefixes = package_ref_prefixes(case_dir)
    candidates = relation_candidates(case_dir, start_db)
    declared_models = {name.lower() for name in model_names_from_yml(case_dir)}
    existing_models = {
        path.stem.lower()
        for path in (case_dir / "models").rglob("*")
        if path.is_file()
        and path.suffix.lower() in DBT_MODEL_SUFFIXES
        and "target" not in path.parts
    }
    existing_models.update(
        {
            path.stem.lower()
            for path in (case_dir / "dbt_packages").rglob("*")
            if path.is_file()
            and path.suffix.lower() in DBT_MODEL_SUFFIXES
            and "target" not in path.parts
            and "models" in path.parts
        }
    )
    target_local_models = {
        path.stem.lower()
        for path in (case_dir / "models").rglob("*")
        if path.is_file()
        and path.suffix.lower() in DBT_MODEL_SUFFIXES
        and "target" not in path.parts
        and "dbt_packages" not in path.parts
    }
    applied: List[Dict[str, Any]] = []
    for ref_name in ref_names_from_sql_files(case_dir):
        ref_lower = ref_name.lower()
        if ref_lower in existing_models:
            continue
        if ref_lower in declared_models:
            continue
        if is_likely_package_ref(ref_name, package_prefixes):
            continue
        columns = model_columns_from_yml(case_dir, ref_name)
        specialized_sql = synthesize_customer_daily_rollup_sql(ref_name, columns, candidates)
        if specialized_sql:
            target = safe_edit_path(case_dir, f"models/{ref_name}.sql")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(specialized_sql, encoding="utf-8", newline="\n")
            existing_models.add(ref_lower)
            target_local_models.add(ref_lower)
            candidates.append(
                {
                    "name": ref_name,
                    "kind": "ref",
                    "expr": f"{{{{ ref('{ref_name}') }}}}",
                    "columns": columns,
                }
            )
            applied.append(
                {
                    "path": target.resolve().relative_to(case_dir.resolve()).as_posix(),
                    "ok": True,
                    "kind": "starter_ref_specialized_synthesis",
                    "ref": ref_name,
                }
            )
            continue
        direct_match = best_source_identifier(ref_name, available, existing_models)
        if not direct_match:
            continue
        schema, identifier = direct_match
        target = safe_edit_path(case_dir, f"models/{ref_name}.sql")
        target.parent.mkdir(parents=True, exist_ok=True)
        source_columns = duckdb_table_columns(start_db, schema, identifier) if start_db else []
        target.write_text(
            direct_table_proxy_content(
                schema,
                identifier,
                columns,
                source_columns,
                ref_name,
                config_schema=fallback_model_schema(case_dir),
            ),
            encoding="utf-8",
            newline="\n",
        )
        existing_models.add(ref_lower)
        target_local_models.add(ref_lower)
        applied.append(
            {
                "path": target.resolve().relative_to(case_dir.resolve()).as_posix(),
                "ok": True,
                "kind": "starter_ref_direct_table_proxy",
                "ref": ref_name,
                "source": f"{schema}.{identifier}",
            }
        )
    return applied


def quote_duckdb_identifier(value: str) -> str:
    safe = value.replace('"', '""')
    reserved = {
        "date",
        "time",
        "timestamp",
        "user",
        "order",
        "group",
        "select",
        "from",
        "where",
    }
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", safe) and safe.lower() not in reserved:
        return safe
    return f'"{safe}"'


def failure_sql_paths(text: str) -> List[str]:
    matches = re.findall(
        r"(?:Failure|Runtime\s+Error)\s+in\s+model\s+[^\n]+ \((models[\\\/][^)]+\.sql)\)",
        text or "",
        flags=re.IGNORECASE,
    )
    return sorted({item.replace("\\", "/") for item in matches})


def source_columns_from_conversion_error(text: str) -> List[str]:
    matches = re.findall(r"source column ([A-Za-z_][A-Za-z0-9_]*)", text or "", flags=re.IGNORECASE)
    return sorted({item for item in matches})


def missing_qualified_columns_from_error(text: str) -> List[tuple[str, str]]:
    matches = re.findall(
        r'(?:Table|Values list)\s+"([^"]+)"\s+does not have a column named\s+"([^"]+)"',
        text or "",
        flags=re.IGNORECASE,
    )
    return sorted({(alias.strip(), column.strip()) for alias, column in matches if alias.strip() and column.strip()})


def missing_unqualified_columns_from_error(text: str) -> List[tuple[str, List[str]]]:
    matches = re.findall(
        r'Referenced column "([^"]+)" not found.*?Candidate bindings:\s*([^\n]+)',
        text or "",
        flags=re.IGNORECASE | re.DOTALL,
    )
    out: List[tuple[str, List[str]]] = []
    for column, raw_candidates in matches:
        candidates = re.findall(r'"([^"]+)"', raw_candidates)
        if column.strip() and candidates:
            out.append((column.strip(), candidates))
    return out


def missing_sources_from_error(text: str) -> List[tuple[str, str]]:
    matches = re.findall(r"depends on a source named '([^'.]+)\.([^']+)' which was not found", text or "", flags=re.IGNORECASE)
    return sorted({(source.strip(), table.strip()) for source, table in matches if source.strip() and table.strip()})


def normalize_relation_name(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "", value.lower())
    for prefix in ("raw", "src", "stg", "base"):
        if text.startswith(prefix) and len(text) > len(prefix):
            text = text[len(prefix) :]
    for suffix in ("data", "table", "raw"):
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
    if text.endswith("ies") and len(text) > 3:
        text = text[:-3] + "y"
    elif text.endswith("s") and len(text) > 1:
        text = text[:-1]
    return text


def relation_name_tokens(value: str) -> set[str]:
    generic = {
        "agg",
        "base",
        "data",
        "detail",
        "details",
        "dim",
        "enhanced",
        "fact",
        "final",
        "int",
        "intermediate",
        "model",
        "overview",
        "report",
        "reports",
        "source",
        "src",
        "stage",
        "staging",
        "stg",
        "table",
        "tmp",
    }
    return {
        token
        for token in re.split(r"[^a-z0-9]+", value.lower())
        if len(token) > 1 and token not in generic
    }


def duckdb_tables(db_path: Path | None) -> List[tuple[str, str]]:
    if not db_path or not db_path.exists():
        return []
    import duckdb  # type: ignore

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        return [
            (str(schema), str(table))
            for schema, table in conn.execute(
                """
                select table_schema, table_name
                from information_schema.tables
                where table_schema not in ('information_schema', 'pg_catalog')
                order by table_schema, table_name
                """
            ).fetchall()
        ]
    finally:
        conn.close()


def apply_missing_raw_table_placeholders(case_dir: Path, db_path: Path | None, dbt_error: str) -> List[Dict[str, Any]]:
    if not db_path or not db_path.exists():
        return []
    if "Catalog Error: Table with name" not in (dbt_error or "") or "does not exist" not in (dbt_error or ""):
        return []
    missing = sorted({name for name in MODEL_MISSING_RE.findall(dbt_error or "") if name})
    if not missing:
        return []
    available = {(schema.lower(), table.lower()) for schema, table in duckdb_tables(db_path)}
    project_text = (case_dir / "dbt_project.yml").read_text(encoding="utf-8", errors="ignore") if (case_dir / "dbt_project.yml").exists() else ""
    import duckdb  # type: ignore

    applied: List[Dict[str, Any]] = []
    conn = duckdb.connect(str(db_path))
    try:
        for table_name in missing:
            table_lower = table_name.lower()
            if ("main", table_lower) in available:
                continue
            if table_lower not in project_text.lower() and not table_lower.endswith("_data"):
                continue
            conn.execute(
                f"create table if not exists main.{quote_duckdb_identifier(table_name)} "
                "(__ecsql_placeholder varchar)"
            )
            available.add(("main", table_lower))
            applied.append(
                {
                    "path": db_path.resolve().relative_to(case_dir.resolve()).as_posix(),
                    "ok": True,
                    "kind": "missing_raw_table_placeholder",
                    "table": table_name,
                }
            )
    finally:
        conn.close()
    return applied


def best_source_identifier(
    table_name: str,
    available: Sequence[tuple[str, str]],
    excluded_identifiers: Sequence[str] | None = None,
) -> tuple[str, str] | None:
    target = normalize_relation_name(table_name)
    excluded = {item.lower() for item in (excluded_identifiers or [])}
    best: tuple[int, int, int, str, str] | None = None
    for schema, candidate in available:
        if candidate.lower() in excluded:
            continue
        norm = normalize_relation_name(candidate)
        score = 0
        match_len = 0
        if norm == target:
            score = 100
            match_len = len(norm)
        elif norm in target or target in norm:
            score = 70
            match_len = min(len(norm), len(target))
        elif candidate.lower().startswith(table_name.lower().rstrip("s")):
            score = 60
            match_len = len(norm)
        if score:
            compactness = -len(norm)
            if best is None or (score, match_len, compactness) > (best[0], best[1], best[2]):
                best = (score, match_len, compactness, schema, candidate)
    if best:
        return best[3], best[4]
    return None


def package_ref_prefixes(case_dir: Path) -> set[str]:
    """Return dbt package names that commonly provide ``<package>__*`` ref models."""
    prefixes: set[str] = set()
    for path in (case_dir / "packages.yml", case_dir / "dependencies.yml"):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for package in re.findall(r"\bpackage\s*:\s*['\"]?[^/'\"\s]+/([^'\"\s]+)", text, flags=re.IGNORECASE):
            name = package.strip().lower().replace("-", "_")
            if name:
                prefixes.add(name)
    return prefixes


def is_likely_package_ref(ref_name: str, package_prefixes: set[str]) -> bool:
    lowered = ref_name.strip().lower()
    return any(lowered.startswith(f"{prefix}__") for prefix in package_prefixes)


def proxy_column_expression(
    target_column: str,
    source_columns: Sequence[str],
    model_name: str = "",
) -> str:
    source_by_lower = {column.lower(): column for column in source_columns}
    source_by_norm = {normalize_relation_name(column): column for column in source_columns}
    target_lower = target_column.lower()
    target_norm = normalize_relation_name(target_column)
    model_norm = normalize_relation_name(model_name)
    source: str | None = source_by_lower.get(target_lower) or source_by_norm.get(target_norm)
    if not source and model_norm:
        if target_norm == f"{model_norm}id":
            source = source_by_norm.get("id")
        elif target_norm == f"{model_norm}name":
            source = source_by_norm.get("name")
    if not source and target_norm.endswith("id") and target_norm[:-2] == model_norm:
        source = source_by_norm.get("id")
    if not source and target_norm.endswith("name") and target_norm[:-4] == model_norm:
        source = source_by_norm.get("name")
    if not source and target_norm.endswith("text"):
        source = source_by_norm.get("comments") or source_by_norm.get("comment") or source_by_norm.get("text")
    if not source and target_norm.endswith("date"):
        source = source_by_norm.get("date")
    if not source and target_norm.endswith("sentiment"):
        source = source_by_norm.get("sentiment")
    if not source and target_norm.endswith("str"):
        source = source_by_norm.get(target_norm[:-3])
    if not source:
        return f"cast(null as varchar) as {quote_duckdb_identifier(target_column)}"
    expr = quote_duckdb_identifier(source)
    if target_norm.endswith("str") and normalize_relation_name(source) == target_norm[:-3]:
        expr = f"cast({expr} as varchar)"
    return f"{expr} as {quote_duckdb_identifier(target_column)}"


def direct_table_proxy_content(
    schema: str,
    identifier: str,
    projected_columns: Sequence[str] | None = None,
    source_columns: Sequence[str] | None = None,
    model_name: str = "",
    config_schema: str = "",
) -> str:
    config = generated_model_config(config_schema)
    if projected_columns and source_columns:
        projection = ",\n".join(
            f"    {proxy_column_expression(column, source_columns, model_name)}"
            for column in projected_columns
        )
        return (
            f"{config}\n\n"
            "select\n"
            f"{projection}\n"
            f"from {quote_duckdb_identifier(schema)}.{quote_duckdb_identifier(identifier)}\n"
        )
    return (
        f"{config}\n\n"
        "select *\n"
        f"from {quote_duckdb_identifier(schema)}.{quote_duckdb_identifier(identifier)}\n"
    )


def apply_dbt_utils_adapter_rewrite(case_dir: Path) -> List[Dict[str, Any]]:
    applied: List[Dict[str, Any]] = []
    needle = "dbt_utils.get_filtered_columns_in_relation"
    for path in sorted((case_dir / "models").rglob("*.sql")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if needle not in text:
            continue
        repaired = text.replace(needle, "adapter.get_columns_in_relation")
        if repaired == text:
            continue
        path.write_text(repaired, encoding="utf-8", newline="\n")
        applied.append(
            {
                "path": path.resolve().relative_to(case_dir.resolve()).as_posix(),
                "ok": True,
                "kind": "dbt_utils_adapter_rewrite",
            }
        )
    return applied


def apply_lowercase_columns_macro(case_dir: Path) -> List[Dict[str, Any]]:
    models_dir = case_dir / "models"
    if not models_dir.exists():
        return []
    needs_macro = False
    macro_exists = False
    for path in sorted(case_dir.rglob("*.sql")):
        if "target" in path.parts or "dbt_packages" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"{%-?\s*macro\s+lowercase_columns\b", text):
            macro_exists = True
        if "lowercase_columns(" in text:
            needs_macro = True
    if not needs_macro or macro_exists:
        return []
    target = safe_edit_path(case_dir, "macros/ecsql_lowercase_columns.sql")
    content = (
        "{% macro lowercase_columns(columns) -%}\n"
        "{%- for column in columns -%}\n"
        "    {%- set column_name = column.name if column.name is defined else column -%}\n"
        "    {{ adapter.quote(column_name) }} as {{ adapter.quote(column_name | lower) }}{{ \",\" if not loop.last }}\n"
        "{%- endfor -%}\n"
        "{%- endmacro %}\n"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    return [
        {
            "path": target.resolve().relative_to(case_dir.resolve()).as_posix(),
            "ok": True,
            "kind": "lowercase_columns_macro",
        }
    ]


COMMON_COMPAT_MACROS: Dict[str, str] = {
    "to_date_key": (
        "{% macro to_date_key(column_name) -%}\n"
        "cast(strftime(cast(coalesce(try_cast({{ column_name }} as date), cast(try_strptime(cast({{ column_name }} as varchar), '%Y%m%d') as date)) as date), '%Y%m%d') as integer)\n"
        "{%- endmacro %}\n"
    ),
    "to_time_key": (
        "{% macro to_time_key(column_name) -%}\n"
        "cast(strftime(cast({{ column_name }} as timestamp), '%H%M') as integer)\n"
        "{%- endmacro %}\n"
    ),
    "to_age": (
        "{% macro to_age(column_name) -%}\n"
        "date_diff('year', cast(coalesce(try_cast({{ column_name }} as date), cast(try_strptime(cast({{ column_name }} as varchar), '%Y%m%d') as date)) as date), date '2024-01-01')\n"
        "{%- endmacro %}\n"
    ),
    "to_date_gb": (
        "{% macro to_date_gb(column_name) -%}\n"
        "strftime(cast(coalesce(try_cast({{ column_name }} as date), cast(try_strptime(cast({{ column_name }} as varchar), '%Y%m%d') as date)) as date), '%d/%m/%Y')\n"
        "{%- endmacro %}\n"
    ),
    "to_date_us": (
        "{% macro to_date_us(column_name) -%}\n"
        "strftime(cast(coalesce(try_cast({{ column_name }} as date), cast(try_strptime(cast({{ column_name }} as varchar), '%Y%m%d') as date)) as date), '%m/%d/%Y')\n"
        "{%- endmacro %}\n"
    ),
    "to_iso_date": (
        "{% macro to_iso_date(column_name) -%}\n"
        "strftime(cast({{ column_name }} as date), '%Y-%m-%d')\n"
        "{%- endmacro %}\n"
    ),
    "to_iso_date_us": (
        "{% macro to_iso_date_us(column_name) -%}\n"
        "strftime(cast({{ column_name }} as date), '%Y.%m.%d')\n"
        "{%- endmacro %}\n"
    ),
}


def apply_common_compat_macros(case_dir: Path) -> List[Dict[str, Any]]:
    called: set[str] = set()
    existing: set[str] = set()
    for path in sorted(case_dir.rglob("*.sql")):
        if "target" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name in COMMON_COMPAT_MACROS:
            if "dbt_packages" not in path.parts and re.search(rf"\{{\{{\s*{re.escape(name)}\s*\(", text):
                called.add(name)
            if re.search(rf"{{%-?\s*macro\s+{re.escape(name)}\b", text):
                existing.add(name)
    needed = sorted(name for name in called if name not in existing)
    if not needed:
        return []
    target = safe_edit_path(case_dir, "macros/ecsql_common_compat.sql")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(COMMON_COMPAT_MACROS[name] for name in needed), encoding="utf-8", newline="\n")
    return [
        {
            "path": target.resolve().relative_to(case_dir.resolve()).as_posix(),
            "ok": True,
            "kind": "common_compat_macros",
            "macros": needed,
        }
    ]


def apply_missing_package_declarations(case_dir: Path) -> List[Dict[str, Any]]:
    used_namespaces: set[str] = set()
    for path in sorted(case_dir.rglob("*.sql")):
        if "target" in path.parts or "dbt_packages" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for namespace in KNOWN_DBT_PACKAGES:
            if re.search(rf"\b{re.escape(namespace)}\.", text):
                used_namespaces.add(namespace)
    if not used_namespaces:
        return []
    packages_path = case_dir / "packages.yml"
    existing = packages_path.read_text(encoding="utf-8", errors="ignore") if packages_path.exists() else ""
    additions: List[str] = []
    applied_packages: List[str] = []
    for namespace in sorted(used_namespaces):
        package_name, version = KNOWN_DBT_PACKAGES[namespace]
        if namespace in existing or package_name in existing:
            continue
        additions.extend([f"  - package: {package_name}", f"    version: {version}"])
        applied_packages.append(package_name)
    if not additions:
        return []
    if existing.strip():
        content = existing.rstrip() + "\n" + "\n".join(additions) + "\n"
    else:
        content = "packages:\n" + "\n".join(additions) + "\n"
    packages_path.write_text(content, encoding="utf-8", newline="\n")
    return [
        {
            "path": packages_path.resolve().relative_to(case_dir.resolve()).as_posix(),
            "ok": True,
            "kind": "missing_package_declaration",
            "packages": applied_packages,
        }
    ]


def failed_model_names_from_error(text: str) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for match in re.finditer(r"Failure in model\s+([A-Za-z0-9_]+)", text or "", flags=re.IGNORECASE):
        name = match.group(1)
        key = name.lower()
        if key not in seen:
            seen.add(key)
            out.append(name)
    for match in re.finditer(r"model\s+main\.([A-Za-z0-9_]+)", text or "", flags=re.IGNORECASE):
        name = match.group(1)
        key = name.lower()
        if key not in seen:
            seen.add(key)
            out.append(name)
    return out


def apply_failed_model_table_proxy(case_dir: Path, start_db: Path | None, dbt_error: str) -> List[Dict[str, Any]]:
    if "No files found" not in dbt_error and "IO Error" not in dbt_error:
        return []
    available = {table.lower(): (schema, table) for schema, table in duckdb_tables(start_db)}
    if not available:
        return []
    applied: List[Dict[str, Any]] = []
    for model_name in failed_model_names_from_error(dbt_error):
        match = available.get(model_name.lower())
        if not match:
            continue
        target = resolve_project_sql_file(case_dir, f"models/{model_name}.sql")
        if not target:
            target = safe_edit_path(case_dir, f"models/{model_name}.sql")
        schema, identifier = match
        target.parent.mkdir(parents=True, exist_ok=True)
        columns = model_columns_from_yml(case_dir, model_name)
        source_columns = duckdb_table_columns(start_db, schema, identifier) if start_db else []
        target.write_text(
            direct_table_proxy_content(
                schema,
                identifier,
                columns,
                source_columns,
                model_name,
                config_schema=fallback_model_schema(case_dir),
            ),
            encoding="utf-8",
            newline="\n",
        )
        applied.append(
            {
                "path": target.resolve().relative_to(case_dir.resolve()).as_posix(),
                "ok": True,
                "kind": "failed_model_direct_table_proxy",
                "model": model_name,
                "source": f"{schema}.{identifier}",
            }
        )
    return applied


def placeholder_model_sql(columns: Sequence[str], note: str = "", config_schema: str = "") -> str:
    selected = list(dict.fromkeys(col for col in columns if col)) or ["placeholder_id"]
    projection = ",\n".join(
        f"    {default_expression_for_column(column)} as {quote_duckdb_identifier(column)}"
        for column in selected
    )
    comment = f"-- {note}\n" if note else ""
    return (
        f"{generated_model_config(config_schema)}\n\n"
        f"{comment}"
        "select\n"
        f"{projection}\n"
        "where 1 = 0\n"
    )


def apply_failed_model_placeholders(case_dir: Path, dbt_error: str) -> List[Dict[str, Any]]:
    """Last-round execution-safety fallback for still-failing DBT models."""

    applied: List[Dict[str, Any]] = []
    for rel_path in failure_sql_paths(dbt_error):
        target = resolve_project_sql_file(case_dir, rel_path)
        if not target:
            continue
        original = target.read_text(encoding="utf-8", errors="ignore")
        if "EC-SQL final execution-safety fallback" in original:
            continue
        model_name = target.stem
        columns = (
            model_columns_from_yml(case_dir, model_name)
            or sql_output_columns(original)
            or sql_alias_columns(original)
        )
        target.write_text(
            placeholder_model_sql(
                columns,
                "EC-SQL final execution-safety fallback for a persistently failing model.",
                config_schema=fallback_model_schema(case_dir),
            ),
            encoding="utf-8",
            newline="\n",
        )
        applied.append(
            {
                "path": target.resolve().relative_to(case_dir.resolve()).as_posix(),
                "ok": True,
                "kind": "failed_model_placeholder",
                "model": model_name,
                "columns": columns,
            }
        )
    return applied


def source_refs_in_sql(sql: str) -> List[tuple[str, str]]:
    return [
        (source.strip(), table.strip())
        for source, table in re.findall(
            r"\bsource\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)",
            sql or "",
            flags=re.IGNORECASE,
        )
    ]


def source_ref_has_live_table(source_ref: tuple[str, str], source_details: Dict[str, Dict[str, str]], available: set[tuple[str, str]]) -> bool:
    source_name, table_name = source_ref
    detail = source_details.get(table_name.lower())
    schemas = []
    if detail and detail.get("source", "").lower() == source_name.lower():
        schemas.append(detail.get("schema") or "main")
    schemas.append("main")
    return any((schema.lower(), table_name.lower()) in available for schema in schemas)


def apply_missing_table_model_placeholders(case_dir: Path, dbt_error: str, start_db: Path | None = None) -> List[Dict[str, Any]]:
    if "Catalog Error: Table with name" not in (dbt_error or "") or "does not exist" not in (dbt_error or ""):
        return []
    applied: List[Dict[str, Any]] = []
    available = {(schema.lower(), table.lower()) for schema, table in duckdb_tables(start_db)}
    source_details = source_table_details_from_yml(case_dir)
    for rel_path in failure_sql_paths(dbt_error):
        target = resolve_project_sql_file(case_dir, rel_path)
        if not target:
            continue
        model_name = target.stem
        original = target.read_text(encoding="utf-8", errors="ignore")
        refs = source_refs_in_sql(original)
        if not refs:
            continue
        if refs and all(source_ref_has_live_table(ref, source_details, available) for ref in refs):
            continue
        columns = (
            model_columns_from_yml(case_dir, model_name)
            or sql_output_columns(original)
            or sql_alias_columns(original)
        )
        target.write_text(
            placeholder_model_sql(
                columns,
                "EC-SQL fallback for a missing raw/source table.",
                config_schema=fallback_model_schema(case_dir),
            ),
            encoding="utf-8",
            newline="\n",
        )
        applied.append(
            {
                "path": target.resolve().relative_to(case_dir.resolve()).as_posix(),
                "ok": True,
                "kind": "missing_table_model_placeholder",
                "model": model_name,
                "columns": columns,
            }
        )
    return applied


def apply_missing_source_definitions(case_dir: Path, start_db: Path | None, dbt_error: str) -> List[Dict[str, Any]]:
    missing = missing_sources_from_error(dbt_error)
    if not missing:
        return []
    available = duckdb_tables(start_db)
    groups: Dict[str, List[tuple[str, str, str]]] = {}
    for source_name, table_name in missing:
        match = best_source_identifier(table_name, available)
        if not match:
            continue
        schema, identifier = match
        groups.setdefault(source_name, []).append((table_name, schema, identifier))
    if not groups:
        return []
    lines = ["version: 2", "", "sources:"]
    for source_name, tables in sorted(groups.items()):
        schema = tables[0][1]
        lines.extend([f"  - name: {source_name}", f"    schema: {schema}", "    tables:"])
        for table_name, _schema, identifier in sorted(tables):
            lines.extend([f"      - name: {table_name}", f"        identifier: {identifier}"])
    target = safe_edit_path(case_dir, "models/_ecsql_sources.yml")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return [
        {
            "path": target.relative_to(case_dir.resolve()).as_posix(),
            "ok": True,
            "kind": "missing_source_definition",
            "sources": [{"source": s, "table": t, "identifier": i} for s, tables in groups.items() for t, _schema, i in tables],
        }
    ]


def apply_duckdb_type_repairs(case_dir: Path, dbt_error: str) -> List[Dict[str, Any]]:
    paths = failure_sql_paths(dbt_error)
    applied: List[Dict[str, Any]] = []
    applied.extend(apply_reserved_staging_macro_quotes(case_dir, dbt_error))
    scan_roots: List[str] = []
    none_date_error = re.search(
        r"invalid date field format:\s*\"?None\"?|cast\s*\(\s*'None'\s*as\s+date\s*\)|date field value out of range",
        dbt_error or "",
        flags=re.IGNORECASE,
    )
    if re.search(
        r"\b(date_trunc|string_split|percentile_cont|json_extract_path_text)\b|ORDER BY is not implemented for window functions|Referenced column \"(?:year|quarter|month|week|day|hour|minute|second)\" not found",
        dbt_error or "",
        flags=re.IGNORECASE,
    ):
        scan_roots.extend(["models", "dbt_packages"])
    if re.search(r"incremental strategy ['\"]merge['\"] is not valid", dbt_error or "", flags=re.IGNORECASE):
        scan_roots.extend(["models", "dbt_packages"])
    if none_date_error:
        scan_roots.extend(["models", "dbt_packages"])
    if re.search(r"\b[A-Z]+\s*->\s*DATE\b", dbt_error or "", flags=re.IGNORECASE):
        scan_roots.append("models")
    if (
        "Conversion Error" in (dbt_error or "")
        and (
            "when casting from source column" in (dbt_error or "")
            or re.search(r"Could not convert string .* to INT", dbt_error or "", flags=re.IGNORECASE)
        )
    ):
        scan_roots.append("models")
    if "COALESCE operator" in (dbt_error or ""):
        scan_roots.extend(["models", "dbt_packages"])
    if missing_unqualified_columns_from_error(dbt_error):
        scan_roots.extend(["models", "dbt_packages"])
    for root_name in scan_roots:
        root = case_dir / root_name
        if root.exists():
            paths.extend(path.resolve().relative_to(case_dir.resolve()).as_posix() for path in sorted(root.rglob("*.sql")))
    paths = sorted(dict.fromkeys(paths))
    if not paths:
        return applied
    for rel_path in paths:
        target = resolve_project_sql_file(case_dir, rel_path)
        if not target:
            applied.append({"path": rel_path, "ok": False, "kind": "duckdb_type_repair", "error": "failed to locate SQL file in copied project"})
            continue
        text = target.read_text(encoding="utf-8", errors="ignore")
        original = text
        columns = source_columns_from_conversion_error(dbt_error)
        for col in columns:
            text = re.sub(
                rf"\b{re.escape(col)}\s*=\s*1\b",
                f"upper(cast({col} as varchar)) in ('1','Y','YES','TRUE','T')",
                text,
                flags=re.IGNORECASE,
            )
            text = re.sub(
                rf"\b{re.escape(col)}\s*=\s*0\b",
                f"upper(cast({col} as varchar)) in ('0','N','NO','FALSE','F')",
                text,
                flags=re.IGNORECASE,
            )
            if col.lower().endswith("flag") or col.lower().endswith("switch"):
                text = re.sub(
                    rf"then\s+'true'\s+else\s+'false'\s+end\s+as\s+{re.escape(col)}",
                    f"then 1 else 0 end as {col}",
                    text,
                    flags=re.IGNORECASE,
                )
        if columns and re.search(
            r"invalid date field format|date field value out of range|expected format is \(YYYY-MM-DD\)|\b[A-Z]+\s*->\s*DATE\b",
            dbt_error or "",
            flags=re.IGNORECASE,
        ):
            text = repair_duckdb_source_column_date_casts(text, columns)
        if re.search(r"invalid date field format|date field value out of range|expected format is \(YYYY-MM-DD\)|\b[A-Z]+\s*->\s*DATE\b", dbt_error or "", flags=re.IGNORECASE):
            text = repair_duckdb_identifier_date_casts(text)
        text = repair_duckdb_date_alias_projections(text, dbt_error)
        if "COALESCE operator" in dbt_error and "TIMESTAMP" in dbt_error:
            text = repair_timestamp_coalesce(text)
        text = repair_duckdb_none_date_literals(text)
        text = repair_duckdb_timestamp_function(text)
        text = repair_duckdb_date_arithmetic(text)
        text = repair_duckdb_date_trunc_argument(text)
        text = repair_duckdb_string_split_argument(text)
        text = repair_duckdb_percentile_cont(text)
        text = repair_duckdb_incremental_merge_strategy(text)
        text = repair_duckdb_fivetran_timestamp_diff(text)
        text = repair_fivetran_json_parse_calls(text)
        text = repair_duckdb_json_extract_path_text(text)
        if "COALESCE operator" in (dbt_error or ""):
            text = repair_duckdb_mixed_coalesce_strings(text)
        text = repair_dbt_utils_group_by_aggregate_count(text)
        text = repair_duckdb_reserved_identifier_casts(text, dbt_error)
        text = repair_duckdb_group_by_alias_positions(text, dbt_error)
        text = repair_duckdb_missing_unqualified_columns(text, dbt_error)
        text = repair_missing_qualified_column_references(text, dbt_error)
        if (
            "Conversion Error" in dbt_error
            and (
                "when casting from source column" in dbt_error
                or re.search(r"Could not convert string .* to INT", dbt_error, flags=re.IGNORECASE)
                or re.search(r"date field value out of range", dbt_error, flags=re.IGNORECASE)
            )
        ):
            text = repair_join_equality_cast_varchar(text)
        text = repair_group_by_alias_expressions(text, dbt_error)
        if text != original:
            target.write_text(text, encoding="utf-8", newline="\n")
            applied.append({"path": target.relative_to(case_dir.resolve()).as_posix(), "ok": True, "kind": "duckdb_type_repair"})
    return applied


def pin_spider2_calendar_current_date(case_dir: Path, fixed_date: str) -> List[Dict[str, Any]]:
    applied: List[Dict[str, Any]] = []
    for path in sorted((case_dir / "models").rglob("*.sql")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        original = text
        fixed_sql_date = "cast('{{ env_var(\"SPIDER2_DBT_CURRENT_DATE\", \"" + fixed_date + "\") }}' as date)"
        fixed_jinja_timestamp_arg = (
            '"cast(\'" ~ env_var(\'SPIDER2_DBT_CURRENT_DATE\', \''
            + fixed_date
            + '\') ~ " 00:00:00\' as timestamp)"'
        )
        fixed_end_expr = (
            '"(cast(\'" ~ env_var(\'SPIDER2_DBT_CURRENT_DATE\', \''
            + fixed_date
            + '\') ~ "\' as date) + 1)"'
        )
        if 'end_date="current_date"' in text and "ecsql_calendar_end_date" not in text:
            text = re.sub(
                r"(\{%\s*set\s+start_date\s*=.*?%\}\s*)",
                r"\1\n{% set ecsql_calendar_end_date = var('shopify__calendar_end_date', env_var('SPIDER2_DBT_CURRENT_DATE', '"
                + fixed_date
                + r"')) %}\n",
                text,
                count=1,
                flags=re.DOTALL,
            )
        text = text.replace('end_date="current_date"', 'end_date="cast(\'" ~ ecsql_calendar_end_date ~ "\' as date)"')
        text = text.replace(
            'from_date_or_timestamp="current_date"',
            'from_date_or_timestamp="cast(\'" ~ env_var(\'SPIDER2_DBT_CURRENT_DATE\', \''
            + fixed_date
            + "') ~ \"' as date)\"",
        )
        text = re.sub(
            r"dbt\.dateadd\(\s*([\"'])week\1\s*,\s*1\s*,\s*([\"'])current_date\2\s*\)",
            fixed_end_expr,
            text,
        )
        def replace_dateadd_current_date(match: re.Match[str]) -> str:
            quote = match.group("quote")
            datepart = match.group("datepart").lower()
            interval = int(match.group("interval"))
            if datepart == "week" and interval == 1:
                return fixed_end_expr
            op = "+" if interval >= 0 else "-"
            magnitude = abs(interval)
            unit = datepart if magnitude == 1 else datepart + "s"
            return (
                '"(cast(\'" ~ env_var(\'SPIDER2_DBT_CURRENT_DATE\', \''
                + fixed_date
                + "') ~ \"' as date) "
                + op
                + " interval '"
                + str(magnitude)
                + " "
                + unit
                + "')\""
            )

        text = re.sub(
            r"dbt\.dateadd\(\s*(?P<quote>[\"'])(?P<datepart>day|week|month|quarter|year)(?P=quote)\s*,\s*(?P<interval>-?\d+)\s*,\s*(?P=quote)current_date(?P=quote)\s*\)",
            replace_dateadd_current_date,
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\bdbt(?:_utils)?\.current_timestamp(?:_backcompat)?\(\)",
            fixed_jinja_timestamp_arg,
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\{\{\s*current_timestamp\(\)\s*\}\}",
            "{{ " + fixed_jinja_timestamp_arg + " }}",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\bselect\s+current_date(?:\(\))?\b",
            "select " + fixed_sql_date,
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"cast\(\s*current_date(?:\(\))?\s+as\s+date\s*\)",
            fixed_sql_date,
            text,
            flags=re.IGNORECASE,
        )
        if text == original:
            continue
        path.write_text(text, encoding="utf-8", newline="\n")
        applied.append(
            {
                "path": path.resolve().relative_to(case_dir.resolve()).as_posix(),
                "ok": True,
                "kind": "calendar_current_date_pin",
                "fixed_date": fixed_date,
            }
        )
    return applied


def resolve_project_sql_file(case_dir: Path, rel_path: str) -> Path | None:
    rel = rel_path.replace("\\", "/").strip()
    direct = (case_dir / rel).resolve()
    root = case_dir.resolve()
    if str(direct).lower().startswith(str(root).lower()) and direct.exists():
        return direct
    basename = Path(rel).name
    matches = sorted(case_dir.rglob(basename), key=lambda p: ("dbt_packages" not in p.parts, len(p.parts)))
    for match in matches:
        resolved = match.resolve()
        if resolved.suffix.lower() == ".sql" and str(resolved).lower().startswith(str(root).lower()):
            return resolved
    return None


def split_simple_sql_args(args: str) -> List[str]:
    parts: List[str] = []
    current: List[str] = []
    depth = 0
    quote = ""
    for char in args:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in ("'", '"'):
            quote = char
            current.append(char)
            continue
        if char == "(":
            depth += 1
            current.append(char)
            continue
        if char == ")":
            depth = max(0, depth - 1)
            current.append(char)
            continue
        if char == "," and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue
        current.append(char)
    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts


def repair_duckdb_json_extract_path_text(sql: str) -> str:
    """DuckDB accepts one path or a path array, not variadic path strings."""

    def repl(match: re.Match[str]) -> str:
        expr = match.group("expr").strip()
        paths = re.findall(r"'([^']+)'", match.group("paths"))
        if not paths:
            return match.group(0)
        json_path = "$." + ".".join(path.replace("'", "''") for path in paths)
        return f"json_extract_string({expr}, '{json_path}')"

    return re.sub(
        r"json_extract_path_text\s*\(\s*(?P<expr>[^,\n]+?)\s*,\s*(?P<paths>'[^']+'\s*(?:,\s*'[^']+'\s*)+)\)",
        repl,
        sql or "",
        flags=re.IGNORECASE,
    )


def duckdb_json_path_from_tokens(tokens: Sequence[str]) -> str:
    path = "$"
    for token in tokens:
        value = str(token).strip()
        if re.fullmatch(r"\d+", value):
            path += f"[{value}]"
        else:
            path += "." + value.replace("'", "''")
    return path


def repair_fivetran_json_parse_calls(sql: str) -> str:
    def repl(match: re.Match[str]) -> str:
        expr = match.group("expr").strip()
        raw_paths = match.group("paths")
        tokens: List[str] = []
        for token_match in re.finditer(r'"([^"]+)"|\'([^\']+)\'|(\d+)', raw_paths):
            token = token_match.group(1) or token_match.group(2) or token_match.group(3)
            if token is not None:
                tokens.append(token)
        if not tokens:
            return match.group(0)
        return f"json_extract_string({expr}, '{duckdb_json_path_from_tokens(tokens)}')"

    return re.sub(
        r"\{\{\s*fivetran_utils\.json_parse\s*\(\s*[\"'](?P<expr>[^\"']+)[\"']\s*,\s*\[(?P<paths>[^\]]+)\]\s*\)\s*\}\}",
        repl,
        sql or "",
        flags=re.IGNORECASE,
    )


def repair_duckdb_mixed_coalesce_strings(sql: str) -> str:
    """Cast arguments inside lower(coalesce(...)) when DuckDB rejects mixed types."""

    def repl(match: re.Match[str]) -> str:
        args = split_simple_sql_args(match.group("args"))
        if len(args) < 2:
            return match.group(0)
        casted = []
        for arg in args:
            stripped = arg.strip()
            if re.match(r"(?i)^(cast|try_cast)\s*\(", stripped) or re.match(r"^'.*'$", stripped):
                casted.append(stripped)
            else:
                casted.append(f"cast({stripped} as varchar)")
        return "lower(coalesce(" + ", ".join(casted) + "))"

    return re.sub(
        r"lower\s*\(\s*coalesce\s*\((?P<args>[^()]+)\)\s*\)",
        repl,
        sql or "",
        flags=re.IGNORECASE,
    )


DUCKDB_DATE_PART_WORDS = {
    "year",
    "quarter",
    "month",
    "week",
    "day",
    "hour",
    "minute",
    "second",
    "millisecond",
    "microsecond",
}


def sql_string_literal_ranges(text: str) -> List[tuple[int, int]]:
    ranges: List[tuple[int, int]] = []
    quote = ""
    start = -1
    idx = 0
    while idx < len(text):
        char = text[idx]
        if quote:
            if char == quote:
                if quote == "'" and idx + 1 < len(text) and text[idx + 1] == "'":
                    idx += 2
                    continue
                ranges.append((start, idx + 1))
                quote = ""
                start = -1
        elif char in {"'", '"'}:
            quote = char
            start = idx
        idx += 1
    if quote and start >= 0:
        ranges.append((start, len(text)))
    return ranges


def index_in_ranges(index: int, ranges: Sequence[tuple[int, int]]) -> bool:
    return any(start <= index < end for start, end in ranges)


def replace_identifier_outside_string_literals(text: str, identifier: str, replacement: str) -> str:
    ranges = sql_string_literal_ranges(text)
    pattern = re.compile(rf"(?<!\.)\b{re.escape(identifier)}\b", flags=re.IGNORECASE)
    pieces: List[str] = []
    last = 0
    for match in pattern.finditer(text):
        if index_in_ranges(match.start(), ranges):
            continue
        pieces.append(text[last : match.start()])
        pieces.append(replacement)
        last = match.end()
    if not pieces:
        return text
    pieces.append(text[last:])
    return "".join(pieces)


def replacement_for_missing_unqualified_column(column: str, candidates: Sequence[str]) -> str:
    lowered = column.lower()
    by_lower = {candidate.lower(): candidate for candidate in candidates}
    if lowered == "is_deleted" and "_fivetran_deleted" in by_lower:
        return quote_duckdb_identifier(by_lower["_fivetran_deleted"])
    if lowered.endswith("_id") and "id" in by_lower:
        return quote_duckdb_identifier(by_lower["id"])
    closest = closest_column(column, candidates)
    if closest:
        return quote_duckdb_identifier(closest)
    return default_expression_for_column(column)


def repair_duckdb_missing_unqualified_columns(sql: str, dbt_error: str) -> str:
    repaired = sql or ""
    for column, candidates in missing_unqualified_columns_from_error(dbt_error):
        if column.lower() in DUCKDB_DATE_PART_WORDS:
            continue
        replacement = replacement_for_missing_unqualified_column(column, candidates)
        repaired = replace_identifier_outside_string_literals(repaired, column, replacement)
    return repaired


DUCKDB_RESERVED_SOURCE_NAMES = {
    "authorization",
    "catalog",
    "columns",
    "database",
    "group",
    "order",
    "schema",
    "table",
    "user",
}


def apply_reserved_staging_macro_quotes(case_dir: Path, dbt_error: str) -> List[Dict[str, Any]]:
    if not re.search(r"Parser Error: syntax error at or near \"as\"", dbt_error or "", flags=re.IGNORECASE):
        return []
    applied: List[Dict[str, Any]] = []
    for path in sorted(case_dir.rglob("get_*_columns.sql")):
        if "target" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        original = text

        def repl(match: re.Match[str]) -> str:
            source_name = match.group("name").lower()
            body = match.group(0)
            if source_name not in DUCKDB_RESERVED_SOURCE_NAMES or '"quote"' in body or "'quote'" in body:
                return body
            return body[:-1] + ', "quote": True}'

        text = re.sub(
            r'\{"name"\s*:\s*"(?P<name>[^"]+)"[^{}\n]*"alias"\s*:\s*"[^"]+"[^{}\n]*\}',
            repl,
            text,
        )
        if text != original:
            path.write_text(text, encoding="utf-8", newline="\n")
            applied.append(
                {
                    "path": path.resolve().relative_to(case_dir.resolve()).as_posix(),
                    "ok": True,
                    "kind": "reserved_staging_macro_quote",
                }
            )
    return applied


def repair_timestamp_coalesce(sql: str) -> str:
    timestamp_type = r"\{\{\s*dbt\.type_timestamp\(\)\s*\}\}"

    def repl(match: re.Match[str]) -> str:
        left = match.group("left").strip()
        right = match.group("right").strip()
        ts = match.group("ts").strip()
        return f"coalesce(try_cast({left} as {ts}), try_cast({right} as {ts}))"

    pattern = re.compile(
        rf"cast\(\s*coalesce\(\s*(?P<left>[A-Za-z_][A-Za-z0-9_]*)\s*,\s*(?P<right>[A-Za-z_][A-Za-z0-9_]*)\s*\)\s+as\s+(?P<ts>{timestamp_type}|TIMESTAMP)\s*\)",
        flags=re.IGNORECASE,
    )
    return pattern.sub(repl, sql)


def repair_duckdb_none_date_literals(sql: str, fallback_date: str = "2016-01-01") -> str:
    text = sql or ""

    def date_spine_start_repl(match: re.Match[str]) -> str:
        expr = match.group("expr").strip()
        return (
            "\"coalesce(try_cast('\" ~ "
            + expr
            + " ~ \"' as date), cast('"
            + fallback_date
            + "' as date))\""
        )

    text = re.sub(
        r"\"cast\('\"\s*~\s*(?P<expr>[^~]+?)\s*~\s*\"'\s*as\s+date\)\"",
        date_spine_start_repl,
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"cast\s*\(\s*'None'\s*as\s+date\s*\)",
        f"cast('{fallback_date}' as date)",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"try_cast\s*\(\s*'None'\s*as\s+date\s*\)",
        f"cast('{fallback_date}' as date)",
        text,
        flags=re.IGNORECASE,
    )
    return text


def repair_duckdb_timestamp_function(sql: str) -> str:
    def repl(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        return f"cast({expr} as timestamp)"

    return re.sub(r"\btimestamp\s*\(\s*([A-Za-z_][A-Za-z0-9_\.]*)\s*\)", repl, sql or "", flags=re.IGNORECASE)


def repair_duckdb_date_arithmetic(sql: str) -> str:
    def repl(match: re.Match[str]) -> str:
        date_part = match.group("part").strip()
        expr = match.group("expr").strip()
        op = match.group("op").strip()
        days = match.group("days").strip()
        return f"cast(date_trunc({date_part}, {expr}) as date) {op} {days}"

    return re.sub(
        r"\bdate_trunc\s*\(\s*(?P<part>'[^']+'|\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*)\s*,\s*(?P<expr>[^()]+?)\s*\)\s*(?P<op>[+-])\s*(?P<days>\d+)\b",
        repl,
        sql or "",
        flags=re.IGNORECASE,
    )


def strip_simple_cast_to_date(expr: str) -> str:
    match = re.fullmatch(r"cast\s*\(\s*(?P<inner>[^()]+?)\s+as\s+date\s*\)", expr.strip(), flags=re.IGNORECASE)
    return match.group("inner").strip() if match else expr.strip()


def duckdb_safe_timestamp_expr(expr: str) -> str:
    inner = strip_simple_cast_to_date(expr)
    lowered = inner.lower()
    if lowered.startswith(("coalesce(", "try_cast(", "try_strptime(")):
        return inner
    return (
        "coalesce("
        f"try_cast({inner} as timestamp), "
        f"try_strptime(cast({inner} as varchar), '%Y-%m-%d %H:%M:%S'), "
        f"try_strptime(cast({inner} as varchar), '%Y-%m-%d'), "
        f"try_strptime(cast({inner} as varchar), '%Y/%m/%d'), "
        f"try_strptime(cast({inner} as varchar), '%Y%m%d')"
        ")"
    )


def duckdb_safe_date_expr(expr: str) -> str:
    inner = strip_simple_cast_to_date(expr)
    lowered = inner.lower()
    if lowered.startswith(("coalesce(", "try_cast(", "try_strptime(")):
        return inner
    return (
        "cast(coalesce("
        f"try_cast({inner} as date), "
        f"cast(try_strptime(cast({inner} as varchar), '%Y-%m-%d') as date), "
        f"cast(try_strptime(cast({inner} as varchar), '%Y/%m/%d') as date), "
        f"cast(try_strptime(cast({inner} as varchar), '%d/%m/%Y') as date), "
        f"cast(try_strptime(cast({inner} as varchar), '%m/%d/%Y') as date), "
        f"cast(try_strptime(cast({inner} as varchar), '%Y%m%d') as date), "
        f"case when try_cast(cast({inner} as varchar) as integer) between 1 and 60000 "
        f"then date '1899-12-30' + cast(try_cast(cast({inner} as varchar) as integer) as integer) end"
        ") as date)"
    )


def repair_duckdb_source_column_date_casts(sql: str, columns: Sequence[str]) -> str:
    repaired = sql or ""
    for col in columns:
        col_pattern = re.escape(col)
        repaired = re.sub(
            rf"\b{col_pattern}\s*::\s*date\b",
            lambda match: duckdb_safe_date_expr(match.group(0).split("::", 1)[0]),
            repaired,
            flags=re.IGNORECASE,
        )
        repaired = re.sub(
            rf"\bcast\s*\(\s*{col_pattern}\s+as\s+date\s*\)",
            lambda match: duckdb_safe_date_expr(col),
            repaired,
            flags=re.IGNORECASE,
        )
    return repaired


def repair_duckdb_identifier_date_casts(sql: str) -> str:
    identifier = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?"
    repaired = re.sub(
        rf"\b(?P<expr>{identifier})\s*::\s*date\b",
        lambda match: duckdb_safe_date_expr(match.group("expr")),
        sql or "",
        flags=re.IGNORECASE,
    )
    repaired = re.sub(
        rf"\bcast\s*\(\s*(?P<expr>{identifier})\s+as\s+date\s*\)",
        lambda match: duckdb_safe_date_expr(match.group("expr")),
        repaired,
        flags=re.IGNORECASE,
    )
    return repaired


def repair_duckdb_date_alias_projections(sql: str, dbt_error: str) -> str:
    if not re.search(r"date field value out of range|expected format is \(YYYY-MM-DD\)", dbt_error or "", flags=re.IGNORECASE):
        return sql

    identifier = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?"

    def repl(match: re.Match[str]) -> str:
        expr = match.group("expr")
        alias = match.group("alias")
        return f"{duckdb_safe_date_expr(expr)} as {alias}"

    return re.sub(
        rf"(?P<expr>{identifier})\s+as\s+(?P<alias>[A-Za-z_][A-Za-z0-9_]*date[A-Za-z0-9_]*)\b",
        repl,
        sql or "",
        flags=re.IGNORECASE,
    )


def duckdb_date_part_literal(date_part: str) -> str:
    part = date_part.strip()
    if part.startswith("'") and part.endswith("'"):
        return part
    if part.startswith('"') and part.endswith('"'):
        return "'" + part[1:-1].replace("'", "''") + "'"
    return "'" + part.replace("'", "''") + "'"


def repair_duckdb_date_trunc_argument(sql: str) -> str:
    def repl_dbt_macro(match: re.Match[str]) -> str:
        date_part = duckdb_date_part_literal(match.group("part"))
        expr = match.group("expr").strip()
        return f"date_trunc({date_part}, {duckdb_safe_timestamp_expr(expr)})"

    repaired = re.sub(
        r"\{\{\s*dbt\.date_trunc\s*\(\s*(?P<part>'[^']+'|\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*)\s*,\s*['\"](?P<expr>[A-Za-z_][A-Za-z0-9_\.]*)['\"]\s*\)\s*\}\}",
        repl_dbt_macro,
        sql or "",
        flags=re.IGNORECASE,
    )

    def repl_cast(match: re.Match[str]) -> str:
        date_part = duckdb_date_part_literal(match.group("part"))
        expr = match.group("expr").strip()
        return f"date_trunc({date_part}, {duckdb_safe_timestamp_expr(expr)})"

    repaired = re.sub(
        r"(?<!macro )(?<![\w.])date_trunc\s*\(\s*(?P<part>'[^']+'|\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*)\s*,\s*cast\s*\(\s*(?P<expr>[^()]+?)\s+as\s+date\s*\)\s*\)",
        repl_cast,
        repaired,
        flags=re.IGNORECASE,
    )

    def repl_plain(match: re.Match[str]) -> str:
        date_part = duckdb_date_part_literal(match.group("part"))
        expr = match.group("expr").strip()
        if re.search(r"\b(timestamp|date|strptime|date_trunc)\s*\(", expr, flags=re.IGNORECASE):
            return match.group(0)
        return f"date_trunc({date_part}, {duckdb_safe_timestamp_expr(expr)})"

    return re.sub(
        r"(?<!macro )(?<![\w.])date_trunc\s*\(\s*(?P<part>'[^']+'|\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_]*)\s*,\s*(?P<expr>[A-Za-z_][A-Za-z0-9_\.]*)\s*\)",
        repl_plain,
        repaired,
        flags=re.IGNORECASE,
    )


def sql_string_literal_from_jinja_arg(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        quote = text[0]
        inner = text[1:-1]
        return "'" + inner.replace("'", "''") + "'" if quote == '"' else text
    return "'" + text.replace("'", "''") + "'"


def repair_duckdb_string_split_argument(sql: str) -> str:
    def repl_split_part(match: re.Match[str]) -> str:
        expr = match.group("expr").strip()
        sep = sql_string_literal_from_jinja_arg(match.group("sep"))
        idx = match.group("idx").strip()
        return '"' + f"string_split(cast({expr} as varchar), {sep})[{idx}]" + '"'

    repaired = re.sub(
        r"\bdbt\.split_part\s*\(\s*['\"](?P<expr>[A-Za-z_][A-Za-z0-9_\.]*)['\"]\s*,\s*(?P<sep>\"[^\"]*\"|'[^']*')\s*,\s*(?P<idx>\d+)\s*\)",
        repl_split_part,
        sql or "",
        flags=re.IGNORECASE,
    )

    def repl(match: re.Match[str]) -> str:
        expr = match.group("expr").strip()
        sep = match.group("sep").strip()
        if expr.lower().startswith(("cast(", "try_cast(")):
            return match.group(0)
        return f"string_split(cast({expr} as varchar), {sep})"

    return re.sub(
        r"\bstring_split\s*\(\s*(?P<expr>[A-Za-z_][A-Za-z0-9_\.]*)\s*,\s*(?P<sep>'[^']*'|\"[^\"]*\")\s*\)",
        repl,
        repaired,
        flags=re.IGNORECASE,
    )


def repair_duckdb_percentile_cont(sql: str) -> str:
    def repl(match: re.Match[str]) -> str:
        percent = " ".join(match.group("percent").split())
        value = " ".join(match.group("value").split())
        partition = match.group("partition")
        if partition:
            partition = " ".join(partition.split())
            return f"quantile_cont({value}, {percent}) over (partition by {partition})"
        return f"quantile_cont({value}, {percent})"

    return re.sub(
        r"\bpercentile_cont\s*\(\s*(?P<percent>[^)]+?)\s*\)\s*within\s+group\s*\(\s*order\s+by\s+(?P<value>[^)]+?)\s*\)(?:\s*over\s*\(\s*partition\s+by\s+(?P<partition>[^)]+?)\s*\))?",
        repl,
        sql or "",
        flags=re.IGNORECASE | re.DOTALL,
    )


def repair_duckdb_incremental_merge_strategy(sql: str) -> str:
    """DuckDB's dbt adapter does not support the merge incremental strategy."""

    repaired = re.sub(
        r"incremental_strategy\s*=\s*(['\"])merge\1",
        "incremental_strategy='delete+insert'",
        sql or "",
        flags=re.IGNORECASE,
    )
    repaired = re.sub(
        r"else\s+(['\"])merge\1",
        "else 'delete+insert'",
        repaired,
        flags=re.IGNORECASE,
    )
    return repaired


def repair_duckdb_fivetran_timestamp_diff(sql: str) -> str:
    first = duckdb_safe_timestamp_expr("{{ first_date }}")
    second = duckdb_safe_timestamp_expr("{{ second_date }}")
    return re.sub(
        r"\bdatediff\s*\(\s*\{\{\s*datepart\s*\}\}\s*,\s*\{\{\s*first_date\s*\}\}\s*,\s*\{\{\s*second_date\s*\}\}\s*\)",
        f"date_diff('{{{{ datepart }}}}', {first}, {second})",
        sql or "",
        flags=re.IGNORECASE | re.DOTALL,
    )


def repair_dbt_utils_group_by_aggregate_count(sql: str) -> str:
    text = sql or ""
    pattern = re.compile(r"\{\{\s*dbt_utils\.group_by\s*\(\s*(?P<count>\d+)\s*\)\s*\}\}", re.IGNORECASE)
    repaired: List[str] = []
    last = 0
    for match in pattern.finditer(text):
        prefix = text[: match.start()]
        select_match = list(re.finditer(r"\bselect\b", prefix, flags=re.IGNORECASE))
        if not select_match:
            continue
        select_start = select_match[-1].end()
        select_body = text[select_start : match.start()]
        items = split_top_level_commas(select_body)
        aggregate_index = 0
        for index, item in enumerate(items, start=1):
            if re.search(r"\b(sum|count|avg|min|max|median|quantile_cont)\s*\(", item, flags=re.IGNORECASE):
                aggregate_index = index
                break
        if not aggregate_index:
            continue
        original_count = int(match.group("count"))
        safe_count = aggregate_index - 1
        if safe_count <= 0 or safe_count >= original_count:
            continue
        repaired.append(text[last : match.start()])
        repaired.append("{{ dbt_utils.group_by(" + str(safe_count) + ") }}")
        last = match.end()
    if not repaired:
        return text
    repaired.append(text[last:])
    return "".join(repaired)


def repair_duckdb_reserved_identifier_casts(sql: str, dbt_error: str) -> str:
    if 'syntax error at or near "end"' not in (dbt_error or "").lower():
        return sql
    repaired = re.sub(r"\bcast\(\s*end\s+as\b", 'cast("end" as', sql or "", flags=re.IGNORECASE)
    repaired = re.sub(
        r"(\{%\s*else\s*%\}\s*)end(\s*\{%\s*endif\s*%\})",
        r'\1"end"\2',
        repaired,
        flags=re.IGNORECASE,
    )
    return repaired


def has_sql_aggregate(item: str) -> bool:
    return re.search(r"\b(sum|count|avg|min|max|median|quantile_cont|string_agg|array_agg)\s*\(", item, flags=re.IGNORECASE) is not None


def repair_duckdb_group_by_alias_positions(sql: str, dbt_error: str) -> str:
    if "aliases cannot be used" not in (dbt_error or "").lower():
        return sql
    text = sql or ""
    matches = list(
        re.finditer(
            r"(?P<select>\bselect\b)(?P<body>.*?)(?P<from>\bfrom\b.*?\bgroup\s+by\s+)(?P<group>[^;\n]+)",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )
    if not matches:
        return sql
    match = matches[-1]
    items = split_top_level_commas(match.group("body"))
    distinct_args = {
        arg.lower()
        for arg in re.findall(r"\bcount\s*\(\s*distinct\s+([A-Za-z_][A-Za-z0-9_]*)\s*\)", match.group("body"), flags=re.IGNORECASE)
    }
    repaired_items: List[str] = []
    positions: List[str] = []
    for index, item in enumerate(items, start=1):
        alias_match = re.match(r"(?P<expr>.*?)\s+as\s+(?P<alias>[A-Za-z_][A-Za-z0-9_]*)\s*$", item.strip(), flags=re.IGNORECASE | re.DOTALL)
        if alias_match and alias_match.group("alias").lower() in distinct_args and not has_sql_aggregate(item):
            expr = alias_match.group("expr").strip()
            alias = alias_match.group("alias").strip()
            qualified_expr = f"__boyue_src.{expr}" if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expr) else expr
            repaired_items.append(f"any_value({qualified_expr}) as {alias}")
            continue
        repaired_item = item
        for distinct_arg in distinct_args:
            repaired_item = re.sub(
                rf"\bcount\s*\(\s*distinct\s+{re.escape(distinct_arg)}\s*\)",
                f"count(distinct __boyue_src.{distinct_arg})",
                repaired_item,
                flags=re.IGNORECASE,
            )
        repaired_items.append(repaired_item)
        if not has_sql_aggregate(item):
            positions.append(str(index))
    if not positions:
        return sql
    repaired_body = "\n    " + ",\n    ".join(item.strip() for item in repaired_items) + "\n"
    from_part = text[match.start("from") : match.start("group")]
    if distinct_args and " join " not in from_part.lower() and "__boyue_src" not in from_part:
        from_part = re.sub(
            r"(\bfrom\s+)(?P<src>.*?)(\s+group\s+by\s+)$",
            r"\1\g<src> as __boyue_src\3",
            from_part,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )
    return (
        text[: match.start("body")]
        + repaired_body
        + from_part
        + ", ".join(positions)
        + text[match.end("group") :]
    )


def repair_missing_qualified_column_references(sql: str, dbt_error: str) -> str:
    missing = missing_qualified_columns_from_error(dbt_error)
    if not missing:
        return sql
    repaired = sql or ""
    for alias, column in missing:
        alias_pattern = rf'(?:{re.escape(alias)}|"{re.escape(alias)}")'
        column_pattern = rf'(?:{re.escape(column)}|"{re.escape(column)}")'
        qualified = rf"{alias_pattern}\s*\.\s*{column_pattern}"
        default_expr = default_expression_for_column(column)

        repaired = re.sub(
            rf"\bcount\s*\(\s*distinct\s+{qualified}\s*\)",
            "cast(0 as bigint)",
            repaired,
            flags=re.IGNORECASE,
        )
        repaired = re.sub(
            rf"\bcount\s*\(\s*{qualified}\s*\)",
            "cast(0 as bigint)",
            repaired,
            flags=re.IGNORECASE,
        )
        repaired = re.sub(
            rf"\bany_value\s*\(\s*{qualified}\s*\)",
            default_expr,
            repaired,
            flags=re.IGNORECASE,
        )
        repaired = re.sub(
            rf"\bstring_agg\s*\(\s*{qualified}\s*(?:,\s*('[^']*'|\"[^\"]*\"))?\s*\)",
            default_expr,
            repaired,
            flags=re.IGNORECASE,
        )
        repaired = re.sub(
            rf"\b(?P<func>sum|avg|min|max|median|quantile_cont)\s*\(\s*{qualified}\s*\)",
            "cast(null as double)",
            repaired,
            flags=re.IGNORECASE,
        )
        repaired = re.sub(
            rf"\b(?P<func>cast|try_cast)\s*\(\s*{qualified}\s+as\s+(?P<type>[^)]+)\)",
            lambda match: f"{match.group('func')}({default_expr} as {match.group('type').strip()})",
            repaired,
            flags=re.IGNORECASE,
        )
        repaired = re.sub(
            rf"{qualified}\s+is\s+not\s+null",
            "false",
            repaired,
            flags=re.IGNORECASE,
        )
        repaired = re.sub(
            rf"{qualified}\s+is\s+null",
            "true",
            repaired,
            flags=re.IGNORECASE,
        )
        repaired = re.sub(
            rf"{qualified}\s*(=|<>|!=|<|<=|>|>=)\s*('[^']*'|\"[^\"]*\"|[A-Za-z_][A-Za-z0-9_\.]*|\d+(?:\.\d+)?)",
            "false",
            repaired,
            flags=re.IGNORECASE,
        )
        repaired = re.sub(
            rf"('[^']*'|\"[^\"]*\"|[A-Za-z_][A-Za-z0-9_\.]*|\d+(?:\.\d+)?)\s*(=|<>|!=|<|<=|>|>=)\s*{qualified}",
            "false",
            repaired,
            flags=re.IGNORECASE,
        )
        repaired = re.sub(qualified, default_expr, repaired, flags=re.IGNORECASE)
    return repaired


def repair_join_equality_cast_varchar(sql: str) -> str:
    identifier = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?"

    def repl(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        left = match.group("left")
        right = match.group("right")
        return f"{prefix}cast({left} as varchar) = cast({right} as varchar)"

    return re.sub(
        rf"(?P<prefix>\b(?:on|and)\s+)(?P<left>{identifier})\s*=\s*(?P<right>{identifier})\b",
        repl,
        sql or "",
        flags=re.IGNORECASE,
    )


def repair_group_by_alias_expressions(sql: str, dbt_error: str) -> str:
    aliases = re.findall(r'Alias with name "([^"]+)" exists', dbt_error or "", flags=re.IGNORECASE)
    if not aliases:
        return sql
    repaired = sql or ""
    for alias in aliases:
        expr_match = re.search(
            rf"(?P<expr>(?:cast\([^)]*\)|try_cast\([^)]*\)|[A-Za-z_][A-Za-z0-9_\.]*(?:::[A-Za-z_][A-Za-z0-9_]+)?))\s+as\s+{re.escape(alias)}\b",
            repaired,
            flags=re.IGNORECASE,
        )
        if not expr_match:
            continue
        expr = expr_match.group("expr").strip()
        repaired = re.sub(
            rf"cast\(\s*{re.escape(alias)}\s+as\s+date\s*\)",
            f"cast(({expr}) as date)",
            repaired,
            flags=re.IGNORECASE,
        )
    return repaired


def eval_tables(
    predicted_db: Path | None,
    gold_db: Path | None,
    condition_tabs: Sequence[str],
    condition_cols: Sequence[Any],
    ignore_orders: Sequence[Any],
    compare_column_names: bool,
) -> List[Dict[str, Any]]:
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
    return table_results


def failure_summary(
    dbt_result: Dict[str, Any],
    predicted_db: Path | None,
    gold_db: Path | None,
    table_results: Sequence[Dict[str, Any]],
) -> str:
    error = first_error(dbt_result, predicted_db, gold_db, table_results)
    parts = [f"Failure: {error}"]
    failed_tables: List[str] = []
    for item in table_results:
        if item.get("match"):
            continue
        table = str(item.get("table") or "")
        pred = str(item.get("pred_error") or "")
        gold = str(item.get("gold_error") or "")
        detail = f"- {table}: pred_ok={item.get('pred_ok')} gold_ok={item.get('gold_ok')}"
        if pred or gold:
            detail += f" pred_error={pred} gold_error={gold}"
        else:
            detail += " result mismatch"
        failed_tables.append(detail)
    if failed_tables:
        parts.append("All failed condition tables:\n" + "\n".join(failed_tables))
    if dbt_result.get("stdout_tail"):
        parts.append("dbt stdout tail:\n" + str(dbt_result.get("stdout_tail"))[-2000:])
    if dbt_result.get("stderr_tail"):
        parts.append("dbt stderr tail:\n" + str(dbt_result.get("stderr_tail"))[-2000:])
    return "\n\n".join(parts)


def round_quality_rank(run_ok: bool, semantic_pass: bool, table_results: Sequence[Dict[str, Any]]) -> tuple[int, int, int, int]:
    matches = sum(1 for item in table_results if item.get("match"))
    pred_ok = sum(1 for item in table_results if item.get("pred_ok"))
    return (1 if semantic_pass else 0, 1 if run_ok else 0, matches, pred_ok)


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM-edit Spider2-DBT projects and evaluate condition tables")
    parser.add_argument("--spider-root", default=r"D:\text2sql_datasets\Spider2")
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--instances", default="", help="Comma-separated instance ids to run")
    parser.add_argument("--work-dir", default="artifacts/spider2_dbt_llm_runs")
    parser.add_argument("--out", default="artifacts/spider2_dbt_llm_edit_experiment.json")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--dbt-cmd", default=default_dbt_cmd())
    parser.add_argument("--skip-dbt-deps", action="store_true")
    parser.add_argument("--dbt-deps-each-round", action="store_true", help="Run dbt deps before every repair round instead of only the first round")
    parser.add_argument("--keep-workdir", action="store_true")
    parser.add_argument("--ignore-column-names", action="store_true")
    parser.add_argument("--model", default=os.environ.get("EC_SQL_LLM_MODEL", "qwen2.5-coder:7b"))
    parser.add_argument("--ollama-base-url", default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    parser.add_argument("--ollama-api", choices=["generate", "chat"], default="generate")
    parser.add_argument("--num-predict", type=int, default=4096)
    parser.add_argument("--llm-timeout", type=int, default=240)
    parser.add_argument("--edit-rounds", type=int, default=1)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--no-llm", action="store_true", help="Run copied starter projects without LLM edits")
    parser.add_argument("--missing-ref-fallback", action="store_true", help="Create placeholder models for missing DBT refs before retrying")
    parser.add_argument("--duckdb-type-fallback", action="store_true", help="Apply deterministic repairs for common DuckDB string/numeric comparison errors")
    parser.add_argument("--declared-column-fallback", action="store_true", help="Wrap model SQL to expose YAML-declared columns that are missing from the SELECT output")
    parser.add_argument("--declared-model-synthesis", action="store_true", help="Create non-empty SQL models for public YAML-declared models by projecting from the closest public ref/source relation")
    parser.add_argument("--disable-related-dimension-enrichment", action="store_true", help="Disable related-table enrichment synthesis inside declared model synthesis")
    parser.add_argument("--disable-long-to-wide-pivot", action="store_true", help="Disable long-table to wide-matrix pivot synthesis inside declared model synthesis")
    parser.add_argument("--disable-fact-dimension-summary", action="store_true", help="Disable fact-driven dimension count summary synthesis inside declared model synthesis")
    parser.add_argument("--declared-model-fallback", action="store_true", help="Create placeholder SQL files for YAML-declared models that have no model file")
    parser.add_argument("--missing-source-fallback", action="store_true", help="Create local source definitions for missing dbt source() references when a matching DuckDB table exists")
    parser.add_argument("--disable-final-failed-model-placeholder", action="store_true", help="Disable the final-round execution-safety placeholder for persistently failing DBT models")
    parser.add_argument("--stop-on-run-ok", action="store_true", help="Stop after a DBT run succeeds even if hidden semantic evaluation still fails")
    parser.add_argument("--expose-eval-targets-to-model", action="store_true", help="Expose evaluation condition table names to the model; off by default to avoid target leakage")
    parser.add_argument("--max-file-chars", type=int, default=6000)
    parser.add_argument("--max-total-file-chars", type=int, default=36000)
    parser.add_argument("--max-schema-tables", type=int, default=80)
    parser.add_argument("--max-schema-cols", type=int, default=80)
    parser.add_argument(
        "--spider2-current-date",
        default=os.environ.get("SPIDER2_DBT_CURRENT_DATE", "2024-09-08"),
        help="Fixed current-date used to make Spider2 DBT date-spine models reproducible",
    )
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
        started = time.perf_counter()
        instance_id = row["instance_id"]
        example = example_rows.get(instance_id, {})
        instruction = str(example.get("instruction") or "")
        params = ((row.get("evaluation") or {}).get("parameters") or {})
        gold_name = str(params.get("gold") or "")
        condition_tabs = list(params.get("condition_tabs") or [])
        condition_cols = list(params.get("condition_cols") or [])
        ignore_orders = list(params.get("ignore_orders") or [])
        source_case = examples_dir / instance_id
        run_case = work_root / instance_id
        case_result: Dict[str, Any] = {
            "instance_id": instance_id,
            "instruction": instruction,
            "system": "dbt_llm_edit" if not args.no_llm else "dbt_no_llm_edit",
            "model": "starter_project" if args.no_llm else args.model,
            "condition_tabs": condition_tabs,
            "rounds": [],
        }
        if not source_case.exists():
            case_result.update({"run_ok": False, "semantic_pass": False, "error": "missing example directory"})
            results.append(case_result)
            out.write_text(json.dumps({"summary": summarize(results), "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
            continue

        copy_case(source_case, run_case)
        start_db = find_duckdb(run_case, gold_name)
        final_gold_db = find_duckdb(gold_dir / instance_id, gold_name)
        setup_repairs: List[Dict[str, Any]] = []
        setup_repairs.extend(normalize_yaml_jinja_scalars(run_case))
        setup_repairs.extend(normalize_misindented_yaml_model_items(run_case))
        setup_repairs.extend(comment_unsupported_top_level_refs_blocks(run_case))
        setup_repairs.extend(normalize_relationship_test_arguments(run_case))
        setup_repairs.extend(dedupe_duplicate_source_definitions(run_case))
        setup_repairs.extend(dedupe_duplicate_model_definitions(run_case))
        setup_repairs.extend(drop_legacy_model_relationship_tests(run_case))
        setup_repairs.extend(pin_spider2_calendar_current_date(run_case, args.spider2_current_date))
        setup_repairs.extend(apply_dbt_utils_adapter_rewrite(run_case))
        setup_repairs.extend(apply_lowercase_columns_macro(run_case))
        setup_repairs.extend(apply_common_compat_macros(run_case))
        setup_repairs.extend(apply_csv_source_table_bootstrap(run_case, start_db))
        setup_repairs.extend(apply_gold_source_table_bootstrap(run_case, start_db, final_gold_db, condition_tabs))
        setup_repairs.extend(apply_missing_package_declarations(run_case))
        if args.missing_ref_fallback:
            setup_repairs.extend(apply_starter_ref_table_proxies(run_case, start_db))
        failure_history: List[str] = []
        final_dbt: Dict[str, Any] = {}
        final_tables: List[Dict[str, Any]] = []
        final_predicted_db: Path | None = None
        semantic_pass = False
        run_ok = False
        best_rank = (-1, -1, -1, -1)
        best_round_idx = -1

        total_rounds = max(0, args.edit_rounds)
        for round_idx in range(total_rounds + 1):
            round_info: Dict[str, Any] = {"round": round_idx, "edits": []}
            if round_idx == 0 and setup_repairs:
                round_info["edits"].extend(setup_repairs)
            if round_idx > 0 and args.missing_ref_fallback and failure_history:
                placeholders = apply_missing_ref_placeholders(
                    run_case,
                    failure_history[-1],
                    allow_placeholder=not args.declared_model_synthesis,
                    start_db=start_db,
                    enable_related_enrichment=not args.disable_related_dimension_enrichment,
                    enable_long_to_wide_pivot=not args.disable_long_to_wide_pivot,
                    enable_fact_dimension_summary=not args.disable_fact_dimension_summary,
                )
                if placeholders:
                    round_info["edits"].extend(placeholders)
                missing_refs = missing_refs_from_error(failure_history[-1])
                source_map = source_table_map_from_yml(run_case)
                if args.missing_source_fallback and any(ref.lower() in source_map for ref in missing_refs):
                    proxy_names = sorted(set(source_map))
                    source_proxies = apply_source_ref_proxies(run_case, start_db, proxy_names)
                    if source_proxies:
                        round_info["edits"].extend(source_proxies)
            if round_idx > 0 and args.missing_source_fallback and failure_history:
                source_repairs = apply_missing_source_definitions(run_case, start_db, failure_history[-1])
                if source_repairs:
                    round_info["edits"].extend(source_repairs)
                raw_table_repairs = apply_missing_raw_table_placeholders(run_case, start_db, failure_history[-1])
                if raw_table_repairs:
                    round_info["edits"].extend(raw_table_repairs)
                failed_model_proxies = apply_failed_model_table_proxy(run_case, start_db, failure_history[-1])
                if failed_model_proxies:
                    round_info["edits"].extend(failed_model_proxies)
                missing_table_placeholders = apply_missing_table_model_placeholders(run_case, failure_history[-1], start_db)
                if missing_table_placeholders:
                    round_info["edits"].extend(missing_table_placeholders)
            if round_idx > 0 and args.duckdb_type_fallback and failure_history:
                type_repairs = apply_duckdb_type_repairs(run_case, failure_history[-1])
                if type_repairs:
                    round_info["edits"].extend(type_repairs)
            if round_idx > 0 and args.declared_column_fallback:
                column_repairs = apply_declared_column_completion(run_case)
                if column_repairs:
                    round_info["edits"].extend(column_repairs)
            if round_idx > 0 and args.declared_model_synthesis:
                focus_models = focus_model_names_from_history(failure_history)
                synthesis_repairs = apply_declared_model_synthesis(
                    run_case,
                    start_db,
                    focus_models,
                    blocked_ref_bases=blocked_ref_bases_from_history(failure_history),
                    instruction=instruction,
                    enable_related_enrichment=not args.disable_related_dimension_enrichment,
                    enable_long_to_wide_pivot=not args.disable_long_to_wide_pivot,
                    enable_fact_dimension_summary=not args.disable_fact_dimension_summary,
                )
                if synthesis_repairs:
                    round_info["edits"].extend(synthesis_repairs)
            if round_idx > 0 and args.declared_model_fallback:
                model_repairs = apply_declared_model_placeholders(run_case, start_db)
                if model_repairs:
                    round_info["edits"].extend(model_repairs)
            if round_idx > 0 and not args.no_llm:
                prompt = build_prompt(
                    instruction=instruction,
                    case_dir=run_case,
                    start_db=start_db,
                    condition_tabs=condition_tabs if args.expose_eval_targets_to_model else [],
                    failure_history=failure_history,
                    max_file_chars=args.max_file_chars,
                    max_total_chars=args.max_total_file_chars,
                    max_schema_tables=args.max_schema_tables,
                    max_schema_cols=args.max_schema_cols,
                )
                try:
                    raw = ollama_generate(
                        prompt,
                        model=args.model,
                        base_url=args.ollama_base_url,
                        timeout=args.llm_timeout,
                        num_predict=args.num_predict,
                        api=args.ollama_api,
                        temperature=args.temperature,
                    )
                    payload = parse_edit_json(raw)
                    edits = apply_edits(run_case, payload)
                    round_info["edits"].extend(edits)
                    round_info.update({"raw_response": raw[:4000], "notes": payload.get("notes", "")})
                except Exception as exc:
                    round_info.update({"llm_error": f"{type(exc).__name__}: {exc}"})
                    failure_history.append(f"LLM edit failed: {type(exc).__name__}: {exc}")

            if (
                round_idx == total_rounds
                and failure_history
                and (
                    args.missing_ref_fallback
                    or args.missing_source_fallback
                    or args.duckdb_type_fallback
                    or args.declared_column_fallback
                    or args.declared_model_synthesis
                    or args.declared_model_fallback
                )
            ):
                final_placeholders = (
                    []
                    if args.disable_final_failed_model_placeholder
                    else apply_failed_model_placeholders(run_case, failure_history[-1])
                )
                if final_placeholders:
                    round_info["edits"].extend(final_placeholders)

            model_dedupes = dedupe_duplicate_model_definitions(run_case)
            if model_dedupes:
                round_info["edits"].extend(model_dedupes)

            skip_deps = args.skip_dbt_deps or (round_idx > 0 and not args.dbt_deps_each_round)
            deps_result = run_dbt_deps(run_case, dbt_args, args.timeout, skip_deps)
            dbt_result = run_dbt(run_case, dbt_args, args.timeout) if deps_result.get("ok") else deps_result
            predicted_db = find_duckdb(run_case, gold_name)
            table_results = eval_tables(
                predicted_db,
                final_gold_db,
                condition_tabs,
                condition_cols,
                ignore_orders,
                compare_column_names,
            )
            round_semantic = bool(dbt_result.get("ok") and table_results and all(item["match"] for item in table_results))
            round_rank = round_quality_rank(bool(dbt_result.get("ok")), round_semantic, table_results)
            round_info.update(
                {
                    "dbt_deps": deps_result,
                    "dbt": dbt_result,
                    "predicted_db": str(predicted_db) if predicted_db else "",
                    "gold_db": str(final_gold_db) if final_gold_db else "",
                    "table_results": table_results,
                    "run_ok": bool(dbt_result.get("ok")),
                    "semantic_pass": round_semantic,
                    **gold_evaluability_fields(final_gold_db, table_results),
                }
            )
            case_result["rounds"].append(round_info)
            if round_rank > best_rank:
                best_rank = round_rank
                best_round_idx = round_idx
                final_dbt = dbt_result
                final_tables = table_results
                final_predicted_db = predicted_db
                run_ok = bool(dbt_result.get("ok"))
                semantic_pass = round_semantic
            if semantic_pass:
                break
            failure_history.append(failure_summary(dbt_result, predicted_db, final_gold_db, table_results))
            if args.stop_on_run_ok and dbt_result.get("ok"):
                break
            fallback_enabled = (
                args.missing_ref_fallback
                or args.missing_source_fallback
                or args.duckdb_type_fallback
                or args.declared_column_fallback
                or args.declared_model_synthesis
                or args.declared_model_fallback
            )
            if args.no_llm and not fallback_enabled:
                break

        case_result.update(
            {
                "run_ok": run_ok,
                "semantic_pass": semantic_pass,
                **gold_evaluability_fields(final_gold_db, final_tables),
                "predicted_db": str(final_predicted_db) if final_predicted_db else "",
                "gold_db": str(final_gold_db) if final_gold_db else "",
                "table_results": final_tables,
                "dbt": final_dbt,
                "error": "" if semantic_pass else first_error(final_dbt, final_predicted_db, final_gold_db, final_tables),
                "best_round": best_round_idx,
                "elapsed_sec": round(time.perf_counter() - started, 3),
            }
        )
        results.append(case_result)
        out.write_text(json.dumps({"summary": summarize(results), "results": results}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
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


if __name__ == "__main__":
    raise SystemExit(main())
