#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DATASET_ROOT="${DATASET_ROOT:-/data/text2sql_datasets}"
PYTHON_BIN="${PYTHON:-python3}"

cd "${PROJECT_ROOT}"
"${PYTHON_BIN}" "${SCRIPT_DIR}/download_spider2.py" --root "${DATASET_ROOT}" "$@"
