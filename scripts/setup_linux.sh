#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-${PROJECT_ROOT}/.venv}"
PYTHON_BIN="${PYTHON:-python3}"
DATASET_ROOT="${DATASET_ROOT:-/data/text2sql_datasets}"
SPIDER2_LOCALDB="${SPIDER2_LOCALDB:-1}"
SPIDER2_DBT="${SPIDER2_DBT:-1}"
INSTALL_ORACLE="${INSTALL_ORACLE:-0}"
INSTALL_LEGACY_REQUIREMENTS="${INSTALL_LEGACY_REQUIREMENTS:-0}"
INSTALL_RAGANYTHING_LOCAL="${INSTALL_RAGANYTHING_LOCAL:-0}"

cd "${PROJECT_ROOT}"

if ! command -v git >/dev/null 2>&1; then
  echo "git is required. Install git first." >&2
  exit 1
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Python executable not found: ${PYTHON_BIN}" >&2
  exit 1
fi

if ! "${PYTHON_BIN}" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python >= 3.10 is required")
PY
then
  echo "Install Python >= 3.10 before running setup." >&2
  exit 1
fi

if ! "${PYTHON_BIN}" -m venv --help >/dev/null 2>&1; then
  echo "python venv support is missing. On Ubuntu/Debian, install python3-venv." >&2
  exit 1
fi

"${PYTHON_BIN}" -m venv "${VENV_DIR}"
# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip wheel setuptools
if [ -f constraints-server.txt ]; then
  python -m pip install -c constraints-server.txt -r requirements-server.txt
else
  python -m pip install -r requirements-server.txt
fi
if [ "${INSTALL_LEGACY_REQUIREMENTS}" = "1" ]; then
  python -m pip install -r requirements.txt
  python -m pip install -r requirements-raganything.txt
fi
if [ "${INSTALL_ORACLE}" = "1" ]; then
  python -m pip install -r requirements-oracle.txt
fi
if [ "${INSTALL_RAGANYTHING_LOCAL}" = "1" ] && [ -d "${PROJECT_ROOT}/third_party/raganything-1.3.1" ]; then
  python -m pip install --no-deps -e "${PROJECT_ROOT}/third_party/raganything-1.3.1"
fi

download_args=(--root "${DATASET_ROOT}")
if [ "${SPIDER2_LOCALDB}" = "1" ]; then
  download_args+=(--localdb)
fi
if [ "${SPIDER2_DBT}" = "1" ]; then
  download_args+=(--dbt)
fi
bash "${PROJECT_ROOT}/scripts/download_spider2.sh" "${download_args[@]}"

python "${PROJECT_ROOT}/scripts/spider2_manifest.py" \
  --spider-root "${DATASET_ROOT}/Spider2" \
  --out "${PROJECT_ROOT}/artifacts/spider2_manifest.csv"

cat <<EOF
Setup complete.
Virtualenv: ${VENV_DIR}
Dataset root: ${DATASET_ROOT}

Next:
  source "${VENV_DIR}/bin/activate"
  cp .env.example .env
  edit .env for your DB/LLM endpoint
  bash scripts/start_linux.sh

Smoke test:
  python scripts/run_spider2_sqlite_smoke.py \
    --manifest artifacts/spider2_manifest.csv \
    --spider-root "${DATASET_ROOT}/Spider2" \
    --limit 20

DBT smoke test:
  python scripts/run_spider2_dbt_smoke.py \
    --spider-root "${DATASET_ROOT}/Spider2" \
    --limit 68 \
    --out artifacts/spider2_dbt_smoke68.json

One-command benchmark gate:
  bash scripts/run_server_experiments.sh

Set SPIDER2_LOCALDB=0 or SPIDER2_DBT=0 before setup to skip optional dataset downloads.
Set INSTALL_ORACLE=1 before setup only when you need the optional Oracle connector.
Set INSTALL_LEGACY_REQUIREMENTS=1 only when you need the old full local/web dependencies.
Set INSTALL_RAGANYTHING_LOCAL=1 only when you need to import the local RagAnything package directly.
EOF
