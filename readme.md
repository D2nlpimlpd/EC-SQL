# BoyueSQL

BoyueSQL is a generic Text-to-SQL coordination stack for enterprise schemas.
It combines schema retrieval, optional planning, SQL generation, static/live
validation, iterative repair, and semantic evidence checking. The current
migration target is database-agnostic evaluation on Spider2 local SQLite and
Spider2-DBT/DuckDB, with server-side scripts for larger model and ablation
runs.

## Architecture

```text
Question
  -> schema dictionary / schema-KG retrieval
  -> optional planner
  -> SQL generator
  -> dialect-aware guard and execution probe
  -> repair loop
  -> semantic evidence evaluation
```

## Generic Layer

- `boyuesql_generic/dialects.py`: SQL dialect adapters for limit/probe/date
  behavior, identifier quoting, select-only validation, and error
  classification.
- `boyuesql_generic/dictionary.py`: portable schema dictionary loaders for
  JSON, SQLite, and DuckDB.
- `boyuesql_generic/connections.py`: DB-API connection and live-catalog
  adapters. Oracle is loaded lazily only when selected; SQLite and DuckDB can
  run without Oracle client libraries.
- `boyuesql_generic/retrieval.py`: lightweight schema retrieval and prompt
  construction utilities.
- `boyuesql_generic/eval_protocol.py`: result normalization and semantic
  evidence helpers.

## One-command Linux Setup

```bash
bash scripts/setup_linux.sh
```

The Linux setup installs the pinned server-runtime environment by
default. It skips the older full local/web dependency stack unless
`INSTALL_LEGACY_REQUIREMENTS=1` is set, which keeps server benchmark
setup reproducible and avoids unnecessary dependency backtracking.

This creates `.venv`, installs Python dependencies, downloads Spider2 into
`${DATASET_ROOT:-/data/text2sql_datasets}`, installs the no-credential local
SQLite/DBT assets, and writes:

```text
artifacts/spider2_manifest.csv
```

For a single server entrypoint that performs setup when needed and then runs a
safe smoke matrix by default:

```bash
bash scripts/one_click_linux.sh
```

For the one-command setup path that also pulls the configured Ollama models
and downloads HuggingFace snapshots:

```bash
bash scripts/one_click_linux.sh setup
```

Use explicit modes for other handoff steps:

```bash
bash scripts/one_click_linux.sh preflight   # check prerequisites only
bash scripts/one_click_linux.sh models      # pull Ollama models and download HF snapshots
bash scripts/one_click_linux.sh dataset-report # build Spider2 scale evidence
bash scripts/one_click_linux.sh contract    # build acceptance contract
bash scripts/one_click_linux.sh plan        # write expected artifacts and command checklist
bash scripts/one_click_linux.sh dry-run     # setup plus benchmark plan only
bash scripts/one_click_linux.sh benchmark   # setup plus full Spider2 matrix
bash scripts/one_click_linux.sh paper-run   # full foreground matrix plus validate/evidence/bundle/audit
bash scripts/one_click_linux.sh paper-launch # full background matrix plus validate/evidence/bundle/audit
bash scripts/one_click_linux.sh launch      # run benchmark/resume in background
bash scripts/one_click_linux.sh resume      # resume an interrupted RUN_ID
bash scripts/one_click_linux.sh summarize   # rebuild reports for an existing RUN_ID
bash scripts/one_click_linux.sh status      # show PID/log/artifact status for RUN_ID
bash scripts/one_click_linux.sh validate    # validate full server matrix evidence for RUN_ID
bash scripts/one_click_linux.sh evidence    # build paper-ready evidence report and abstract for RUN_ID
bash scripts/one_click_linux.sh abstract    # rebuild only the server-result-grounded abstract
bash scripts/one_click_linux.sh bundle      # package reports/logs/JSON artifacts for return
bash scripts/one_click_linux.sh diagnostics # package pending logs/results for troubleshooting
bash scripts/one_click_linux.sh upload-packet # package release plus handoff/acceptance docs
bash scripts/one_click_linux.sh audit       # list PASS/FAIL/PENDING goal evidence
bash scripts/one_click_linux.sh service     # setup plus generic API service
```

To verify that legacy private-database papers or figures have not drifted back
into the workspace root, run:

```bash
python scripts/audit_workspace_residue.py
```

If the report lists only old local artifacts that should not be part of the
generalized codebase, preserve them under `artifacts/legacy_workspace_residue/`
with `python scripts/audit_workspace_residue.py --quarantine`.

The preflight step also runs `scripts/check_linux_shell_scripts.py`, which
uses GNU/Git Bash to parse every `scripts/*.sh` file with `bash -n` before any
long dataset or model job is started.

## One-command Server Experiments

```bash
bash scripts/run_server_experiments.sh
```

The server experiment runner performs smoke gates, SQLite BoyueSQL ablations,
SOTA-style prompt baselines, DBT starter-project evaluation, optional DBT LLM
editing, result aggregation, and failure diagnostics.

Before an expensive server run, check the planned matrix without executing
dataset, DBT, or model commands:

```bash
DRY_RUN=1 bash scripts/run_server_experiments.sh
```

Then run a tiny no-LLM end-to-end smoke matrix:

```bash
RUN_ID=mini_server_smoke \
RUN_SMOKE=1 SQLITE_SMOKE_LIMIT=3 DBT_SMOKE_LIMIT=2 \
RUN_SQLITE_SCHEMA_ONLY=1 SQLITE_SCHEMA_ONLY_LIMIT=3 \
RUN_SQLITE_LLM=0 \
RUN_DBT_BASELINE=0 \
RUN_DBT_BOYUESQL=1 DBT_BOYUESQL_LIMIT=2 \
RUN_DBT_ABLATIONS=1 DBT_ABLATION_LIMIT=2 \
RUN_DBT_LLM=0 \
bash scripts/run_server_experiments.sh
```

For a tiny integrated LLM comparison through the same server runner:

```bash
RUN_ID=sqlite_llm_server_tiny \
RUN_SMOKE=0 RUN_SQLITE_SCHEMA_ONLY=0 RUN_SQLITE_LLM=1 \
SQLITE_SYSTEMS=boyuesql \
SQLITE_BASELINE_SYSTEMS=direct,din_sql_style \
SQLITE_LLM_LIMIT=135 SQLITE_GOLD_CASE_LIMIT=2 \
BOYUESQL_MODELS=qwen2.5-coder:7b \
BASELINE_MODELS=qwen2.5-coder:7b,sqlcoder:7b,qwen3:32b \
RUN_DBT_BASELINE=0 RUN_DBT_BOYUESQL=0 RUN_DBT_ABLATIONS=0 RUN_DBT_LLM=0 \
NUM_PREDICT=512 LLM_TIMEOUT=180 \
bash scripts/run_server_experiments.sh
```

Useful overrides:

```bash
PYTHON=/path/to/.venv/bin/python \
BOYUESQL_MODELS=qwen3-vl:8b \
BASELINE_MODELS=qwen2.5-coder:7b,sqlcoder:7b,qwen3:32b \
OLLAMA_BASE_URL=http://localhost:11434 \
OLLAMA_API=chat \
bash scripts/run_server_experiments.sh
```

On a long-running Linux server, the simplest foreground paper run is:

```bash
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh paper-run
```

This runs the full matrix, rebuilds summaries, validates the required
SOTA/ablation coverage, builds paper evidence, packages a result bundle, and
ends with the strict readiness audit. If the run is interrupted, resume with
`RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh resume`, then rerun
`validate`, `evidence`, `bundle`, and `audit`. If the background job stops or
times out before producing a complete bundle, run
`RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh diagnostics` to
write `server_<RUN_ID>_diagnostics.{json,md}` and package a pending result
bundle that can be inspected locally with `--allow-pending`.
The `plan` mode also writes `server_matrix_coverage.{json,md}` beside
`expected_artifacts.csv`; this preview must be `PASS` before launching a paper
run because it checks the planned SOTA and ablation coverage against the
acceptance thresholds.

For a background job instead, pull models once and launch the full benchmark:

```bash
bash scripts/one_click_linux.sh models
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh paper-launch
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh status
```

`paper-launch` starts the same full post-processing chain as `paper-run` in the
background. If you prefer manually staged background execution, use:

```bash
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh plan
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh launch
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh status
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh validate
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh evidence
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh abstract
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh bundle
```

The background launcher writes `server_job.pid`, `server_job.log`, and
`launch.env` under `artifacts/server_runs/<RUN_ID>/`, and it also writes a
`server_job.marker` file containing the current launch marker and start time.
A fresh `launch` or `paper-launch` clears stale terminal artifacts for the same
`RUN_ID` before starting, including the result bundle, bundle checksum,
manifest, completion certificate, and local import report. To preserve existing
terminal artifacts while continuing an interrupted run, use
`RESUME=1 RUN_ID=<existing_run_id> bash scripts/one_click_linux.sh launch`, or
run the foreground resume command shown below.
If you are using the single upload packet on a remote server, the equivalent
command is `RUN_ID=<existing_run_id> bash RUN_PACKET_ON_SERVER.sh resume`.

For the full no-credential Spider2 server benchmark profile:

```bash
bash scripts/run_full_server_benchmark.sh
```

It runs the SQLite smoke/schema-only/LLM matrix, SQLite SOTA-style prompt
baselines, DBT starter baseline, deterministic DBT BoyueSQL, and DBT
deterministic ablations. Override `BOYUESQL_MODELS`, `BASELINE_MODELS`,
`RUN_DBT_LLM`, and the limit variables on larger servers.
The SOTA-style baseline rows are local prompt-style proxies unless their
`official_reproduction` field is `true` in the generated summary/evidence CSV.
LLM runs preflight the configured Ollama endpoint by default; set
`RUN_MODEL_CHECK=0` only for non-standard compatible endpoints.
Interrupted long runs can resume with
`RUN_ID=<existing_run_id> bash scripts/one_click_linux.sh resume`, which sets
`SKIP_EXISTING=1` and regenerates aggregate reports after unfinished steps
complete. To rebuild only the summary and failure diagnostics for an existing
run without executing any benchmark case:

```bash
RUN_ID=<existing_run_id> bash scripts/one_click_linux.sh summarize
```

To audit whether the full project goal is actually complete:

```bash
bash scripts/one_click_linux.sh audit
AUDIT_STRICT=1 SERVER_RUN_ID=<server_run_id> bash scripts/one_click_linux.sh audit
```

The strict audit exits non-zero while any required item is still `FAIL` or
`PENDING`; in particular, the full server SOTA matrix remains pending until a
real Linux/server run has produced a summary under
`artifacts/server_runs/<SERVER_RUN_ID>/summary/`. The server matrix audit
requires aggregate summary/case/failure reports plus BoyueSQL full results,
SQLite ablations, SOTA-style baselines across at least three models, DBT
baseline/full results, and multiple DBT ablations with non-empty JSON
artifacts.

For multi-model comparison on a larger server, use comma-separated lists, e.g.
`BOYUESQL_MODELS=qwen3-vl:8b,mistral:7b` and
`BASELINE_MODELS=qwen2.5-coder:7b,sqlcoder:7b,qwen3:32b`.  See
`SERVER_MODEL_GUIDE.md` for the A100 80GB HF snapshot download list and
optional heavier Text2SQL/code-model baselines.

Enable expensive DBT LLM editing on a larger server:

```bash
RUN_DBT_LLM=1 DBT_LLM_LIMIT=68 bash scripts/run_server_experiments.sh
```

Each run writes a summary and a failure report under
`artifacts/server_runs/<RUN_ID>/summary/`. The files named
`server_<RUN_ID>_failures.*` group non-passing cases by suite, system, model,
stage, and error class so the next retrieval/repair change can target the
dominant failure mode rather than only comparing aggregate scores.
After validation and evidence generation pass, run
`RUN_ID=<RUN_ID> bash scripts/one_click_linux.sh bundle` to create
`server_<RUN_ID>_result_bundle.zip` and `.sha256` under the same `summary/`
directory for return from the Linux server.
After copying those two files back, verify the returned bundle locally:

```bash
python scripts/finalize_server_result.py \
  /path/to/server_<RUN_ID>_result_bundle.zip \
  --checksum /path/to/server_<RUN_ID>_result_bundle.sha256 \
  --run-id <RUN_ID> \
  --overwrite
```

The finalizer verifies the bundle, imports it under
`artifacts/server_runs/<RUN_ID>/`, runs the strict readiness audit with that
imported run directory, and copies the server-result abstract to
`artifacts/boyuesql_spider2_server_result_abstract.tex` plus a root-level
`boyuesql_spider2_server_result_abstract.tex` convenience copy. The lower-level
manual commands remain available when you want to inspect each step:

```bash
python scripts/verify_server_result_bundle.py \
  /path/to/server_<RUN_ID>_result_bundle.zip \
  --checksum /path/to/server_<RUN_ID>_result_bundle.sha256 \
  --run-id <RUN_ID>

python scripts/import_server_result_bundle.py \
  /path/to/server_<RUN_ID>_result_bundle.zip \
  --checksum /path/to/server_<RUN_ID>_result_bundle.sha256 \
  --run-id <RUN_ID> \
  --overwrite
```

The verifier checks the bundle checksum, internal manifest hashes, expected
JSON artifacts, summary/evidence reports, the server-result abstract, the
completion certificate, and the required SOTA/ablation matrix coverage before
accepting the server result. The completion certificate is written as
`summary/server_<RUN_ID>_completion_certificate.json` and records both the
matrix check and the abstract check. The importer repeats the same checks,
safely extracts the archive under `artifacts/server_runs/<RUN_ID>/`, and writes
`summary/server_<RUN_ID>_import_report.json` for the local audit.

## One-command Service Startup

```bash
bash scripts/start_linux.sh
```

The startup script activates `.venv` when present, loads `.env`, and runs
`${APP_ENTRY:-boyuesql_service.py}`. This default entrypoint is the clean
generic service used by the Spider2 experiments and server deployment package.

## Clean Server Release Package

To build a server upload package that excludes legacy private-database demo
files, local papers, PDFs, notebooks, datasets, caches, and generated outputs:

```bash
python scripts/build_server_release.py
python scripts/smoke_test_server_release.py
python scripts/build_server_upload_packet.py --run-id server_full_spider2
python scripts/smoke_test_server_upload_packet.py \
  --packet artifacts/server_release/server_full_spider2_server_upload_packet.zip
python scripts/smoke_test_server_upload_packet.py \
  --packet artifacts/server_release/server_full_spider2_server_upload_packet.zip \
  --run-doctor --run-diagnostics
python scripts/smoke_test_server_acceptance_flow.py
python scripts/build_server_handoff_commands.py --host user@server --remote-dir ~/boyuesql_spider2_run
python scripts/run_server_handoff.py --host user@server --stage submit
```

The package is written to:

```text
artifacts/server_release/boyuesql_spider2_server.zip
artifacts/server_release/boyuesql_spider2_server.sha256
artifacts/server_release/server_full_spider2_server_submission_manifest.json
artifacts/server_release/server_full_spider2_server_submission_manifest.md
artifacts/server_release/server_full_spider2_server_upload_packet.zip
artifacts/server_release/server_full_spider2_server_upload_packet.sha256
artifacts/server_release/server_full_spider2_server_upload_packet_manifest.json
```

The release package contains the generic service, `boyuesql_generic`, scripts,
tests, server requirements, docs, and the local modified RagAnything source
under `third_party/raganything-1.3.1`.
The submission manifest records the release SHA-256, local evidence checks,
server command sequence, and expected artifact matrix for the default
`server_full_spider2` run.
The upload packet wraps the release archive, release checksum, handoff command
sheet, submission manifest, dataset-scale report, acceptance contract, and an
internal `UPLOAD_PACKET_MANIFEST.json` with hashes, so a single verified file can
be copied to the server or archived with the paper artifact. After manually
extracting the packet on a server (`unzip ...` or
`python3 -m zipfile -e ...`), `bash RUN_PACKET_ON_SERVER.sh background`
validates the embedded release, extracts it, runs setup/model preparation, and
launches the full background paper run. The same script supports
`doctor`, `status`, `wait`, `validate`, `evidence`, `bundle`, `diagnostics`, and `audit` without
resetting an existing run directory; set `RESET_RELEASE=1` only when you
intentionally want to re-extract the release.
Run `bash RUN_PACKET_ON_SERVER.sh doctor` first on a new server for a lightweight
packet/release/shell/Python preflight that does not download datasets, pull
models, or launch experiments.
Locally, `python scripts/smoke_test_server_upload_packet.py --packet
artifacts/server_release/server_full_spider2_server_upload_packet.zip
--run-doctor --run-diagnostics` exercises the same packet-native doctor
entrypoint and the pending diagnostics-bundle path after extracting the upload
packet.
The automated handoff runner also uses this packet-native entrypoint: `submit`
re-extracts the current upload packet and runs the remote doctor stage before
launch, and `launch`, `status`, and `wait` call `RUN_PACKET_ON_SERVER.sh` inside
the extracted upload packet rather than a separate release path.
The packet smoke test simulates the server-side unpack path locally: it verifies
the packet checksum, extracts the internal manifest, validates the embedded
release checksum, extracts the release, and runs the release smoke checks.
The acceptance-flow smoke test uses a temporary synthetic server run to verify
that result bundling, checksum verification, safe import, and final matrix
checking agree on the `RUN_ID` and file-name contract before a long server job
is launched.
The smoke test verifies the zip checksum, safely extracts the package, checks
that shell scripts are LF-only, compiles key Python entrypoints, runs shell
syntax checks, and executes server preflight inside the extracted release
directory.
The handoff command generator writes copyable upload, server-run, download,
verification, import, and strict-audit commands under `artifacts/server_release/`.
The handoff runner prints the same flow as a dry run by default and now uploads
the single server packet rather than separate release files; add `--execute` to
actually run `ssh`/`scp`. Run `--stage remote-preflight --execute` first when
you want to check the target server's shell, Python, checksum tool, free disk,
GPU, and Ollama reachability without uploading or launching. The preflight uses
`--remote-min-free-gb 20` by default and can be made strict with
`--remote-require-gpu` and `--remote-require-ollama` when the full LLM matrix
must run on a GPU-backed Ollama server. The recommended one-command path is
`--stage supervise --execute`, which uploads the packet, launches the background
run, waits for the result bundle, downloads it, finalizes it locally, and copies
the server-result abstract. Use `--stage submit --background --execute` to
upload and start a long server job, `--stage wait --execute` to poll until the
result bundle exists and passes remote `verify_server_result_bundle.py`, then
`--stage collect --execute` to download and finalize the result with
`finalize_server_result.py`.
If the server job fails or `wait` times out, use
`--stage collect-diagnostics --execute`; the supervised path runs the same
diagnostics fallback automatically before re-raising the failure for
troubleshooting.

## Environment

```bash
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_SQL_MODEL=qwen3-vl:8b
OLLAMA_EMBEDDING_MODEL=qwen3-vl:8b
STRICT_QWEN3_VL_ONLY=1

BOYUESQL_DIALECT=sqlite
DB_PATH=/data/text2sql_datasets/example.sqlite
DATASET_ROOT=/data/text2sql_datasets
DATA_DICT_PATH=data_dictionary.json
RAG_CACHE_DIR=./rag_cache
MAX_ROWS=500
```

For an Oracle deployment, set `BOYUESQL_DIALECT=oracle` and provide
`DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, and `DB_SERVICE_NAME` through
`.env`; set `INSTALL_ORACLE=1` before `scripts/setup_linux.sh` only when the
optional Oracle driver should be installed. For SQLite or DuckDB, set
`BOYUESQL_DIALECT=sqlite` or
`BOYUESQL_DIALECT=duckdb` and provide `DB_PATH` or `DB_DATABASE`. The source
tree intentionally does not contain private host, account, service, or schema
values.

## Local Evidence

Current local checks cover:

- Spider2 manifest coverage: 1,162 benchmark rows across 955 unique
  instances/projects, 226 unique logical database names, and 5 execution
  engines/settings in the current local manifest.
- Spider2 dataset-scale report: `scripts/build_dataset_scale_report.py`
  writes `artifacts/dataset_scale_report.json` and
  `artifacts/dataset_scale_report.md`, documenting the 600+ benchmark-scale
  threshold, unique instance coverage, SQLite rows, and DBT rows directly from
  the manifest.
- Spider2-Lite SQLite smoke: local schema probes pass on the executable SQLite
  subset.
- Spider2-DBT/DuckDB smoke: validates starter/gold DuckDB artifacts and reports
  upstream missing-artifact cases.
- SQLite BoyueSQL experiments: support `schema_only`, `boyuesql`,
  `no_external_knowledge`, `no_schema_retrieval`, `no_repair`, and `direct`.
- SQLite SOTA-style prompt baselines: `din_sql_style`, `dail_sql_style`,
  `self_debug_style`, `mac_sql_style`, and `chess_style` run under the same
  local model/database/evaluation protocol for reproducible comparison. These
  are prompt-style proxy baselines rather than official reproductions of the
  referenced systems; the machine-readable scope is recorded in
  `baselines/baseline_manifest.json` and is propagated into aggregated CSV and
  server evidence reports.
- DBT LLM-edit experiments: non-cheating project editing with valid JSON edit
  parsing, source-proxy repair, public YAML-declared model synthesis, value-join
  model synthesis, taxonomy/crosswalk mapping synthesis, DuckDB syntax repair,
  numeric-tolerant semantic comparison for floating-point tails, and best-round
  reporting.
- Failure diagnostics: `scripts/analyze_experiment_failures.py` parses SQLite,
  DBT, DBT-edit, and smoke artifacts, then emits CSV/Markdown reports covering
  invalid columns, missing tables/refs, syntax/type failures, result mismatch,
  semantic guard failures, placeholders, timeouts, and model-output failures.

See `SERVER_RUNBOOK.md` and `GENERALIZATION_PLAN.md` for the latest measured
results and server run commands. Official externally executed SOTA baselines can
be imported with `scripts/register_external_baseline_result.py`, which records
their source repository, command, commit, and `official_reproduction=True`
metadata before aggregation. Use
`scripts/register_external_baseline_results.py` for bulk CSV imports; generated
`spider2_external_baseline_*.json` files are included by normal
`one_click_linux.sh summarize` runs.
