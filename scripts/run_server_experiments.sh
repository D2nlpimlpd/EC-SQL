#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

VENV_DIR="${VENV_DIR:-${PROJECT_ROOT}/.venv}"
if [ -f "${VENV_DIR}/bin/activate" ]; then
  # shellcheck source=/dev/null
  source "${VENV_DIR}/bin/activate"
fi

if [ -f "${PROJECT_ROOT}/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  source "${PROJECT_ROOT}/.env"
  set +a
fi

DATASET_ROOT="${DATASET_ROOT:-/data/text2sql_datasets}"
SPIDER_ROOT="${SPIDER_ROOT:-${DATASET_ROOT}/Spider2}"
MANIFEST="${MANIFEST:-${PROJECT_ROOT}/artifacts/spider2_manifest.csv}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
OUT_DIR="${OUT_DIR:-${PROJECT_ROOT}/artifacts/server_runs/${RUN_ID}}"
SPIDER2_DBT_PACKAGE_CACHE="${SPIDER2_DBT_PACKAGE_CACHE:-${PROJECT_ROOT}/artifacts/dbt_package_cache}"

SQLITE_SMOKE_LIMIT="${SQLITE_SMOKE_LIMIT:-135}"
DBT_SMOKE_LIMIT="${DBT_SMOKE_LIMIT:-68}"
SQLITE_SCHEMA_ONLY_LIMIT="${SQLITE_SCHEMA_ONLY_LIMIT:-135}"
SQLITE_LLM_LIMIT="${SQLITE_LLM_LIMIT:-135}"
SQLITE_GOLD_CASE_LIMIT="${SQLITE_GOLD_CASE_LIMIT:-0}"
SQLITE_GOLD_CASE_OFFSET="${SQLITE_GOLD_CASE_OFFSET:-0}"
DBT_BASELINE_LIMIT="${DBT_BASELINE_LIMIT:-68}"
DBT_EC_SQL_LIMIT="${DBT_EC_SQL_LIMIT:-68}"
DBT_ABLATION_LIMIT="${DBT_ABLATION_LIMIT:-20}"
DBT_LLM_LIMIT="${DBT_LLM_LIMIT:-10}"

RUN_SMOKE="${RUN_SMOKE:-1}"
RUN_SQLITE_SCHEMA_ONLY="${RUN_SQLITE_SCHEMA_ONLY:-1}"
RUN_SQLITE_LLM="${RUN_SQLITE_LLM:-1}"
RUN_DBT_BASELINE="${RUN_DBT_BASELINE:-1}"
RUN_DBT_EC_SQL="${RUN_DBT_EC_SQL:-1}"
RUN_DBT_ABLATIONS="${RUN_DBT_ABLATIONS:-1}"
RUN_DBT_LLM="${RUN_DBT_LLM:-0}"
DRY_RUN="${DRY_RUN:-0}"
RUN_MODEL_CHECK="${RUN_MODEL_CHECK:-1}"
SKIP_EXISTING="${SKIP_EXISTING:-0}"

EC_SQL_MODEL="${EC_SQL_MODEL:-qwen3-vl:8b}"
BASELINE_MODEL="${BASELINE_MODEL:-qwen2.5-coder:7b}"
DBT_EDIT_MODEL="${DBT_EDIT_MODEL:-${EC_SQL_MODEL}}"
EC_SQL_MODELS="${EC_SQL_MODELS:-${EC_SQL_MODEL}}"
BASELINE_MODELS="${BASELINE_MODELS:-${BASELINE_MODEL},sqlcoder:7b}"
DBT_EDIT_MODELS="${DBT_EDIT_MODELS:-${DBT_EDIT_MODEL}}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
OLLAMA_API="${OLLAMA_API:-chat}"
NUM_PREDICT="${NUM_PREDICT:-4096}"
LLM_TIMEOUT="${LLM_TIMEOUT:-360}"
DBT_TIMEOUT="${DBT_TIMEOUT:-240}"
SPIDER2_DBT_CURRENT_DATE="${SPIDER2_DBT_CURRENT_DATE:-2024-09-08}"
PYTHON_BIN="${PYTHON:-python}"

SQLITE_SYSTEMS="${SQLITE_SYSTEMS:-ecsql,no_semantic_templates,no_external_knowledge,no_schema_retrieval,no_repair}"
SQLITE_BASELINE_SYSTEMS="${SQLITE_BASELINE_SYSTEMS:-direct,din_sql_style,dail_sql_style,self_debug_style,mac_sql_style,chess_style}"

trim() {
  local value="$*"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "${value}"
}

slugify() {
  printf '%s' "$1" | sed -E 's/[^A-Za-z0-9._-]+/_/g'
}

mkdir -p "${OUT_DIR}"

cat > "${OUT_DIR}/run_config.env" <<EOF
RUN_ID=${RUN_ID}
PROJECT_ROOT=${PROJECT_ROOT}
DATASET_ROOT=${DATASET_ROOT}
SPIDER_ROOT=${SPIDER_ROOT}
MANIFEST=${MANIFEST}
EC_SQL_MODEL=${EC_SQL_MODEL}
BASELINE_MODEL=${BASELINE_MODEL}
DBT_EDIT_MODEL=${DBT_EDIT_MODEL}
EC_SQL_MODELS=${EC_SQL_MODELS}
BASELINE_MODELS=${BASELINE_MODELS}
DBT_EDIT_MODELS=${DBT_EDIT_MODELS}
OLLAMA_BASE_URL=${OLLAMA_BASE_URL}
OLLAMA_API=${OLLAMA_API}
NUM_PREDICT=${NUM_PREDICT}
LLM_TIMEOUT=${LLM_TIMEOUT}
DBT_TIMEOUT=${DBT_TIMEOUT}
SPIDER2_DBT_CURRENT_DATE=${SPIDER2_DBT_CURRENT_DATE}
SPIDER2_DBT_PACKAGE_CACHE=${SPIDER2_DBT_PACKAGE_CACHE}
SQLITE_SMOKE_LIMIT=${SQLITE_SMOKE_LIMIT}
DBT_SMOKE_LIMIT=${DBT_SMOKE_LIMIT}
SQLITE_SCHEMA_ONLY_LIMIT=${SQLITE_SCHEMA_ONLY_LIMIT}
SQLITE_LLM_LIMIT=${SQLITE_LLM_LIMIT}
SQLITE_GOLD_CASE_LIMIT=${SQLITE_GOLD_CASE_LIMIT}
SQLITE_GOLD_CASE_OFFSET=${SQLITE_GOLD_CASE_OFFSET}
DBT_BASELINE_LIMIT=${DBT_BASELINE_LIMIT}
DBT_EC_SQL_LIMIT=${DBT_EC_SQL_LIMIT}
DBT_ABLATION_LIMIT=${DBT_ABLATION_LIMIT}
DBT_LLM_LIMIT=${DBT_LLM_LIMIT}
RUN_SMOKE=${RUN_SMOKE}
RUN_SQLITE_SCHEMA_ONLY=${RUN_SQLITE_SCHEMA_ONLY}
RUN_SQLITE_LLM=${RUN_SQLITE_LLM}
RUN_DBT_BASELINE=${RUN_DBT_BASELINE}
RUN_DBT_EC_SQL=${RUN_DBT_EC_SQL}
RUN_DBT_ABLATIONS=${RUN_DBT_ABLATIONS}
RUN_DBT_LLM=${RUN_DBT_LLM}
DRY_RUN=${DRY_RUN}
RUN_MODEL_CHECK=${RUN_MODEL_CHECK}
SKIP_EXISTING=${SKIP_EXISTING}
SQLITE_SYSTEMS=${SQLITE_SYSTEMS}
SQLITE_BASELINE_SYSTEMS=${SQLITE_BASELINE_SYSTEMS}
PYTHON_BIN=${PYTHON_BIN}
EOF

"${PYTHON_BIN}" --version > "${OUT_DIR}/python_version.txt" 2>&1 || true

echo "[server-exp] project: ${PROJECT_ROOT}"
echo "[server-exp] spider root: ${SPIDER_ROOT}"
echo "[server-exp] output dir: ${OUT_DIR}"
echo "[server-exp] EC-SQL models: ${EC_SQL_MODELS}"
echo "[server-exp] baseline models: ${BASELINE_MODELS}"
echo "[server-exp] dry run: ${DRY_RUN}"
export SPIDER2_DBT_CURRENT_DATE
export SPIDER2_DBT_PACKAGE_CACHE
mkdir -p "${SPIDER2_DBT_PACKAGE_CACHE}"

if [ "${DRY_RUN}" = "1" ]; then
  cat > "${OUT_DIR}/planned_steps.txt" <<EOF
Smoke gate: RUN_SMOKE=${RUN_SMOKE}, SQLite limit=${SQLITE_SMOKE_LIMIT}, DBT limit=${DBT_SMOKE_LIMIT}
SQLite schema-only: RUN_SQLITE_SCHEMA_ONLY=${RUN_SQLITE_SCHEMA_ONLY}, limit=${SQLITE_SCHEMA_ONLY_LIMIT}
SQLite EC-SQL/ablations: RUN_SQLITE_LLM=${RUN_SQLITE_LLM}, systems=${SQLITE_SYSTEMS}, models=${EC_SQL_MODELS}, limit=${SQLITE_LLM_LIMIT}
SQLite SOTA-style baselines: RUN_SQLITE_LLM=${RUN_SQLITE_LLM}, systems=${SQLITE_BASELINE_SYSTEMS}, models=${BASELINE_MODELS}, limit=${SQLITE_LLM_LIMIT}
SQLite gold slice: offset=${SQLITE_GOLD_CASE_OFFSET}, gold_case_limit=${SQLITE_GOLD_CASE_LIMIT}
DBT starter baseline: RUN_DBT_BASELINE=${RUN_DBT_BASELINE}, limit=${DBT_BASELINE_LIMIT}
DBT deterministic EC-SQL: RUN_DBT_EC_SQL=${RUN_DBT_EC_SQL}, limit=${DBT_EC_SQL_LIMIT}
DBT deterministic ablations: RUN_DBT_ABLATIONS=${RUN_DBT_ABLATIONS}, limit=${DBT_ABLATION_LIMIT}
DBT LLM editing: RUN_DBT_LLM=${RUN_DBT_LLM}, models=${DBT_EDIT_MODELS}, limit=${DBT_LLM_LIMIT}
Skip existing JSON outputs: SKIP_EXISTING=${SKIP_EXISTING}
EOF
  echo "[server-exp] DRY_RUN=1, wrote ${OUT_DIR}/run_config.env and ${OUT_DIR}/planned_steps.txt"
  echo "[server-exp] no dataset, model, DBT, aggregation, or failure-analysis command was executed"
  exit 0
fi

run_json_step() {
  local label="$1"
  local output="$2"
  shift 2
  if [ "${SKIP_EXISTING}" = "1" ] && [ -s "${output}" ]; then
    echo "[server-exp] skip existing ${label}: ${output}"
    return 0
  fi
  echo "[server-exp] ${label}"
  "$@"
}

if [ "${RUN_MODEL_CHECK}" = "1" ] && { [ "${RUN_SQLITE_LLM}" = "1" ] || [ "${RUN_DBT_LLM}" = "1" ]; }; then
  model_check_args=(--base-url "${OLLAMA_BASE_URL}" --timeout 15)
  if [ "${RUN_SQLITE_LLM}" = "1" ]; then
    model_check_args+=(--model "${EC_SQL_MODELS}" --model "${BASELINE_MODELS}")
  fi
  if [ "${RUN_DBT_LLM}" = "1" ]; then
    model_check_args+=(--model "${DBT_EDIT_MODELS}")
  fi
  echo "[server-exp] Checking Ollama model availability"
  "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/check_ollama_models.py" "${model_check_args[@]}"
fi

if [ ! -f "${MANIFEST}" ]; then
  echo "[server-exp] manifest not found; creating ${MANIFEST}"
  "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/spider2_manifest.py" \
    --spider-root "${SPIDER_ROOT}" \
    --out "${MANIFEST}"
fi

if [ "${RUN_SMOKE}" = "1" ]; then
  run_json_step "SQLite smoke" "${OUT_DIR}/spider2_sqlite_smoke.json" \
    "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/run_spider2_sqlite_smoke.py" \
    --manifest "${MANIFEST}" \
    --spider-root "${SPIDER_ROOT}" \
    --limit "${SQLITE_SMOKE_LIMIT}" \
    --out "${OUT_DIR}/spider2_sqlite_smoke.json"

  run_json_step "DBT smoke" "${OUT_DIR}/spider2_dbt_smoke.json" \
    "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/run_spider2_dbt_smoke.py" \
    --spider-root "${SPIDER_ROOT}" \
    --limit "${DBT_SMOKE_LIMIT}" \
    --out "${OUT_DIR}/spider2_dbt_smoke.json"
fi

if [ "${RUN_SQLITE_SCHEMA_ONLY}" = "1" ]; then
  run_json_step "SQLite schema-only executable baseline" "${OUT_DIR}/spider2_sqlite_schema_only.json" \
    "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/run_spider2_sqlite_experiment.py" \
    --manifest "${MANIFEST}" \
    --spider-root "${SPIDER_ROOT}" \
    --systems schema_only \
    --limit "${SQLITE_SCHEMA_ONLY_LIMIT}" \
    --require-gold \
    --gold-case-limit "${SQLITE_GOLD_CASE_LIMIT}" \
    --gold-case-offset "${SQLITE_GOLD_CASE_OFFSET}" \
    --out "${OUT_DIR}/spider2_sqlite_schema_only.json"
fi

if [ "${RUN_SQLITE_LLM}" = "1" ]; then
  IFS=',' read -r -a EC_SQL_MODEL_LIST <<< "${EC_SQL_MODELS}"
  for MODEL_NAME_RAW in "${EC_SQL_MODEL_LIST[@]}"; do
    MODEL_NAME="$(trim "${MODEL_NAME_RAW}")"
    if [ -z "${MODEL_NAME}" ]; then
      continue
    fi
    MODEL_SLUG="$(slugify "${MODEL_NAME}")"
    SQLITE_EC_SQL_OUT="${OUT_DIR}/spider2_sqlite_ecsql_ablation_${MODEL_SLUG}.json"
    run_json_step "SQLite EC-SQL and ablation systems: ${MODEL_NAME}" "${SQLITE_EC_SQL_OUT}" \
      "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/run_spider2_sqlite_experiment.py" \
      --manifest "${MANIFEST}" \
      --spider-root "${SPIDER_ROOT}" \
      --systems "${SQLITE_SYSTEMS}" \
      --model "${MODEL_NAME}" \
      --ollama-base-url "${OLLAMA_BASE_URL}" \
      --ollama-api "${OLLAMA_API}" \
      --limit "${SQLITE_LLM_LIMIT}" \
      --require-gold \
      --gold-case-limit "${SQLITE_GOLD_CASE_LIMIT}" \
      --gold-case-offset "${SQLITE_GOLD_CASE_OFFSET}" \
      --num-predict "${NUM_PREDICT}" \
      --timeout "${LLM_TIMEOUT}" \
      --max-repairs 5 \
      --out "${SQLITE_EC_SQL_OUT}"
  done

  IFS=',' read -r -a BASELINE_MODEL_LIST <<< "${BASELINE_MODELS}"
  for MODEL_NAME_RAW in "${BASELINE_MODEL_LIST[@]}"; do
    MODEL_NAME="$(trim "${MODEL_NAME_RAW}")"
    if [ -z "${MODEL_NAME}" ]; then
      continue
    fi
    MODEL_SLUG="$(slugify "${MODEL_NAME}")"
    SQLITE_BASELINE_OUT="${OUT_DIR}/spider2_sqlite_sota_baselines_${MODEL_SLUG}.json"
    run_json_step "SQLite SOTA-style baseline systems: ${MODEL_NAME}" "${SQLITE_BASELINE_OUT}" \
      "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/run_spider2_sqlite_experiment.py" \
      --manifest "${MANIFEST}" \
      --spider-root "${SPIDER_ROOT}" \
      --systems "${SQLITE_BASELINE_SYSTEMS}" \
      --model "${MODEL_NAME}" \
      --ollama-base-url "${OLLAMA_BASE_URL}" \
      --ollama-api "${OLLAMA_API}" \
      --limit "${SQLITE_LLM_LIMIT}" \
      --require-gold \
      --gold-case-limit "${SQLITE_GOLD_CASE_LIMIT}" \
      --gold-case-offset "${SQLITE_GOLD_CASE_OFFSET}" \
      --num-predict "${NUM_PREDICT}" \
      --timeout "${LLM_TIMEOUT}" \
      --max-repairs 0 \
      --out "${SQLITE_BASELINE_OUT}"
  done
fi

if [ "${RUN_DBT_BASELINE}" = "1" ]; then
  run_json_step "DBT starter-project baseline" "${OUT_DIR}/spider2_dbt_existing_project.json" \
    "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/run_spider2_dbt_experiment.py" \
    --spider-root "${SPIDER_ROOT}" \
    --limit "${DBT_BASELINE_LIMIT}" \
    --out "${OUT_DIR}/spider2_dbt_existing_project.json" \
    --timeout "${DBT_TIMEOUT}"
fi

run_dbt_ecsql_no_llm() {
  local label="$1"
  local limit="$2"
  shift 2
  local output="${OUT_DIR}/spider2_dbt_llm_edit_${label}.json"
  run_json_step "DBT EC-SQL deterministic edit: ${label}" "${output}" \
    "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/run_spider2_dbt_llm_edit_experiment.py" \
    --spider-root "${SPIDER_ROOT}" \
    --limit "${limit}" \
    --work-dir "${OUT_DIR}/work_dbt_${label}" \
    --out "${output}" \
    --timeout "${DBT_TIMEOUT}" \
    --edit-rounds 5 \
    --no-llm \
    --spider2-current-date "${SPIDER2_DBT_CURRENT_DATE}" \
    "$@"
}

if [ "${RUN_DBT_EC_SQL}" = "1" ]; then
  run_dbt_ecsql_no_llm "ecsql_deterministic_full" "${DBT_EC_SQL_LIMIT}" \
    --missing-ref-fallback \
    --missing-source-fallback \
    --duckdb-type-fallback \
    --declared-column-fallback \
    --declared-model-synthesis \
    --declared-model-fallback
fi

if [ "${RUN_DBT_ABLATIONS}" = "1" ]; then
  run_dbt_ecsql_no_llm "ecsql_ablation_no_declared_model_synthesis" "${DBT_ABLATION_LIMIT}" \
    --missing-ref-fallback \
    --missing-source-fallback \
    --duckdb-type-fallback \
    --declared-column-fallback \
    --declared-model-fallback

  run_dbt_ecsql_no_llm "ecsql_ablation_no_duckdb_type_repair" "${DBT_ABLATION_LIMIT}" \
    --missing-ref-fallback \
    --missing-source-fallback \
    --declared-column-fallback \
    --declared-model-synthesis \
    --declared-model-fallback

  run_dbt_ecsql_no_llm "ecsql_ablation_no_missing_ref_source_fallback" "${DBT_ABLATION_LIMIT}" \
    --duckdb-type-fallback \
    --declared-column-fallback \
    --declared-model-synthesis \
    --declared-model-fallback

  run_dbt_ecsql_no_llm "ecsql_ablation_no_declared_column_completion" "${DBT_ABLATION_LIMIT}" \
    --missing-ref-fallback \
    --missing-source-fallback \
    --duckdb-type-fallback \
    --declared-model-synthesis \
    --declared-model-fallback

  run_dbt_ecsql_no_llm "ecsql_ablation_no_related_dimension_enrichment" "${DBT_ABLATION_LIMIT}" \
    --missing-ref-fallback \
    --missing-source-fallback \
    --duckdb-type-fallback \
    --declared-column-fallback \
    --declared-model-synthesis \
    --declared-model-fallback \
    --disable-related-dimension-enrichment

  run_dbt_ecsql_no_llm "ecsql_ablation_no_fact_pivot_synthesis" "${DBT_ABLATION_LIMIT}" \
    --missing-ref-fallback \
    --missing-source-fallback \
    --duckdb-type-fallback \
    --declared-column-fallback \
    --declared-model-synthesis \
    --declared-model-fallback \
    --disable-long-to-wide-pivot \
    --disable-fact-dimension-summary

  run_dbt_ecsql_no_llm "ecsql_ablation_no_final_failed_model_placeholder" "${DBT_ABLATION_LIMIT}" \
    --missing-ref-fallback \
    --missing-source-fallback \
    --duckdb-type-fallback \
    --declared-column-fallback \
    --declared-model-synthesis \
    --declared-model-fallback \
    --disable-final-failed-model-placeholder
fi

if [ "${RUN_DBT_LLM}" = "1" ]; then
  IFS=',' read -r -a DBT_EDIT_MODEL_LIST <<< "${DBT_EDIT_MODELS}"
  for MODEL_NAME_RAW in "${DBT_EDIT_MODEL_LIST[@]}"; do
    MODEL_NAME="$(trim "${MODEL_NAME_RAW}")"
    if [ -z "${MODEL_NAME}" ]; then
      continue
    fi
    MODEL_SLUG="$(slugify "${MODEL_NAME}")"
    DBT_LLM_OUT="${OUT_DIR}/spider2_dbt_llm_edit_${MODEL_SLUG}.json"
    run_json_step "DBT LLM editing experiment: ${MODEL_NAME}" "${DBT_LLM_OUT}" \
      "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/run_spider2_dbt_llm_edit_experiment.py" \
      --spider-root "${SPIDER_ROOT}" \
      --limit "${DBT_LLM_LIMIT}" \
      --model "${MODEL_NAME}" \
      --ollama-base-url "${OLLAMA_BASE_URL}" \
      --ollama-api "${OLLAMA_API}" \
      --num-predict "${NUM_PREDICT}" \
      --llm-timeout "${LLM_TIMEOUT}" \
      --timeout "${DBT_TIMEOUT}" \
      --edit-rounds 3 \
      --missing-ref-fallback \
      --missing-source-fallback \
      --duckdb-type-fallback \
      --declared-column-fallback \
      --declared-model-synthesis \
      --declared-model-fallback \
      --spider2-current-date "${SPIDER2_DBT_CURRENT_DATE}" \
      --out "${DBT_LLM_OUT}"
  done
fi

echo "[server-exp] Aggregate experiment artifacts"
"${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/aggregate_experiment_results.py" \
  --inputs "${OUT_DIR}/spider2*.json" "${OUT_DIR}/*_registered.json" \
  --out-dir "${OUT_DIR}/summary" \
  --summary-name "server_${RUN_ID}"

echo "[server-exp] Diagnose experiment failures"
"${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/analyze_experiment_failures.py" \
  --inputs "${OUT_DIR}/spider2*.json" "${OUT_DIR}/*_registered.json" \
  --out-dir "${OUT_DIR}/summary" \
  --name "server_${RUN_ID}_failures"

echo "[server-exp] done"
echo "[server-exp] summary: ${OUT_DIR}/summary/server_${RUN_ID}.md"
echo "[server-exp] failure diagnostics: ${OUT_DIR}/summary/server_${RUN_ID}_failures.md"
