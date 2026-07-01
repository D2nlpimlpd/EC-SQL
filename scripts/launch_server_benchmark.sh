#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

RUN_ID="${RUN_ID:-server_full_$(date +%Y%m%d_%H%M%S)}"
OUT_DIR="${OUT_DIR:-${PROJECT_ROOT}/artifacts/server_runs/${RUN_ID}}"
LOG_FILE="${LOG_FILE:-${OUT_DIR}/server_job.log}"
PID_FILE="${PID_FILE:-${OUT_DIR}/server_job.pid}"
MODE="${LAUNCH_MODE:-benchmark}"
PYTHON_BOOTSTRAP="${PYTHON:-python3}"
RUN_STARTED_AT_EPOCH="$(date +%s)"
RUN_MARKER_ID="${RUN_MARKER_ID:-$(date -u +%Y%m%dT%H%M%SZ)_$$}"

if [ "${RESUME:-0}" = "1" ]; then
  MODE="resume"
fi

mkdir -p "${OUT_DIR}"

SUMMARY_DIR="${OUT_DIR}/summary"
FINAL_BUNDLE="${SUMMARY_DIR}/server_${RUN_ID}_result_bundle.zip"
FINAL_CHECKSUM="${SUMMARY_DIR}/server_${RUN_ID}_result_bundle.sha256"
FINAL_MANIFEST="${SUMMARY_DIR}/server_${RUN_ID}_result_bundle_manifest.json"
FINAL_CERTIFICATE="${SUMMARY_DIR}/server_${RUN_ID}_completion_certificate.json"
FINAL_IMPORT_REPORT="${SUMMARY_DIR}/server_${RUN_ID}_import_report.json"

cat > "${OUT_DIR}/launch.env" <<EOF
RUN_ID=${RUN_ID}
OUT_DIR=${OUT_DIR}
MODE=${MODE}
RUN_MARKER_ID=${RUN_MARKER_ID}
RUN_STARTED_AT_EPOCH=${RUN_STARTED_AT_EPOCH}
DATASET_ROOT=${DATASET_ROOT:-}
SPIDER_ROOT=${SPIDER_ROOT:-}
MANIFEST=${MANIFEST:-}
EC_SQL_MODELS=${EC_SQL_MODELS:-}
BASELINE_MODELS=${BASELINE_MODELS:-}
RUN_DBT_LLM=${RUN_DBT_LLM:-}
OLLAMA_BASE_URL=${OLLAMA_BASE_URL:-}
EOF

echo "[launch] RUN_ID=${RUN_ID}"
echo "[launch] mode=${MODE}"
echo "[launch] out=${OUT_DIR}"
echo "[launch] log=${LOG_FILE}"
echo "[launch] marker=${RUN_MARKER_ID}"

echo "[launch] writing expected artifact plan"
"${PYTHON_BOOTSTRAP}" "${PROJECT_ROOT}/scripts/plan_server_matrix.py" --run-id "${RUN_ID}" --out-dir "${OUT_DIR}"

if [ -s "${PID_FILE}" ]; then
  old_pid="$(cat "${PID_FILE}")"
  if kill -0 "${old_pid}" >/dev/null 2>&1; then
    echo "[launch] existing process appears to be running: pid=${old_pid}" >&2
    echo "[launch] use RUN_ID=${RUN_ID} bash scripts/one_click_linux.sh status"
    exit 2
  fi
fi

mkdir -p "${SUMMARY_DIR}"
cat > "${OUT_DIR}/server_job.marker" <<EOF
RUN_ID=${RUN_ID}
RUN_MARKER_ID=${RUN_MARKER_ID}
RUN_STARTED_AT_EPOCH=${RUN_STARTED_AT_EPOCH}
MODE=${MODE}
OUT_DIR=${OUT_DIR}
EOF

if [ "${MODE}" != "resume" ]; then
  echo "[launch] fresh launch: clearing stale terminal bundle files for RUN_ID=${RUN_ID}"
  rm -f \
    "${FINAL_BUNDLE}" \
    "${FINAL_CHECKSUM}" \
    "${FINAL_MANIFEST}" \
    "${FINAL_CERTIFICATE}" \
    "${FINAL_IMPORT_REPORT}"
else
  echo "[launch] resume mode: preserving existing terminal files"
fi

if [ "${BACKGROUND:-1}" = "0" ]; then
  echo "[launch] running in foreground"
  RUN_ID="${RUN_ID}" OUT_DIR="${OUT_DIR}" bash "${PROJECT_ROOT}/scripts/one_click_linux.sh" "${MODE}" 2>&1 | tee "${LOG_FILE}"
  exit "${PIPESTATUS[0]}"
fi

nohup env RUN_ID="${RUN_ID}" OUT_DIR="${OUT_DIR}" bash "${PROJECT_ROOT}/scripts/one_click_linux.sh" "${MODE}" > "${LOG_FILE}" 2>&1 &
pid="$!"
echo "${pid}" > "${PID_FILE}"
echo "[launch] started pid=${pid}"
echo "[launch] status: RUN_ID=${RUN_ID} bash scripts/one_click_linux.sh status"
