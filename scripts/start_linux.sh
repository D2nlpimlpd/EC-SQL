#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-${PROJECT_ROOT}/.venv}"
APP_ENTRY="${APP_ENTRY:-ecsql_service.py}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-5000}"

cd "${PROJECT_ROOT}"

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

export FLASK_RUN_HOST="${HOST}"
export FLASK_RUN_PORT="${PORT}"
export DATASET_ROOT="${DATASET_ROOT:-/data/text2sql_datasets}"
export EC_SQL_DIALECT="${EC_SQL_DIALECT:-sqlite}"

if [ ! -f "${PROJECT_ROOT}/${APP_ENTRY}" ]; then
  echo "Application entry file not found: ${PROJECT_ROOT}/${APP_ENTRY}" >&2
  echo "Set APP_ENTRY to a valid Python file, for example APP_ENTRY=ecsql_service.py." >&2
  exit 1
fi

python "${APP_ENTRY}"
