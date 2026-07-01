#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

MODE="${1:-${ONE_CLICK_MODE:-smoke}}"
VENV_DIR="${VENV_DIR:-${PROJECT_ROOT}/.venv}"
DATASET_ROOT="${DATASET_ROOT:-/data/text2sql_datasets}"
SPIDER_ROOT="${SPIDER_ROOT:-${DATASET_ROOT}/Spider2}"
MANIFEST="${MANIFEST:-${PROJECT_ROOT}/artifacts/spider2_manifest.csv}"
PYTHON_BOOTSTRAP="${PYTHON:-python3}"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/one_click_linux.sh [preflight|setup|models|dataset-report|contract|plan|dry-run|smoke|benchmark|paper-run|paper-launch|launch|resume|summarize|status|validate|evidence|abstract|bundle|diagnostics|upload-packet|audit|service|all]

Modes:
  preflight  Check Python/Git/files/disk/dataset/manifest/Ollama prerequisites.
  setup      Create/update .venv, install dependencies, download Spider2, write manifest, and pull configured models.
  models     Pull required Ollama models and download configured HuggingFace snapshots.
  dataset-report Build the Spider2 dataset scale report from the manifest.
  contract   Build the machine-readable server acceptance contract.
  plan       Write expected server matrix artifacts and command checklist for RUN_ID.
  dry-run    Ensure setup, then print the full benchmark plan without running models/DBT.
  smoke      Ensure setup, then run a short no-LLM SQLite/DBT smoke benchmark. Default.
  benchmark  Ensure setup, then run scripts/run_full_server_benchmark.sh.
  paper-run  Run the full foreground paper matrix, validate it, build evidence, bundle results, and audit.
  paper-launch Start paper-run in the background with nohup and a PID/log file.
  launch     Start benchmark/resume in the background with nohup and a PID/log file.
  resume     Resume an interrupted full benchmark with RUN_ID and SKIP_EXISTING=1.
  summarize  Rebuild summary/failure reports for an existing RUN_ID without rerunning cases.
  status     Show PID/log/artifact status for RUN_ID.
  validate   Validate that RUN_ID contains the full required server experiment matrix.
  evidence   Build paper-ready evidence CSV/Markdown/LaTeX and abstract for RUN_ID.
  abstract   Build the server-result-grounded abstract for RUN_ID.
  bundle     Package server summaries, evidence, logs, and JSON artifacts for return.
  diagnostics Write run diagnostics and package a pending result bundle for troubleshooting.
  upload-packet Package the release, checksums, handoff docs, and acceptance contract for server upload.
  audit      Audit current goal readiness and list PASS/FAIL/PENDING evidence.
  service    Ensure setup, then start ecsql_service.py.
  all        Ensure setup, run full benchmark, then start the service.

Useful environment variables:
  DATASET_ROOT=/data/text2sql_datasets
  VENV_DIR=/path/to/.venv
  PYTHON=python3.11
  FORCE_SETUP=1
  SKIP_SETUP=1
  RUN_ID=my_server_run
  SKIP_EXISTING=1
  EC_SQL_MODELS=qwen3-vl:8b
  BASELINE_MODELS=qwen2.5-coder:7b,sqlcoder:7b
  HF_BASELINE_MODELS=NumbersStation/nsql-6B,deepseek-ai/deepseek-coder-6.7b-instruct
  HF_EXTRA_MODELS=
  HF_HOME=/data/huggingface
  HF_SKIP_DOWNLOAD=0
  HF_DOWNLOAD_WARN_ONLY=0
  SETUP_SKIP_MODELS=0
  OLLAMA_BASE_URL=http://localhost:11434
EOF
}

run_preflight() {
  echo "[one-click] server preflight"
  preflight_args=(
    "${PROJECT_ROOT}/scripts/server_preflight.py"
    --project-root "${PROJECT_ROOT}"
    --spider-root "${SPIDER_ROOT}"
    --manifest "${MANIFEST}"
    --min-free-gb "${PREFLIGHT_MIN_FREE_GB:-5}"
    --ollama-base-url "${OLLAMA_BASE_URL:-http://localhost:11434}"
    --model "${EC_SQL_MODELS:-${EC_SQL_MODEL:-qwen3-vl:8b}}"
    --model "${BASELINE_MODELS:-${BASELINE_MODEL:-qwen2.5-coder:7b},sqlcoder:7b}"
  )
  if [ "${PREFLIGHT_REQUIRE_DATASET:-0}" = "1" ]; then
    preflight_args+=(--require-dataset)
  fi
  if [ "${PREFLIGHT_SKIP_OLLAMA:-0}" = "1" ]; then
    preflight_args+=(--skip-ollama)
  fi
  if [ "${PREFLIGHT_WARN_ONLY:-0}" = "1" ]; then
    preflight_args+=(--warn-only)
  fi
  "${PYTHON_BOOTSTRAP}" "${preflight_args[@]}"
}

select_bootstrap_python() {
  if [ -z "${PYTHON:-}" ] && [ -x "${VENV_DIR}/bin/python" ]; then
    PYTHON_BOOTSTRAP="${VENV_DIR}/bin/python"
  fi
}

run_dataset_report() {
  echo "[one-click] building Spider2 dataset scale report"
  local report_args=(
    "${PROJECT_ROOT}/scripts/build_dataset_scale_report.py"
    --dataset-root "${SPIDER_ROOT}"
    --manifest "${MANIFEST}"
  )
  if [ -n "${RUN_ID:-}" ]; then
    local report_dir="${OUT_DIR:-${PROJECT_ROOT}/artifacts/server_runs/${RUN_ID}}/summary"
    mkdir -p "${report_dir}"
    report_args+=(
      --json-out "${report_dir}/server_${RUN_ID}_dataset_scale_report.json"
      --md-out "${report_dir}/server_${RUN_ID}_dataset_scale_report.md"
    )
  fi
  "${PYTHON_BOOTSTRAP}" "${report_args[@]}"
}

ensure_setup() {
  if [ "${SKIP_SETUP:-0}" = "1" ]; then
    echo "[one-click] SKIP_SETUP=1, skipping setup"
    select_bootstrap_python
    return 0
  fi
  if [ "${FORCE_SETUP:-0}" != "1" ] && [ -x "${VENV_DIR}/bin/python" ] && [ -f "${MANIFEST}" ]; then
    echo "[one-click] existing virtualenv and manifest found; set FORCE_SETUP=1 to reinstall"
    select_bootstrap_python
    run_dataset_report
    return 0
  fi
  echo "[one-click] running setup_linux.sh"
  PYTHON="${PYTHON_BOOTSTRAP}" \
  VENV_DIR="${VENV_DIR}" \
  DATASET_ROOT="${DATASET_ROOT}" \
  bash "${PROJECT_ROOT}/scripts/setup_linux.sh"
  select_bootstrap_python
  run_dataset_report
}

run_dry_run() {
  echo "[one-click] full benchmark dry-run"
  DRY_RUN=1 \
  VENV_DIR="${VENV_DIR}" \
  DATASET_ROOT="${DATASET_ROOT}" \
  SPIDER_ROOT="${SPIDER_ROOT}" \
  MANIFEST="${MANIFEST}" \
  bash "${PROJECT_ROOT}/scripts/run_full_server_benchmark.sh"
}

run_models() {
  echo "[one-click] pulling Ollama models"
  "${PYTHON_BOOTSTRAP}" "${PROJECT_ROOT}/scripts/pull_ollama_models.py" \
    --base-url "${OLLAMA_BASE_URL:-http://localhost:11434}" \
    --model "${EC_SQL_MODELS:-${EC_SQL_MODEL:-qwen3-vl:8b}}" \
    --model "${BASELINE_MODELS:-${BASELINE_MODEL:-qwen2.5-coder:7b},sqlcoder:7b}" \
    --timeout "${OLLAMA_PULL_TIMEOUT:-1800}"
  if [ "${HF_SKIP_DOWNLOAD:-0}" = "1" ]; then
    echo "[one-click] HF_SKIP_DOWNLOAD=1, skipping HuggingFace snapshot downloads"
    return 0
  fi
  echo "[one-click] downloading HuggingFace model snapshots"
  hf_args=(
    "${PROJECT_ROOT}/scripts/download_hf_models.py"
    --model "${HF_BASELINE_MODELS:-NumbersStation/nsql-6B,deepseek-ai/deepseek-coder-6.7b-instruct}"
  )
  if [ -n "${HF_EXTRA_MODELS:-}" ]; then
    hf_args+=(--model "${HF_EXTRA_MODELS}")
  fi
  if [ -n "${HF_HOME:-}" ]; then
    hf_args+=(--cache-dir "${HF_HOME}")
  fi
  if [ "${HF_DOWNLOAD_WARN_ONLY:-0}" = "1" ]; then
    hf_args+=(--warn-only)
  fi
  "${PYTHON_BOOTSTRAP}" "${hf_args[@]}"
}

run_plan() {
  local plan_run_id="${RUN_ID:-server_full_spider2}"
  echo "[one-click] planning server matrix RUN_ID=${plan_run_id}"
  "${PYTHON_BOOTSTRAP}" "${PROJECT_ROOT}/scripts/plan_server_matrix.py" \
    --run-id "${plan_run_id}"
  RUN_ID="${plan_run_id}" run_acceptance_contract
}

run_acceptance_contract() {
  local contract_run_id="${RUN_ID:-server_full_spider2}"
  echo "[one-click] building acceptance contract RUN_ID=${contract_run_id}"
  "${PYTHON_BOOTSTRAP}" "${PROJECT_ROOT}/scripts/build_server_acceptance_contract.py" \
    --run-id "${contract_run_id}"
}

run_smoke() {
  echo "[one-click] short smoke benchmark"
  RUN_ID="${RUN_ID:-one_click_smoke_$(date +%Y%m%d_%H%M%S)}" \
  VENV_DIR="${VENV_DIR}" \
  DATASET_ROOT="${DATASET_ROOT}" \
  SPIDER_ROOT="${SPIDER_ROOT}" \
  MANIFEST="${MANIFEST}" \
  RUN_SMOKE=1 \
  SQLITE_SMOKE_LIMIT="${SQLITE_SMOKE_LIMIT:-5}" \
  DBT_SMOKE_LIMIT="${DBT_SMOKE_LIMIT:-2}" \
  RUN_SQLITE_SCHEMA_ONLY=1 \
  SQLITE_SCHEMA_ONLY_LIMIT="${SQLITE_SCHEMA_ONLY_LIMIT:-5}" \
  RUN_SQLITE_LLM=0 \
  RUN_DBT_BASELINE=0 \
  RUN_DBT_EC_SQL=1 \
  DBT_EC_SQL_LIMIT="${DBT_EC_SQL_LIMIT:-2}" \
  RUN_DBT_ABLATIONS=1 \
  DBT_ABLATION_LIMIT="${DBT_ABLATION_LIMIT:-2}" \
  RUN_DBT_LLM=0 \
  bash "${PROJECT_ROOT}/scripts/run_server_experiments.sh"
}

run_benchmark() {
  echo "[one-click] full benchmark"
  VENV_DIR="${VENV_DIR}" \
  DATASET_ROOT="${DATASET_ROOT}" \
  SPIDER_ROOT="${SPIDER_ROOT}" \
  MANIFEST="${MANIFEST}" \
  bash "${PROJECT_ROOT}/scripts/run_full_server_benchmark.sh"
}

run_paper_run() {
  export RUN_ID="${RUN_ID:-server_full_spider2}"
  echo "[one-click] paper-run RUN_ID=${RUN_ID}"
  ensure_setup
  if [ "${PAPER_RUN_SKIP_MODELS:-0}" != "1" ]; then
    run_models
  fi
  run_plan
  run_benchmark
  run_summarize
  run_validate
  run_evidence
  run_bundle
  run_upload_packet
  AUDIT_STRICT=1 SERVER_RUN_ID="${RUN_ID}" run_audit
}

run_launch() {
  ensure_setup
  run_plan
  echo "[one-click] launching background benchmark"
  VENV_DIR="${VENV_DIR}" \
  DATASET_ROOT="${DATASET_ROOT}" \
  SPIDER_ROOT="${SPIDER_ROOT}" \
  MANIFEST="${MANIFEST}" \
  bash "${PROJECT_ROOT}/scripts/launch_server_benchmark.sh"
}

run_paper_launch() {
  export RUN_ID="${RUN_ID:-server_full_spider2}"
  export LAUNCH_MODE="paper-run"
  echo "[one-click] launching background paper-run RUN_ID=${RUN_ID}"
  run_launch
}

require_run_id() {
  if [ -z "${RUN_ID:-}" ]; then
    echo "RUN_ID is required for this mode. Example: RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh resume" >&2
    exit 2
  fi
}

run_resume() {
  require_run_id
  echo "[one-click] resuming benchmark RUN_ID=${RUN_ID}"
  SKIP_EXISTING=1 \
  VENV_DIR="${VENV_DIR}" \
  DATASET_ROOT="${DATASET_ROOT}" \
  SPIDER_ROOT="${SPIDER_ROOT}" \
  MANIFEST="${MANIFEST}" \
  bash "${PROJECT_ROOT}/scripts/run_full_server_benchmark.sh"
}

run_summarize() {
  require_run_id
  local out_dir="${OUT_DIR:-${PROJECT_ROOT}/artifacts/server_runs/${RUN_ID}}"
  local summary_dir="${out_dir}/summary"
  mkdir -p "${summary_dir}"
  echo "[one-click] rebuilding summaries for RUN_ID=${RUN_ID}"
  "${PYTHON_BOOTSTRAP}" "${PROJECT_ROOT}/scripts/aggregate_experiment_results.py" \
    --inputs "${out_dir}/spider2*.json" "${out_dir}/*_registered.json" \
    --out-dir "${summary_dir}" \
    --summary-name "server_${RUN_ID}"
  "${PYTHON_BOOTSTRAP}" "${PROJECT_ROOT}/scripts/analyze_experiment_failures.py" \
    --inputs "${out_dir}/spider2*.json" "${out_dir}/*_registered.json" \
    --out-dir "${summary_dir}" \
    --name "server_${RUN_ID}_failures"
  echo "[one-click] summary: ${summary_dir}/server_${RUN_ID}.md"
  echo "[one-click] failure diagnostics: ${summary_dir}/server_${RUN_ID}_failures.md"
}

run_status() {
  require_run_id
  "${PYTHON_BOOTSTRAP}" "${PROJECT_ROOT}/scripts/server_run_status.py" \
    --run-id "${RUN_ID}" \
    --tail "${STATUS_TAIL_LINES:-40}"
}

run_validate() {
  require_run_id
  echo "[one-click] validating server matrix RUN_ID=${RUN_ID}"
  "${PYTHON_BOOTSTRAP}" "${PROJECT_ROOT}/scripts/validate_server_matrix.py" \
    --run-id "${RUN_ID}"
}

run_evidence() {
  require_run_id
  run_dataset_report
  run_acceptance_contract
  echo "[one-click] building server evidence report RUN_ID=${RUN_ID}"
  evidence_args=(
    "${PROJECT_ROOT}/scripts/build_server_evidence_report.py"
    --run-id "${RUN_ID}"
  )
  if [ "${EVIDENCE_ALLOW_PENDING:-0}" = "1" ]; then
    evidence_args+=(--allow-pending)
  fi
  "${PYTHON_BOOTSTRAP}" "${evidence_args[@]}"
  run_abstract
}

run_abstract() {
  require_run_id
  echo "[one-click] building server abstract RUN_ID=${RUN_ID}"
  abstract_args=(
    "${PROJECT_ROOT}/scripts/build_server_abstract.py"
    --run-id "${RUN_ID}"
  )
  if [ "${ABSTRACT_ALLOW_PENDING:-${EVIDENCE_ALLOW_PENDING:-0}}" = "1" ]; then
    abstract_args+=(--allow-pending)
  fi
  "${PYTHON_BOOTSTRAP}" "${abstract_args[@]}"
}

run_bundle() {
  require_run_id
  echo "[one-click] building server result bundle RUN_ID=${RUN_ID}"
  bundle_args=(
    "${PROJECT_ROOT}/scripts/build_server_result_bundle.py"
    --run-id "${RUN_ID}"
  )
  if [ "${BUNDLE_ALLOW_PENDING:-0}" = "1" ]; then
    bundle_args+=(--allow-pending)
  fi
  "${PYTHON_BOOTSTRAP}" "${bundle_args[@]}"
}

run_diagnostics() {
  require_run_id
  echo "[one-click] writing server diagnostics RUN_ID=${RUN_ID}"
  "${PYTHON_BOOTSTRAP}" "${PROJECT_ROOT}/scripts/build_server_diagnostics.py" \
    --run-id "${RUN_ID}" \
    --tail "${DIAGNOSTICS_TAIL_LINES:-200}"
  BUNDLE_ALLOW_PENDING=1 run_bundle
}

run_upload_packet() {
  require_run_id
  echo "[one-click] building server upload packet RUN_ID=${RUN_ID}"
  "${PYTHON_BOOTSTRAP}" "${PROJECT_ROOT}/scripts/build_server_upload_packet.py" \
    --run-id "${RUN_ID}"
}

run_audit() {
  echo "[one-click] goal readiness audit"
  audit_args=(
    "${PROJECT_ROOT}/scripts/audit_goal_readiness.py"
    --dataset-root "${SPIDER_ROOT}"
    --manifest "${MANIFEST}"
  )
  if [ -n "${SERVER_RUN_ID:-}" ]; then
    audit_args+=(--server-run-id "${SERVER_RUN_ID}")
  elif [ -n "${RUN_ID:-}" ]; then
    audit_args+=(--server-run-id "${RUN_ID}")
  fi
  if [ "${AUDIT_STRICT:-0}" = "1" ]; then
    audit_args+=(--strict)
  fi
  "${PYTHON_BOOTSTRAP}" "${audit_args[@]}"
}

run_service() {
  echo "[one-click] starting service"
  VENV_DIR="${VENV_DIR}" \
  DATASET_ROOT="${DATASET_ROOT}" \
  SPIDER_ROOT="${SPIDER_ROOT}" \
  MANIFEST="${MANIFEST}" \
  bash "${PROJECT_ROOT}/scripts/start_linux.sh"
}

case "${MODE}" in
  -h|--help|help)
    usage
    ;;
  setup)
    ensure_setup
    if [ "${SETUP_SKIP_MODELS:-0}" != "1" ]; then
      run_models
    fi
    ;;
  models)
    run_models
    ;;
  dataset-report)
    select_bootstrap_python
    run_dataset_report
    ;;
  contract)
    select_bootstrap_python
    run_acceptance_contract
    ;;
  plan)
    run_plan
    ;;
  preflight)
    run_preflight
    ;;
  dry-run)
    ensure_setup
    run_dry_run
    ;;
  smoke)
    ensure_setup
    run_smoke
    ;;
  benchmark)
    ensure_setup
    run_benchmark
    ;;
  paper-run)
    run_paper_run
    ;;
  paper-launch)
    run_paper_launch
    ;;
  launch)
    run_launch
    ;;
  resume)
    ensure_setup
    run_resume
    ;;
  summarize)
    run_summarize
    ;;
  status)
    run_status
    ;;
  validate)
    run_validate
    ;;
  evidence)
    run_evidence
    ;;
  abstract)
    run_abstract
    ;;
  bundle)
    run_bundle
    ;;
  diagnostics)
    run_diagnostics
    ;;
  upload-packet)
    run_upload_packet
    ;;
  audit)
    run_audit
    ;;
  service)
    ensure_setup
    run_service
    ;;
  all)
    ensure_setup
    run_benchmark
    run_service
    ;;
  *)
    echo "Unknown one-click mode: ${MODE}" >&2
    usage >&2
    exit 2
    ;;
esac
