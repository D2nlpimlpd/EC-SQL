from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify, request

from boyuesql_generic import connect_database, default_config_from_env, get_dialect
from boyuesql_generic.dictionary import SchemaDictionary, load_schema_dictionary
from boyuesql_generic.retrieval import retrieve_tables, schema_prompt


SQL_BLOCK_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
SQL_START_RE = re.compile(r"\b(WITH|SELECT)\b", re.IGNORECASE)


app = Flask(__name__)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def runtime_config():
    return default_config_from_env(default_dialect=os.environ.get("BOYUESQL_DIALECT", "sqlite"))


def schema_source_path() -> str:
    cfg = runtime_config()
    return (
        os.environ.get("SCHEMA_DICTIONARY_PATH", "").strip()
        or os.environ.get("DATA_DICT_PATH", "").strip()
        or cfg.path
        or cfg.database
    )


def load_runtime_schema() -> SchemaDictionary:
    source = schema_source_path()
    if not source:
        raise RuntimeError(
            "No schema source configured. Set SCHEMA_DICTIONARY_PATH, DATA_DICT_PATH, DB_PATH, or DB_DATABASE."
        )
    return load_schema_dictionary(source, dialect=runtime_config().dialect)


def extract_sql(text: str) -> str:
    if not text:
        return ""
    block = SQL_BLOCK_RE.search(text)
    candidate = block.group(1) if block else text
    start = SQL_START_RE.search(candidate)
    if start:
        candidate = candidate[start.start() :]
    candidate = candidate.strip()
    if ";" in candidate:
        candidate = candidate[: candidate.find(";") + 1]
    return candidate.strip()


def maybe_deterministic_sql(question: str, schema: SchemaDictionary) -> tuple[str, str]:
    if not env_bool("BOYUESQL_ENABLE_SQLITE_TEMPLATES", True):
        return "", ""
    if get_dialect(schema.dialect).name != "sqlite":
        return "", ""
    try:
        from scripts.run_spider2_sqlite_experiment import synthesize_semantic_template_sql
    except Exception:
        return "", ""
    return synthesize_semantic_template_sql(question, schema)


def build_prompt(question: str, schema: SchemaDictionary) -> str:
    limit = env_int("SCHEMA_TABLE_LIMIT", 16)
    selected = retrieve_tables(schema, question, limit=limit, relation_closure=2)
    schema_text = schema_prompt([item.table for item in selected])
    dialect = get_dialect(schema.dialect)
    return f"""You are BoyueSQL, a generic enterprise Text-to-SQL system.
Return exactly one read-only SQL query and no prose.
Use only schema-provided identifiers.
{dialect.error_prompt_rules()}

Question:
{question}

Schema:
{schema_text}

SQL:
"""


def ollama_sql(prompt: str) -> tuple[str, str]:
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = os.environ.get("OLLAMA_SQL_MODEL", "qwen3-vl:8b")
    api = os.environ.get("OLLAMA_API", "generate").strip().lower()
    timeout = env_int("LLM_TIMEOUT", 120)
    num_predict = env_int("NUM_PREDICT", 512)
    payload: dict[str, Any]
    if api == "chat":
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.0, "num_predict": num_predict},
        }
        response = requests.post(f"{base_url}/api/chat", json=payload, timeout=timeout)
        response.raise_for_status()
        raw = str((response.json().get("message") or {}).get("content") or "")
    else:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.0, "num_predict": num_predict},
        }
        response = requests.post(f"{base_url}/api/generate", json=payload, timeout=timeout)
        response.raise_for_status()
        raw = str(response.json().get("response") or "")
    return extract_sql(raw), raw


def execute_sql(sql: str, row_limit: int) -> dict[str, Any]:
    cfg = runtime_config()
    dialect = get_dialect(cfg.dialect)
    validation_error = dialect.validate_select_only(sql)
    if validation_error:
        return {"ok": False, "error": validation_error, "columns": [], "rows": []}
    limited_sql = dialect.limit_query(sql, row_limit)
    conn = connect_database(cfg, default_dialect="sqlite")
    try:
        cur = conn.cursor()
        cur.execute(limited_sql)
        columns = [str(desc[0]) for desc in (cur.description or [])]
        rows = [list(row) for row in cur.fetchall()]
        return {"ok": True, "error": "", "columns": columns, "rows": rows}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "columns": [], "rows": []}
    finally:
        conn.close()


@app.get("/health")
def health():
    cfg = runtime_config()
    source = schema_source_path()
    return jsonify(
        {
            "ok": True,
            "service": "boyuesql",
            "dialect": cfg.dialect,
            "schema_source": source,
            "schema_source_exists": bool(source and (source == ":memory:" or Path(source).exists())),
        }
    )


@app.get("/api/schema")
def schema_summary():
    try:
        schema = load_runtime_schema()
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 400
    tables = [
        {
            "name": table.name,
            "type": table.table_type,
            "columns": [column.name for column in table.columns],
        }
        for table in schema.tables.values()
    ]
    return jsonify({"ok": True, "dialect": schema.dialect, "source": schema.source, "table_count": len(tables), "tables": tables})


@app.post("/api/query")
def query():
    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "question is required"}), 400
    row_limit = int(payload.get("row_limit") or env_int("MAX_ROWS", 500))
    should_execute = bool(payload.get("execute", True))
    started = time.perf_counter()
    try:
        schema = load_runtime_schema()
        dialect = get_dialect(schema.dialect)
        deterministic_sql, deterministic_reason = maybe_deterministic_sql(question, schema)
        if deterministic_sql and not dialect.validate_select_only(deterministic_sql):
            sql = deterministic_sql
            raw = deterministic_reason
        else:
            prompt = build_prompt(question, schema)
            sql, raw = ollama_sql(prompt)
        validation_error = dialect.validate_select_only(sql)
        result = execute_sql(sql, row_limit) if should_execute and not validation_error else None
        return jsonify(
            {
                "ok": not validation_error and (result is None or bool(result.get("ok"))),
                "sql": sql,
                "validation_error": validation_error or "",
                "result": result,
                "raw_response": raw[:2000],
                "latency_s": round(time.perf_counter() - started, 4),
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = env_int("PORT", 5000)
    app.run(host=host, port=port)
