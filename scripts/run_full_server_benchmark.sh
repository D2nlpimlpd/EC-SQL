#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

export RUN_ID="${RUN_ID:-full_server_$(date +%Y%m%d_%H%M%S)}"

# Full no-credential Spider2 local benchmark defaults.
export RUN_SMOKE="${RUN_SMOKE:-1}"
export SQLITE_SMOKE_LIMIT="${SQLITE_SMOKE_LIMIT:-135}"
export DBT_SMOKE_LIMIT="${DBT_SMOKE_LIMIT:-68}"

export RUN_SQLITE_SCHEMA_ONLY="${RUN_SQLITE_SCHEMA_ONLY:-1}"
export SQLITE_SCHEMA_ONLY_LIMIT="${SQLITE_SCHEMA_ONLY_LIMIT:-135}"

export RUN_SQLITE_LLM="${RUN_SQLITE_LLM:-1}"
export SQLITE_LLM_LIMIT="${SQLITE_LLM_LIMIT:-135}"
export SQLITE_GOLD_CASE_LIMIT="${SQLITE_GOLD_CASE_LIMIT:-0}"
export SQLITE_GOLD_CASE_OFFSET="${SQLITE_GOLD_CASE_OFFSET:-0}"
export SQLITE_SYSTEMS="${SQLITE_SYSTEMS:-ecsql,no_semantic_templates,no_external_knowledge,no_schema_retrieval,no_repair}"
export SQLITE_BASELINE_SYSTEMS="${SQLITE_BASELINE_SYSTEMS:-direct,din_sql_style,dail_sql_style,self_debug_style,mac_sql_style,chess_style}"

export RUN_DBT_BASELINE="${RUN_DBT_BASELINE:-1}"
export DBT_BASELINE_LIMIT="${DBT_BASELINE_LIMIT:-68}"
export RUN_DBT_EC_SQL="${RUN_DBT_EC_SQL:-1}"
export DBT_EC_SQL_LIMIT="${DBT_EC_SQL_LIMIT:-68}"
export RUN_DBT_ABLATIONS="${RUN_DBT_ABLATIONS:-1}"
export DBT_ABLATION_LIMIT="${DBT_ABLATION_LIMIT:-68}"

# DBT LLM editing is expensive and less stable than the deterministic path.
# Enable it on a larger server with RUN_DBT_LLM=1.
export RUN_DBT_LLM="${RUN_DBT_LLM:-0}"
export DBT_LLM_LIMIT="${DBT_LLM_LIMIT:-68}"

export EC_SQL_MODELS="${EC_SQL_MODELS:-qwen3-vl:8b}"
export BASELINE_MODELS="${BASELINE_MODELS:-qwen2.5-coder:7b,sqlcoder:7b}"
export DBT_EDIT_MODELS="${DBT_EDIT_MODELS:-${EC_SQL_MODELS}}"

export OLLAMA_API="${OLLAMA_API:-chat}"
export NUM_PREDICT="${NUM_PREDICT:-4096}"
export LLM_TIMEOUT="${LLM_TIMEOUT:-360}"
export DBT_TIMEOUT="${DBT_TIMEOUT:-240}"

echo "[full-benchmark] RUN_ID=${RUN_ID}"
echo "[full-benchmark] EC_SQL_MODELS=${EC_SQL_MODELS}"
echo "[full-benchmark] BASELINE_MODELS=${BASELINE_MODELS}"
echo "[full-benchmark] RUN_DBT_LLM=${RUN_DBT_LLM}"

bash "${PROJECT_ROOT}/scripts/run_server_experiments.sh"
