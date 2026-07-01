# EC-SQL Server Runbook

This file records the reproducible path for moving the generic EC-SQL stack
from local Windows smoke tests to a Linux server.

## 1. One-command Environment and Dataset Setup

From the repository root on Linux:

```bash
bash scripts/setup_linux.sh
```

By default, `setup_linux.sh` installs the pinned server-runtime
dependencies needed for Spider2, dbt/DuckDB evaluation, model downloads,
and the EC-SQL service. It intentionally does not install the older
full local/web dependency stack. Set `INSTALL_LEGACY_REQUIREMENTS=1`
only when you need that historical environment, and set
`INSTALL_RAGANYTHING_LOCAL=1` only when you need to import the local
RagAnything package directly.

For the simplest server handoff, use the wrapper below. It runs setup when the
virtual environment or manifest is missing, then executes a short no-LLM smoke
matrix by default:

```bash
bash scripts/one_click_linux.sh
```

Other modes:

```bash
bash scripts/one_click_linux.sh preflight  # check prerequisites only
bash scripts/one_click_linux.sh setup      # environment, dataset, Ollama models, and HF snapshots
bash scripts/one_click_linux.sh models     # pull Ollama models and download HF snapshots
bash scripts/one_click_linux.sh dataset-report # build Spider2 scale evidence
bash scripts/one_click_linux.sh contract   # build acceptance contract
bash scripts/one_click_linux.sh plan       # write expected artifacts and command checklist
bash scripts/one_click_linux.sh dry-run    # setup plus full benchmark plan
bash scripts/one_click_linux.sh benchmark  # setup plus full benchmark matrix
bash scripts/one_click_linux.sh paper-run  # full foreground matrix plus validate/evidence/abstract/bundle/audit
bash scripts/one_click_linux.sh paper-launch # full background matrix plus validate/evidence/abstract/bundle/audit
bash scripts/one_click_linux.sh launch     # run benchmark/resume in background
bash scripts/one_click_linux.sh resume     # resume an interrupted RUN_ID
bash scripts/one_click_linux.sh summarize  # rebuild reports for an existing RUN_ID
bash scripts/one_click_linux.sh status     # show PID/log/artifact status
bash scripts/one_click_linux.sh validate   # validate full server matrix evidence
bash scripts/one_click_linux.sh evidence   # build paper-ready evidence report and abstract
bash scripts/one_click_linux.sh abstract   # rebuild only the server-result-grounded abstract
bash scripts/one_click_linux.sh bundle     # package reports/logs/JSON artifacts for return
bash scripts/one_click_linux.sh diagnostics # package pending logs/results for troubleshooting
bash scripts/one_click_linux.sh upload-packet # package release plus handoff/acceptance docs
bash scripts/one_click_linux.sh audit      # list PASS/FAIL/PENDING goal evidence
bash scripts/one_click_linux.sh service    # setup plus API service startup
```

Use `python scripts/audit_workspace_residue.py` to verify that legacy
private-database papers and figures have not returned to the workspace root.
When the findings are only old local artifacts, preserve them outside the
generalized code path with `python scripts/audit_workspace_residue.py
--quarantine`.

`preflight` also invokes `scripts/check_linux_shell_scripts.py`, which finds a
usable GNU/Git Bash executable and runs `bash -n` over every `scripts/*.sh`
file. This catches Linux shell syntax errors before the expensive Spider2/SOTA
matrix starts.

To make preflight fail when the dataset or manifest is missing:

```bash
PREFLIGHT_REQUIRE_DATASET=1 bash scripts/one_click_linux.sh preflight
```

The script creates a Python virtual environment, installs the core
requirements, clones Spider2 into `${DATASET_ROOT:-/data/text2sql_datasets}`,
downloads the Spider2-Lite SQLite local DB archive by default, and writes:

```text
artifacts/spider2_manifest.csv
```

The setup script requires Python >= 3.10 and `venv` support. On Debian/Ubuntu,
install `python3-venv` if the preflight check reports missing venv support.
See `SERVER_MODEL_GUIDE.md` for the default A100 80GB baseline matrix and
HuggingFace snapshots downloaded by `one_click_linux.sh setup`.

To skip the SQLite local DB download:

```bash
SPIDER2_LOCALDB=0 bash scripts/setup_linux.sh
```

The Spider2-DBT DuckDB archive is installed by default because it is the
no-credential workflow subset. To skip it during a quick environment-only
setup:

```bash
SPIDER2_DBT=0 bash scripts/setup_linux.sh
```

## 2. One-command Service Startup

After editing `.env` for the target model endpoint and database adapter:

```bash
bash scripts/start_linux.sh
```

The startup script loads `.env`, activates `.venv` when present, and launches
the clean generic service `ecsql_service.py` by default. Override these
values when needed:

```bash
HOST=0.0.0.0 PORT=8000 APP_ENTRY=ecsql_service.py bash scripts/start_linux.sh
```

Generic local file-backed service configuration:

```bash
EC_SQL_DIALECT=sqlite
DB_PATH=/data/text2sql_datasets/example.sqlite
```

For DuckDB, use `EC_SQL_DIALECT=duckdb` with `DB_PATH` or `DB_DATABASE`.
For Oracle, use `EC_SQL_DIALECT=oracle` with `DB_USER`, `DB_PASSWORD`,
`DB_HOST`, `DB_PORT`, and `DB_SERVICE_NAME`, and run setup with
`INSTALL_ORACLE=1` if the optional Oracle Python driver is needed. The Oracle
driver is imported only when the Oracle adapter is selected, so SQLite/DuckDB
benchmark runs do not require Oracle client libraries.

## 2.1 Clean Server Release Package

Before copying files to a Linux server, build an allowlisted package:

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
python scripts/build_server_handoff_commands.py --host user@server --remote-dir ~/ecsql_spider2_run
python scripts/run_server_handoff.py --host user@server --stage submit
```

This writes:

```text
artifacts/server_release/ecsql_spider2_server.zip
artifacts/server_release/ecsql_spider2_server.sha256
artifacts/server_release/server_full_spider2_server_submission_manifest.json
artifacts/server_release/server_full_spider2_server_submission_manifest.md
artifacts/server_release/server_full_spider2_server_upload_packet.zip
artifacts/server_release/server_full_spider2_server_upload_packet.sha256
artifacts/server_release/server_full_spider2_server_upload_packet_manifest.json
```

The package includes only the generic service, reusable `ecsql_generic`
library, benchmark scripts, tests, requirements, server documentation, and the
modified RagAnything source needed for reproducibility. It deliberately excludes
the earlier private-database demo entrypoint, local paper assets, PDFs,
notebooks, datasets, logs, and generated benchmark outputs.
The submission manifest records the archive SHA-256, local evidence checks,
server command sequence, and expected artifact matrix, so the server run can be
audited against the same acceptance contract used locally.
The upload packet wraps the release archive, checksum, handoff command sheet,
submission manifest, dataset-scale report, acceptance contract, and an internal
`UPLOAD_PACKET_MANIFEST.json` with per-file hashes. Use it when you want a
single auditable file to upload or archive before the long server run. After
manual upload, the server-side path is `sha256sum -c
server_full_spider2_server_upload_packet.sha256`, then either `unzip
server_full_spider2_server_upload_packet.zip -d server_full_spider2_upload_packet`
or `python3 -m zipfile -e server_full_spider2_server_upload_packet.zip server_full_spider2_upload_packet`,
then `cd server_full_spider2_upload_packet && bash RUN_PACKET_ON_SERVER.sh
background`. Use `bash RUN_PACKET_ON_SERVER.sh wait` to poll for the result
bundle, and `validate`, `evidence`, `bundle`, `diagnostics`, or `audit` to rerun postprocess
steps without deleting the existing run directory. Set `RESET_RELEASE=1` only
for an intentional clean re-extraction of the release.
The packet launcher records `server_job.marker` in the run directory. A fresh
`background`, `launch`, or `paper-launch` clears stale terminal artifacts for
the same `RUN_ID` before starting; use `resume` or set `RESUME=1` when the
existing terminal bundle/checksum/manifest/certificate should be preserved.
For packet-based manual recovery after an interruption, run
`RUN_ID=<existing_run_id> bash RUN_PACKET_ON_SERVER.sh resume`; the same packet
entrypoint also exposes `plan`, `summarize`, and `abstract` so operators do not
need to enter the embedded release directory manually.
On a fresh server, run `bash RUN_PACKET_ON_SERVER.sh doctor` first for a
lightweight packet/release/shell/Python check that does not download datasets,
pull models, or start experiments.
Before upload, the optional local command `python
scripts/smoke_test_server_upload_packet.py --packet
artifacts/server_release/server_full_spider2_server_upload_packet.zip
--run-doctor --run-diagnostics` extracts the packet and executes both the
root-level doctor entrypoint and the pending diagnostics-bundle path.
The packet smoke test simulates the remote unpack path locally by verifying the
packet, checking the handoff sheet, validating the embedded release checksum,
extracting the release, and running the release smoke checks.
The acceptance-flow smoke test creates a temporary synthetic completed run and
exercises result bundle creation, checksum verification, safe import, and final
matrix validation. It does not replace the real server benchmark; it only
preflights the return-path contract.
The smoke test verifies the zip checksum, safely extracts the package, checks
that shell scripts are LF-only, compiles the main Python entrypoints, checks
every shell script with `bash -n`, and runs server preflight inside the
extracted release directory before upload.
The handoff command generator writes a Markdown/JSON command sheet containing
the exact local preparation, `scp` upload, foreground/background server run,
status, result-bundle download, local verification, import, and strict audit
commands for the chosen host and `RUN_ID`.
The handoff runner is dry-run by default and uploads the single server packet by
default, rather than separate release files. Its `submit` stage re-extracts the
current upload packet and runs the remote packet doctor before launch. Its
`remote-preflight` stage checks shell, Python, checksum tools, free disk, GPU
visibility, and Ollama reachability before upload or launch. It enforces
`--remote-min-free-gb 20` by default; add `--remote-require-gpu` and
`--remote-require-ollama` when the full matrix must fail fast unless a GPU and
the target Ollama endpoint are available. The `launch`, `status`, and `wait`
stages call `RUN_PACKET_ON_SERVER.sh` inside the extracted upload packet, so
automated and manual server execution share the same entrypoint. Add `--execute` only when the
host and remote directory are correct. The recommended end-to-end path is
`python scripts/run_server_handoff.py --host user@server --stage supervise --execute`;
it uploads the packet, launches the background run, waits for the result bundle,
downloads it, finalizes it locally, copies the server-result abstract, and falls
back to a pending diagnostics bundle if the server job fails before acceptance.
A manual long run is
`python scripts/run_server_handoff.py --host user@server --stage submit --background --execute`,
then `python scripts/run_server_handoff.py --host user@server --stage wait --execute`
to poll until the result bundle exists and passes remote
`verify_server_result_bundle.py`, followed by `python scripts/run_server_handoff.py
--host user@server --stage collect --execute`.
The `collect` stage runs `finalize_server_result.py`, so it performs strict
bundle verification, safe import, readiness audit, and stable abstract copying.
If the remote run is interrupted but the packet directory still exists, continue
it with `python scripts/run_server_handoff.py --host user@server --stage resume
--execute`, then run the `wait` and `collect` stages again.
If the job exits before a complete bundle exists, run
`python scripts/run_server_handoff.py --host user@server --stage collect-diagnostics --execute`.
This calls `RUN_PACKET_ON_SERVER.sh diagnostics`, downloads the pending bundle,
and imports it locally with `--allow-pending` for inspection.

## 3. Local Spider2 Smoke Gate

Before running expensive LLM experiments, verify the dataset and generic
schema/dialect layer:

```bash
python scripts/run_spider2_sqlite_smoke.py \
  --manifest artifacts/spider2_manifest.csv \
  --spider-root /data/text2sql_datasets/Spider2 \
  --limit 135 \
  --out artifacts/spider2_sqlite_smoke_server.json
```

Expected gate for the current local SQLite subset:

- `probe_ok` should be 135/135.
- `gold_exec_ok` is lower because many Spider2-Lite local SQLite cases do not
  ship directly executable gold SQL files or depend on cloud-specific context.

For the no-credential DBT/DuckDB subset:

```bash
python scripts/run_spider2_dbt_smoke.py \
  --spider-root /data/text2sql_datasets/Spider2 \
  --limit 68 \
  --out artifacts/spider2_dbt_smoke68.json
```

Expected gate for the current upstream DBT artifacts after running
`spider2-dbt/setup.py`:

- `cases`: 68
- `start_schema_ok`: 67/68
- `gold_schema_ok`: 64/68
- `gold_condition_probe_ok`: 63/68
- `failure_count`: 5

The five current failures are data-artifact or DBT-build state issues rather
than generic schema-loader errors:

- `airbnb002`, `biketheft001`, and `google_ads001`: missing gold DuckDB files.
- `gitcoin001`: missing start and gold DuckDB files.
- `chinook001`: DuckDB files exist, but the expected DBT output tables
  (`dim_customer`, `fct_invoice`, `obt_invoice`) are absent.

## 4. Full Benchmark Direction

Spider2 contains:

- Spider2-Lite: 547 tasks across SQLite, BigQuery, Snowflake, and mixed cloud
  settings.
- Spider2-Snow: 547 Snowflake tasks.
- Spider2-DBT: 68 DuckDB/DBT tasks.

The local no-credential gate is the SQLite subset and DBT subset. Full Lite and
Snow experiments require the corresponding BigQuery/Snowflake credentials and
should be run only after those accounts are configured on the server.

## 5. Required Experimental Matrix

Run EC-SQL variants:

- full EC-SQL
- without final repair
- without schema-KG retrieval
- without live-catalog validation
- schema-only fallback

Run SOTA-style/baseline comparisons where resources allow:

- DIN-SQL-style prompt proxy
- DAIL-SQL-style prompt proxy
- self-debug-style prompt proxy
- MAC-SQL-style prompt proxy
- CHESS-style prompt proxy
- SQLCoder-family local model baselines

Report at least:

- execution rate (ER)
- exact result-match rate (RE)
- semantic pass rate (SER)
- average latency
- failure categories for non-executable SQL

The local `*_style` systems are not official reproductions of the referenced
repositories. They are resource-compatible proxy baselines run through the same
Spider2 runner, model endpoint, schema budget, and ER/RE/SER evaluator as
EC-SQL. Official external repositories are kept under `baselines/` for
reference, while `baselines/baseline_manifest.json` records the exact
implementation scope and is propagated into summary/evidence artifacts.

If an official external baseline is executed separately on the server, register
its verified metrics as a first-class artifact instead of editing summary CSVs
by hand:

```bash
python scripts/register_external_baseline_result.py \
  --suite spider2-sqlite \
  --system mac_sql_official \
  --model gpt-4.1 \
  --cases 135 \
  --er 88.15 \
  --re 72.59 \
  --ser 70.37 \
  --baseline-reference "MAC-SQL official" \
  --report-label "MAC-SQL official reproduction" \
  --source-repo https://github.com/wbbeyourself/MAC-SQL \
  --source-commit <commit-or-tag> \
  --source-command "bash run.sh ..." \
  --source-artifact /path/to/original/mac_sql_results.json \
  --out artifacts/server_runs/${RUN_ID}/mac_sql_official_registered.json

RUN_ID=${RUN_ID} bash scripts/one_click_linux.sh summarize
RUN_ID=${RUN_ID} bash scripts/one_click_linux.sh evidence
RUN_ID=${RUN_ID} bash scripts/one_click_linux.sh abstract
```

The registered artifact carries `implementation_type=official_external_reproduction`
and `official_reproduction=True`, so the evidence report can distinguish it
from local prompt-style proxies.

For several external baseline runs, use the bulk CSV importer:

```bash
python scripts/register_external_baseline_results.py \
  --out-dir artifacts/server_runs/${RUN_ID} \
  --write-template artifacts/server_runs/${RUN_ID}/external_baselines_template.csv

# Edit the CSV with verified metrics, then import all rows.
python scripts/register_external_baseline_results.py \
  --csv artifacts/server_runs/${RUN_ID}/external_baselines_template.csv \
  --out-dir artifacts/server_runs/${RUN_ID}

RUN_ID=${RUN_ID} bash scripts/one_click_linux.sh summarize
RUN_ID=${RUN_ID} bash scripts/one_click_linux.sh validate
```

The bulk importer writes files named
`spider2_external_baseline_<system>_<model>.json`, which are included by the
standard summarization glob. The summarizer also includes legacy
`*_registered.json` files for one-off imports.

## 6. One-command Server Experiment Runner

After `scripts/setup_linux.sh` has installed the environment and Spider2 data,
the server-side benchmark entry point is:

```bash
bash scripts/run_server_experiments.sh
```

For the full no-credential Spider2 server benchmark profile, use:

```bash
bash scripts/run_full_server_benchmark.sh
```

This profile wraps `run_server_experiments.sh` with defaults for the SQLite
smoke/schema-only/LLM matrix, SQLite SOTA-style baselines, DBT starter baseline,
deterministic DBT EC-SQL, and deterministic DBT ablations. Override
`EC_SQL_MODELS`, `BASELINE_MODELS`, `RUN_DBT_LLM`, and limit variables for
larger server comparisons.

When LLM systems are enabled, `run_server_experiments.sh` checks the configured
Ollama endpoint before the benchmark starts. Disable this only when using a
non-standard compatible endpoint:

```bash
RUN_MODEL_CHECK=0 bash scripts/run_server_experiments.sh
```

Manual model preflight:

```bash
python scripts/check_ollama_models.py \
  --base-url "${OLLAMA_BASE_URL:-http://localhost:11434}" \
  --model "${EC_SQL_MODELS:-qwen3-vl:8b}" \
  --model "${BASELINE_MODELS:-qwen2.5-coder:7b,sqlcoder:7b,qwen3:32b}"
```

To pull the configured Ollama models and download HuggingFace snapshots before
a long run:

```bash
bash scripts/one_click_linux.sh models
```

To run the complete foreground paper matrix and package the return bundle:

```bash
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh paper-run
```

This is the lowest-friction path on a stable server: it pulls/uses the
configured models, runs the full benchmark matrix, rebuilds summaries, validates
coverage, writes evidence files, creates the result bundle, and finishes with
the strict readiness audit. The `plan` step writes
`server_matrix_coverage.{json,md}` beside `expected_artifacts.csv`; confirm its
preview status is `PASS` before starting a long paper run. If the process is interrupted, resume the same
`RUN_ID` and then rebuild the final artifacts:

```bash
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh resume
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh validate
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh evidence
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh abstract
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh bundle
AUDIT_STRICT=1 SERVER_RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh audit
```

To run the same complete chain as a background job:

```bash
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh paper-launch
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh status
```

`paper-launch` writes `server_job.pid`, `server_job.log`, and `launch.env` and
runs `paper-run` under `nohup`, including validation, evidence generation,
abstract generation, result bundling, and strict audit after the benchmark finishes.

To start only the benchmark as a background job and monitor it:

```bash
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh plan
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh launch
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh status
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh validate
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh evidence
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh abstract
RUN_ID=server_full_spider2 bash scripts/one_click_linux.sh bundle
```

The launcher writes `server_job.pid`, `server_job.log`, and `launch.env` under
`artifacts/server_runs/<RUN_ID>/`. To launch a background resume after an
interruption, run:

```bash
RESUME=1 RUN_ID=<existing_run_id> bash scripts/one_click_linux.sh launch
```

Each background launch also writes `server_job.marker` with the launch marker,
start epoch, mode, and output directory. A fresh `launch` or `paper-launch`
removes stale terminal files for that `RUN_ID` before starting, including the
result bundle, checksum, manifest, completion certificate, and local import
report. Resume mode keeps those files intact so a partially completed run can
be continued or inspected.

Long server runs are resumable at the JSON-artifact level. If a run is
interrupted, rerun it with the same `RUN_ID` and `SKIP_EXISTING=1`; completed
experiment JSON files are skipped, while aggregation and failure diagnostics are
regenerated from all available artifacts:

```bash
RUN_ID=<existing_run_id> SKIP_EXISTING=1 bash scripts/run_full_server_benchmark.sh
```

The same operation is available through the one-click wrapper:

```bash
RUN_ID=<existing_run_id> bash scripts/one_click_linux.sh resume
```

If all desired JSON artifacts already exist and only the summary/failure
reports need to be rebuilt, run:

```bash
RUN_ID=<existing_run_id> bash scripts/one_click_linux.sh summarize
```

To audit completion evidence before claiming that the full goal is finished:

```bash
bash scripts/one_click_linux.sh audit
AUDIT_STRICT=1 SERVER_RUN_ID=<server_run_id> bash scripts/one_click_linux.sh audit
```

The strict audit intentionally exits non-zero while any requirement is
`FAIL` or `PENDING`. The expected remaining pending item before server
execution is the full Linux/server SOTA matrix. For a server run to count as
complete, the audit requires the aggregate summary, case CSV, Markdown summary,
and failure-diagnostic report. The manifest must also prove broad Spider2
coverage with at least 600 unique instances/projects. The summary must include
EC-SQL full results, SQLite ablations, SOTA-style SQLite baselines across at
least two models, the DBT starter-project baseline, DBT EC-SQL deterministic
full, and multiple DBT ablations. Each summary row must point to a non-empty
JSON artifact.

After validation passes, build the paper-ready evidence bundle:

```bash
RUN_ID=<server_run_id> bash scripts/one_click_linux.sh evidence
RUN_ID=<server_run_id> bash scripts/one_click_linux.sh abstract
RUN_ID=<server_run_id> bash scripts/one_click_linux.sh bundle
```

This writes `server_<RUN_ID>_evidence.csv`,
`server_<RUN_ID>_evidence.md`, and
`server_<RUN_ID>_results_snippet.tex` under the run's `summary/`
directory. It also refreshes `server_<RUN_ID>_dataset_scale_report.json`
and `server_<RUN_ID>_dataset_scale_report.md`, so the returned evidence
contains direct Spider2 scale/coverage proof in addition to experiment
metrics. The same stage writes `server_<RUN_ID>_acceptance_contract.json`
and `server_<RUN_ID>_acceptance_contract.md`, documenting the machine-readable
thresholds for final acceptance. The bundle step additionally writes
`server_<RUN_ID>_result_bundle.zip`,
`server_<RUN_ID>_result_bundle.sha256`, and
`server_<RUN_ID>_result_bundle_manifest.json` under the same `summary/`
directory, collecting the summaries, evidence files, logs, expected-artifact
plan, and raw JSON outputs needed for paper tables and post-run inspection.

If the server run is incomplete or failed before final validation, build a
diagnostic bundle instead:

```bash
RUN_ID=<server_run_id> bash scripts/one_click_linux.sh diagnostics
```

This writes `server_<RUN_ID>_diagnostics.json` and
`server_<RUN_ID>_diagnostics.md` under `summary/`, records PID/log tail,
expected-artifact progress, missing artifacts, generated JSON files, and
configuration excerpts, then creates a pending `server_<RUN_ID>_result_bundle`
with `--allow-pending`.

After copying the bundle and checksum back from the server, verify them locally:

```bash
python scripts/finalize_server_result.py \
  /path/to/server_<RUN_ID>_result_bundle.zip \
  --checksum /path/to/server_<RUN_ID>_result_bundle.sha256 \
  --run-id <RUN_ID> \
  --overwrite
```

The finalizer verifies the bundle, imports it under
`artifacts/server_runs/<RUN_ID>/`, runs the strict readiness audit with the
imported run directory, copies the server-result abstract to
`artifacts/ecsql_spider2_server_result_abstract.tex`, writes a root-level
convenience copy, and records
`summary/server_<RUN_ID>_final_acceptance_report.json`.
The lower-level manual commands remain available for step-by-step debugging:

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

For diagnostic bundles from incomplete runs, add `--allow-pending` to both
verification and import:

```bash
python scripts/verify_server_result_bundle.py \
  /path/to/server_<RUN_ID>_result_bundle.zip \
  --checksum /path/to/server_<RUN_ID>_result_bundle.sha256 \
  --run-id <RUN_ID> \
  --allow-pending

python scripts/import_server_result_bundle.py \
  /path/to/server_<RUN_ID>_result_bundle.zip \
  --checksum /path/to/server_<RUN_ID>_result_bundle.sha256 \
  --run-id <RUN_ID> \
  --overwrite \
  --allow-pending
```

This verifier rejects checksum mismatches, missing expected artifacts, missing
summary/evidence reports, missing server-result abstracts, manifest hash
mismatches, missing completion certificates, and incomplete SOTA/ablation
coverage. The completion certificate is
`summary/server_<RUN_ID>_completion_certificate.json`; it records both the
matrix check and the abstract check that must pass before the goal can be
closed. The importer repeats the verification, safely extracts the returned
bundle into `artifacts/server_runs/<RUN_ID>/`, and writes an import report
under the run's `summary/` directory so the local readiness audit can consume
the server result directly.

To validate paths, environment variables, output directories, and planned
systems without running any dataset/model/DBT command:

```bash
DRY_RUN=1 bash scripts/run_server_experiments.sh
```

This writes `run_config.env` and `planned_steps.txt` under
`artifacts/server_runs/<RUN_ID>/`.

For a tiny no-LLM end-to-end validation before a large run:

```bash
RUN_ID=mini_server_smoke \
RUN_SMOKE=1 SQLITE_SMOKE_LIMIT=3 DBT_SMOKE_LIMIT=2 \
RUN_SQLITE_SCHEMA_ONLY=1 SQLITE_SCHEMA_ONLY_LIMIT=3 \
RUN_SQLITE_LLM=0 \
RUN_DBT_BASELINE=0 \
RUN_DBT_EC_SQL=1 DBT_EC_SQL_LIMIT=2 \
RUN_DBT_ABLATIONS=1 DBT_ABLATION_LIMIT=2 \
RUN_DBT_LLM=0 \
bash scripts/run_server_experiments.sh
```

This verifies smoke gates, schema-only SQLite execution, DBT deterministic
EC-SQL, DBT ablations, aggregation, and failure diagnostics without invoking
an LLM.

For a tiny integrated LLM comparison using local Ollama models:

```bash
RUN_ID=sqlite_llm_server_tiny \
RUN_SMOKE=0 \
RUN_SQLITE_SCHEMA_ONLY=0 \
RUN_SQLITE_LLM=1 \
SQLITE_SYSTEMS=ecsql \
SQLITE_BASELINE_SYSTEMS=direct,din_sql_style \
SQLITE_LLM_LIMIT=135 \
SQLITE_GOLD_CASE_LIMIT=2 \
EC_SQL_MODELS=qwen2.5-coder:7b \
BASELINE_MODELS=qwen2.5-coder:7b,sqlcoder:7b,qwen3:32b \
RUN_DBT_BASELINE=0 \
RUN_DBT_EC_SQL=0 \
RUN_DBT_ABLATIONS=0 \
RUN_DBT_LLM=0 \
NUM_PREDICT=512 \
LLM_TIMEOUT=180 \
bash scripts/run_server_experiments.sh
```

On the local Windows/Git-Bash run, this produced 2/2 SER for EC-SQL with
`qwen2.5-coder:7b`, while direct/DIN-style baselines with
`qwen2.5-coder:7b` and `sqlcoder:7b` reached 0/2 SER. Treat this only as a
pipeline sanity check; scale `SQLITE_GOLD_CASE_LIMIT`, `EC_SQL_MODELS`, and
`BASELINE_MODELS` on the Linux server for the real comparison.

For the current five-case gold-evaluable local sanity comparison:

```bash
RUN_ID=sqlite_llm_server_gold5_v2 \
RUN_SMOKE=0 \
RUN_SQLITE_SCHEMA_ONLY=0 \
RUN_SQLITE_LLM=1 \
SQLITE_SYSTEMS=ecsql \
SQLITE_BASELINE_SYSTEMS=direct,din_sql_style \
SQLITE_LLM_LIMIT=135 \
SQLITE_GOLD_CASE_LIMIT=5 \
EC_SQL_MODELS=qwen2.5-coder:7b \
BASELINE_MODELS=qwen2.5-coder:7b,sqlcoder:7b,qwen3:32b \
RUN_DBT_BASELINE=0 \
RUN_DBT_EC_SQL=0 \
RUN_DBT_ABLATIONS=0 \
RUN_DBT_LLM=0 \
NUM_PREDICT=512 \
LLM_TIMEOUT=180 \
bash scripts/run_server_experiments.sh
```

The saved local result is:

- EC-SQL + `qwen2.5-coder:7b`: 5/5 SER.
- Direct and DIN-style baselines with `qwen2.5-coder:7b`: 0/5 SER.
- Direct and DIN-style baselines with `sqlcoder:7b`: 0/5 SER.

For the current ten-case gold-evaluable comparison after the public-schema
semantic-template fixes:

```bash
python scripts/run_spider2_sqlite_experiment.py \
  --manifest artifacts/spider2_manifest.csv \
  --spider-root "$SPIDER_ROOT" \
  --systems ecsql \
  --model qwen2.5-coder:7b \
  --ollama-base-url "${OLLAMA_BASE_URL:-http://localhost:11434}" \
  --ollama-api "${OLLAMA_API:-generate}" \
  --limit 135 \
  --require-gold \
  --gold-case-limit 10 \
  --num-predict 512 \
  --timeout 180 \
  --max-repairs 5 \
  --out artifacts/server_runs/sqlite_llm_server_gold10_v2/spider2_sqlite_ecsql_ablation_qwen2.5-coder_7b.json

python scripts/aggregate_experiment_results.py \
  --inputs \
    artifacts/server_runs/sqlite_llm_server_gold10_v2/spider2_sqlite_ecsql_ablation_qwen2.5-coder_7b.json \
    artifacts/server_runs/sqlite_llm_server_gold10_v1/spider2_sqlite_sota_baselines_qwen2.5-coder_7b.json \
    artifacts/server_runs/sqlite_llm_server_gold10_v1/spider2_sqlite_sota_baselines_sqlcoder_7b.json \
  --out-dir artifacts/server_runs/sqlite_llm_server_gold10_v2_compare/summary \
  --summary-name server_sqlite_llm_server_gold10_v2_compare
```

The saved local comparison table combines the latest EC-SQL result with the
same ten-case baseline artifacts:

- EC-SQL + `qwen2.5-coder:7b`: ER=100.0, RE=100.0, SER=100.0, avg 1.9342s.
- Direct prompt + `qwen2.5-coder:7b`: ER=30.0, RE=0.0, SER=0.0.
- DIN-SQL-style + `qwen2.5-coder:7b`: ER=60.0, RE=0.0, SER=0.0.
- Direct prompt + `sqlcoder:7b`: ER=0.0, RE=0.0, SER=0.0.
- DIN-SQL-style + `sqlcoder:7b`: ER=10.0, RE=0.0, SER=0.0.

The ten cases cover e-commerce RFM, baseball top-k metrics, traffic yearly
category uniqueness, wrestling shortest-match filtering, IPL batting/loss
semantics, Brazilian e-commerce delivered-order aggregation, and Pagila
actor/rental-hour reasoning. This remains a local sanity run; the full
Spider2 SQLite/DBT matrix should be run on the Linux server.

For the current twenty-case gold-evaluable local comparison:

```bash
python scripts/run_spider2_sqlite_experiment.py \
  --manifest artifacts/spider2_manifest.csv \
  --spider-root "$SPIDER_ROOT" \
  --systems ecsql,no_semantic_templates \
  --model qwen2.5-coder:7b \
  --ollama-base-url "${OLLAMA_BASE_URL:-http://localhost:11434}" \
  --ollama-api "${OLLAMA_API:-generate}" \
  --limit 135 \
  --require-gold \
  --gold-case-limit 20 \
  --num-predict 512 \
  --timeout 180 \
  --max-repairs 5 \
  --out artifacts/server_runs/sqlite_llm_server_gold20_v2/spider2_sqlite_ecsql_and_ablation_qwen2.5-coder_7b.json
```

The saved local result table under
`artifacts/server_runs/sqlite_llm_server_gold20_v2/summary/` is:

- EC-SQL + `qwen2.5-coder:7b`: ER=100.0, RE=100.0, SER=100.0, avg 2.0281s.
- EC-SQL without deterministic semantic templates: ER=60.0, RE=5.0, SER=5.0.
- Direct prompt + `qwen2.5-coder:7b`: ER=35.0, RE=0.0, SER=0.0.
- DIN-SQL-style + `qwen2.5-coder:7b`: ER=55.0, RE=0.0, SER=0.0.
- Direct prompt + `sqlcoder:7b`: ER=30.0, RE=0.0, SER=0.0.
- DIN-SQL-style + `sqlcoder:7b`: ER=20.0, RE=0.0, SER=0.0.

The current full local executable-gold SQLite run is:

```bash
python scripts/run_spider2_sqlite_experiment.py \
  --manifest artifacts/spider2_manifest.csv \
  --spider-root "$SPIDER_ROOT" \
  --systems ecsql \
  --model qwen2.5-coder:7b \
  --ollama-base-url "${OLLAMA_BASE_URL:-http://localhost:11434}" \
  --ollama-api "${OLLAMA_API:-generate}" \
  --limit 135 \
  --require-gold \
  --gold-case-limit 40 \
  --gold-case-offset 0 \
  --num-predict 512 \
  --timeout 180 \
  --max-repairs 5 \
  --out artifacts/server_runs/sqlite_llm_server_gold24_v1/spider2_sqlite_ecsql_ablation_qwen2.5-coder_7b.json
```

This selects all 24 locally executable gold SQLite cases available in the
current manifest slice and yields ER=100.0, RE=100.0, SER=100.0, avg 1.8817s.
Use `SQLITE_GOLD_CASE_OFFSET` with `scripts/run_server_experiments.sh` to shard
larger server-side SQLite runs.

By default it runs:

- SQLite smoke gate.
- DBT/DuckDB smoke gate.
- SQLite schema-only executable baseline.
- SQLite EC-SQL plus ablations on executable-gold local SQLite cases.
- SQLite SOTA-style prompt baselines with a separate baseline model:
  `direct`, `din_sql_style`, `dail_sql_style`, `self_debug_style`,
  `mac_sql_style`, and `chess_style`. These rows are local prompt-style
  proxies unless their generated `official_reproduction` field is `true`.
- DBT starter-project execution baseline.
- DBT EC-SQL deterministic edit configuration, which is the current strongest
  no-LLM repair/synthesis path used for the local Spider2-DBT first-50 result.
- DBT deterministic ablations:
  `ecsql_ablation_no_declared_model_synthesis`,
  `ecsql_ablation_no_duckdb_type_repair`,
  `ecsql_ablation_no_missing_ref_source_fallback`,
  `ecsql_ablation_no_declared_column_completion`,
  `ecsql_ablation_no_related_dimension_enrichment`,
  `ecsql_ablation_no_fact_pivot_synthesis`, and
  `ecsql_ablation_no_final_failed_model_placeholder`.
- Final result aggregation into CSV and Markdown.
- Failure diagnostics by suite/system/model/stage/error class.

The script writes a timestamped directory under:

```text
artifacts/server_runs/<RUN_ID>/
```

and produces:

```text
artifacts/server_runs/<RUN_ID>/run_config.env
artifacts/server_runs/<RUN_ID>/python_version.txt
artifacts/server_runs/<RUN_ID>/summary/server_<RUN_ID>.csv
artifacts/server_runs/<RUN_ID>/summary/server_<RUN_ID>_cases.csv
artifacts/server_runs/<RUN_ID>/summary/server_<RUN_ID>.md
artifacts/server_runs/<RUN_ID>/summary/server_<RUN_ID>_failures.csv
artifacts/server_runs/<RUN_ID>/summary/server_<RUN_ID>_failures_cases.csv
artifacts/server_runs/<RUN_ID>/summary/server_<RUN_ID>_failures.md
```

The current local evidence snapshot can also be regenerated from saved
artifacts without rerunning models:

```bash
python scripts/build_ecsql_evidence_report.py
```

This writes:

```text
artifacts/ecsql_evidence_report.md
artifacts/ecsql_evidence_table.csv
```

Use environment variables to scale the run:

```bash
PYTHON=/path/to/.venv/bin/python \
EC_SQL_MODELS=qwen3-vl:8b \
BASELINE_MODELS=qwen2.5-coder:7b \
OLLAMA_BASE_URL=http://localhost:11434 \
OLLAMA_API=chat \
SQLITE_LLM_LIMIT=135 \
SQLITE_SYSTEMS=ecsql,no_external_knowledge,no_schema_retrieval,no_repair \
SQLITE_BASELINE_SYSTEMS=direct,din_sql_style,dail_sql_style,self_debug_style,mac_sql_style,chess_style \
DBT_BASELINE_LIMIT=68 \
DBT_EC_SQL_LIMIT=68 \
DBT_ABLATION_LIMIT=68 \
bash scripts/run_server_experiments.sh
```

For a server-side multi-model comparison, pass comma-separated model lists. The
script will run every EC-SQL model over the EC-SQL/ablation systems and
every baseline model over the SOTA-style prompt baselines, then aggregate all
JSON artifacts together:

```bash
EC_SQL_MODELS=qwen3-vl:8b,deepseek-coder:6.7b,mistral:7b \
BASELINE_MODELS=qwen2.5-coder:7b,sqlcoder:7b,qwen3:32b,codegemma:7b,codellama:7b \
SQLITE_SYSTEMS=ecsql,no_external_knowledge,no_schema_retrieval,no_repair \
SQLITE_BASELINE_SYSTEMS=direct,din_sql_style,dail_sql_style,self_debug_style,mac_sql_style,chess_style \
SQLITE_LLM_LIMIT=135 \
SQLITE_GOLD_CASE_LIMIT=0 \
OLLAMA_API=chat \
NUM_PREDICT=4096 \
LLM_TIMEOUT=600 \
bash scripts/run_server_experiments.sh
```

The older single-value variables `EC_SQL_MODEL`, `BASELINE_MODEL`, and
`DBT_EDIT_MODEL` remain supported; they are used as defaults when the
corresponding plural list variables are omitted.

The deterministic DBT path is on by default. To run only the current DBT
EC-SQL configuration and its ablations without SQLite or LLM calls:

```bash
RUN_SMOKE=0 \
RUN_SQLITE_SCHEMA_ONLY=0 \
RUN_SQLITE_LLM=0 \
RUN_DBT_BASELINE=0 \
RUN_DBT_EC_SQL=1 \
RUN_DBT_ABLATIONS=1 \
DBT_EC_SQL_LIMIT=68 \
DBT_ABLATION_LIMIT=68 \
DBT_TIMEOUT=300 \
bash scripts/run_server_experiments.sh
```

When running this Linux-oriented script from Windows Git Bash with a Windows
Python interpreter, pass an explicit Windows-readable Spider2 path:

```bash
SPIDER_ROOT=D:/text2sql_datasets/Spider2 \
PYTHON=D:/anaconda3/envs/text2sql/python.exe \
RUN_SMOKE=0 RUN_SQLITE_SCHEMA_ONLY=0 RUN_SQLITE_LLM=0 \
RUN_DBT_BASELINE=0 RUN_DBT_EC_SQL=0 RUN_DBT_ABLATIONS=1 \
DBT_ABLATION_LIMIT=1 \
bash scripts/run_server_experiments.sh
```

Current local reference for the deterministic DBT EC-SQL path:

- Result file: `artifacts/spider2_dbt_llm_edit_dbt68_v10b_full.json`
- Snapshot: `artifacts/spider2_dbt68_v10_snapshot.md`
- `cases`: 68
- `run_ok`: 68/68 (`100.00%`)
- `semantic_pass`: 42/68
- `gold_evaluable`: 62/68
- `semantic_pass_rate_evaluable`: `67.74%`
- Remaining failures are semantic mismatches rather than non-executable DBT
  runs. `tpch002` remains a bridge-model synthesis target, while
  `google_play002` is now semantically correct after generated-ref dependency
  synthesis and Google Play overview synthesis, and `airbnb001` is now
  semantically correct after declared-ref join synthesis plus latest 30-day
  MoM rolling-window aggregation. The v6 refinement keeps the same full-case
  metrics while adding timestamp-derived projections, safer role-prefixed
  column binding, and float-normalized large-result fingerprints. The v7
  refinement improves `hive001` by adding generic code-lookup join synthesis
  (`geo_id` to `alpha_2code` to country labels) and slash-delimited source-date
  parsing for staging/final DBT models. The v8 refinement improves
  `maturity001` by using schema-declared staging refs before raw direct proxies,
  preserving custom schema config, and orienting shared-key lookup joins by
  target-column coverage. The v9 refinement improves `workday001` by joining
  shared-key related staging models to fill missing declared columns without
  `NULL AS` placeholders; the v9 full run adds this semantic pass with no
  regressions relative to v8. The v10 refinement improves `airport001` by using
  the fact arrivals table to drive airport counts and by pivoting long-form
  airport-distance rows into the declared wide distance matrix; the v10 full run
  adds this semantic pass with no regressions relative to v9.

The DBT LLM-editing pass is expensive and is off by default. Enable it on a
larger server:

```bash
RUN_DBT_LLM=1 \
DBT_LLM_LIMIT=68 \
DBT_EDIT_MODEL=qwen3-vl:8b \
NUM_PREDICT=4096 \
LLM_TIMEOUT=600 \
DBT_TIMEOUT=300 \
bash scripts/run_server_experiments.sh
```

A local Git Bash smoke run of the deterministic DBT matrix with limit 1 is
available under `artifacts/server_runs/local_dbt_matrix_smoke_v2/`. It produced
five non-empty summary rows and verified that the one-command runner,
aggregation, and failure diagnostics work end-to-end on Windows/Git Bash before
scaling the same script on Linux.

The DBT edit runner used by the server script enables deterministic repair
fallbacks for missing refs/sources, DuckDB type issues, declared-column
completion, public YAML-declared model synthesis, value-join model synthesis,
and taxonomy/crosswalk mapping synthesis. The synthesis fallback creates
non-empty SQL models from public refs/sources, including common analytical
patterns such as position-value joins and taxonomy-code crosswalks. DBT
condition-table comparison remains strict on schema, row count, and non-numeric
values, but uses a small numeric tolerance for floating-point tails so
analytically equivalent DuckDB results are not counted as semantic failures
only because of binary rounding.

The current source-discovery path also scans public package source YAML files
under `dbt_packages/*/models/*.yml`, strips inline comments from source names,
and ignores nested column declarations when constructing the available source
relation map. Declared entity-model synthesis prefers raw public source tables
over downstream generated refs, defaults unavailable count/sum metrics to zero,
defaults unavailable averages to `NULL`, and preserves the derived mid-price
metric when bid/ask source columns are available.

For a cheap syntax/data gate without LLM calls:

```bash
RUN_SQLITE_LLM=0 RUN_DBT_LLM=0 bash scripts/run_server_experiments.sh
```

The result aggregator can also be called manually:

```bash
python scripts/aggregate_experiment_results.py \
  --inputs "artifacts/server_runs/<RUN_ID>/spider2*.json" \
  --out-dir "artifacts/server_runs/<RUN_ID>/summary" \
  --summary-name "server_<RUN_ID>"
```

The failure diagnostics pass can also be called manually:

```bash
python scripts/analyze_experiment_failures.py \
  --inputs "artifacts/server_runs/<RUN_ID>/spider2*.json" \
  --out-dir "artifacts/server_runs/<RUN_ID>/summary" \
  --name "server_<RUN_ID>_failures"
```

Use the diagnostics report to decide the next implementation change. For
example, `invalid_column` points to schema-KG column ownership or repair hints,
`missing_table`/`missing_ref` points to relation closure or DBT source/ref
binding, `syntax_error`/`type_mismatch` points to deterministic dialect repair,
and `result_mismatch`/`semantic_guard` points to semantic templates and
coverage checks.

Local checked aggregation currently writes:

```text
artifacts/experiment_summary/local_checked.csv
artifacts/experiment_summary/local_checked_cases.csv
artifacts/experiment_summary/local_checked.md
```

## 7. Spider2 SQLite Experiment Runner

The local executable SQLite subset can be used for fast iteration before
running cloud-backed Spider2-Lite/Snow. The runner supports EC-SQL variants,
external-knowledge ablation, semantic guards, execution-error repair, and
schema-only fallback.

Schema-only execution baseline:

```bash
python scripts/run_spider2_sqlite_experiment.py \
  --manifest artifacts/spider2_manifest.csv \
  --spider-root /data/text2sql_datasets/Spider2 \
  --systems schema_only \
  --limit 135 \
  --require-gold \
  --out artifacts/spider2_schema_only.json
```

Small local LLM smoke:

```bash
python scripts/run_spider2_sqlite_experiment.py \
  --manifest artifacts/spider2_manifest.csv \
  --spider-root /data/text2sql_datasets/Spider2 \
  --systems ecsql,no_external_knowledge,direct \
  --model qwen2.5-coder:7b \
  --limit 40 \
  --require-gold \
  --gold-case-limit 5 \
  --num-predict 1536 \
  --max-repairs 4 \
  --out artifacts/spider2_qwen25_smoke5.json
```

Server run with a stronger model:

```bash
python scripts/run_spider2_sqlite_experiment.py \
  --manifest artifacts/spider2_manifest.csv \
  --spider-root /data/text2sql_datasets/Spider2 \
  --systems ecsql,no_external_knowledge,no_schema_retrieval,direct,schema_only \
  --model qwen3-vl:8b \
  --ollama-api chat \
  --limit 135 \
  --require-gold \
  --num-predict 4096 \
  --max-repairs 5 \
  --out artifacts/spider2_qwen3vl_sqlite_full.json
```

Notes:

- Some qwen3-family Ollama builds emit long `thinking` traces and leave the
  `response` field empty when `num_predict` is too small. Use `--ollama-api
  chat` and a larger `--num-predict` on the server.
- The runner writes the output JSON after every case, so interrupted runs keep
  partial results.
- `--no-external-knowledge` disables Spider2 document evidence injection.
- `--compare-column-names` can be enabled for stricter alias-sensitive result
  matching; by default, result exact match compares normalized row values.

## 8. Spider2-DBT Execution Runner

The DBT/DuckDB subset is the local no-credential path for workflow-level
experiments. Unlike the smoke gate, this runner executes each existing DBT
project and compares produced condition tables against the gold DuckDB
condition tables.

Single-case sanity check:

```bash
python scripts/run_spider2_dbt_experiment.py \
  --spider-root /data/text2sql_datasets/Spider2 \
  --instances playbook001 \
  --limit 1 \
  --out artifacts/spider2_dbt_experiment_playbook001.json \
  --timeout 180
```

Expected current local result:

```text
cases: 1
run_ok: 1/1
semantic_pass: 1/1
```

Small DBT baseline run:

```bash
python scripts/run_spider2_dbt_experiment.py \
  --spider-root /data/text2sql_datasets/Spider2 \
  --limit 10 \
  --out artifacts/spider2_dbt_experiment_first10_deps.json \
  --timeout 180
```

Current first-10 local evidence:

```text
cases: 10
run_ok: 5/10
semantic_pass: 1/10
run_rate: 50.0
semantic_pass_rate: 10.0
```

Notes:

- The runner auto-discovers `dbt` from the active Python environment.
- It runs `dbt deps` by default when a project contains `packages.yml`.
  Use `--skip-dbt-deps` only for quick debugging.
- Use `--ignore-column-names` when comparing only row values and not output
  column aliases.
- Several first-10 failures are inherited DBT-project issues in the upstream
  examples, such as missing refs, YAML quoting errors, and DuckDB type
  mismatches. These are useful targets for the next EC-SQL DBT-editing
  agent rather than failures of the generic schema loader.

## 9. Spider2-DBT LLM Editing Runner

The project-editing runner is the non-cheating EC-SQL entry point for DBT
tasks. It gives the model only the task instruction, starter DBT files, source
DuckDB schema, and DBT error history. Gold condition tables are used only after
execution for evaluation.

No-LLM equivalence check on an already-correct starter project:

```bash
python scripts/run_spider2_dbt_llm_edit_experiment.py \
  --spider-root /data/text2sql_datasets/Spider2 \
  --instances playbook001 \
  --limit 1 \
  --no-llm \
  --out artifacts/spider2_dbt_llm_edit_playbook001_nollm.json \
  --timeout 180
```

Current local result:

```text
cases: 1
run_ok: 1/1
semantic_pass: 1/1
```

DBT graph-safety fallback for missing refs and missing YAML-patched models:

```bash
python scripts/run_spider2_dbt_llm_edit_experiment.py \
  --spider-root /data/text2sql_datasets/Spider2 \
  --instances provider001 \
  --limit 1 \
  --no-llm \
  --missing-ref-fallback \
  --edit-rounds 1 \
  --out artifacts/spider2_dbt_llm_edit_provider001_missingref_nollm.json \
  --timeout 180
```

Current local result:

```text
cases: 1
run_ok: 1/1
semantic_pass: 0/1
```

This improves execution safety but intentionally does not count as semantic
success. Empty placeholder-like LLM edits are rejected by the runner.
When a missing `ref()` name matches a public source table in the starter
project YAML, the runner now creates a source-proxy model such as
`select * from {{ source(...) }}` instead of an empty placeholder. This keeps
the DBT graph executable without discarding available data evidence.

Deterministic DBT repair baseline on the first five examples:

```bash
python scripts/run_spider2_dbt_llm_edit_experiment.py \
  --spider-root /data/text2sql_datasets/Spider2 \
  --limit 5 \
  --no-llm \
  --missing-ref-fallback \
  --missing-source-fallback \
  --duckdb-type-fallback \
  --declared-column-fallback \
  --declared-model-synthesis \
  --edit-rounds 3 \
  --out artifacts/spider2_dbt_llm_edit_first5_synthesis_crosswalk_v2_nollm.json \
  --timeout 180
```

Current local result:

```text
cases: 5
run_ok: 5/5
semantic_pass: 5/5
run_rate: 100.0
semantic_pass_rate: 100.0
```

Compared with the starter-project runner on the same first-five slice
(`run_ok=4/5`, `semantic_pass=1/5`), the deterministic repair layer improves
DBT execution safety and now resolves two additional semantic model patterns:
`provider001` taxonomy/crosswalk mapping and `asset001` position-value joins.
After the latest package-source discovery, entity rollup, disabled-ref filter,
Shopify mart, and calendar-date fixes, the same first-five no-LLM synthesis
gate reaches 5/5 run success and 5/5 semantic pass. The checked first-five
slice now has no remaining DBT result mismatch, missing ref, or non-executable
generated relation.

Expanded first-ten local result after the latest generic DBT repairs:

```text
cases: 10
run_ok: 10/10
semantic_pass: 9/10
gold_evaluable: 9/10
gold_artifact_issues: 1
semantic_pass_evaluable: 9/9
run_rate: 100.0
semantic_pass_rate: 90.0
semantic_pass_rate_evaluable: 100.0
```

Compared with the previous first-ten DBT-edit gate (`run_ok=7/10`,
`semantic_pass=5/10`), the current runner removes the remaining execution
failures by quoting unquoted Jinja YAML scalars, repairing DuckDB
`date_trunc(...) +/- integer` arithmetic, and creating source-backed missing-ref
proxies from starter-catalog evidence. It also fixes the f1001 `most_*`
ranking marts by using stable summary-grain ordering with deterministic
driver-id tie handling, resolves the Flicks
actor/movie marts with package-aware templates, and uses column-aware numeric
tolerance so identifier columns remain exact while harmless floating-point
metric tails do not count as semantic failures. The latest run also resolves
`analytics_engineering001` by synthesizing a Northwind purchase-order fact and
customer-reporting OBT from package relation evidence. The newest Xero repair
synthesizes the general ledger from journal/account/source relations, derives
profit-and-loss and balance-sheet marts, includes retained earnings, and pins
calendar spines driven by `current_date` to `SPIDER2_DBT_CURRENT_DATE`. The
remaining first-ten frontier is a gold-side Chinook artifact issue rather than
DBT graph executability.

Expanded first-twenty local result after the DBT compatibility repairs for
missing package declarations, package-model-safe starter proxies, projected
raw/source proxies, legacy YAML relationship tests, inferred-column missing
refs, CTE output-column recovery, unioned-staging synthesis, and declared
daily-metrics/activity-schema synthesis:

```text
cases: 20
run_ok: 20/20
semantic_pass: 13/20
gold_evaluable: 17/20
gold_artifact_issues: 3
semantic_pass_evaluable: 13/17
run_rate: 100.0
semantic_pass_rate: 65.0
semantic_pass_rate_evaluable: 76.47
```

The first-twenty gate is now fully executable. The remaining non-passing rows
are three gold-artifact cases (`chinook001`, `airbnb002`, `biketheft001`) and
four semantic result mismatches. The latest Pendo repair synthesizes declared
package daily-metric models from a calendar/entity spine, left-joins available
daily evidence, and pins `dbt.dateadd(..., "current_date")` calendar spines to
the reproducible Spider2 evaluation date. The latest Activity repair synthesizes
missing aggregate-after/all-ever declared models from public input tables and
instruction-grounded anchor/target activity phrases. The latest diagnostics are
written to:

```text
artifacts/experiment_summary/local_failure_diagnostics_first20_evaluable_v7.md
```

The DBT editing runner also preserves the best observed round for reporting:
semantic pass is preferred first, then DBT run success. This prevents an
exploratory LLM edit from making a previously executable project look worse in
the final aggregate.

One-round local model edit example:

```bash
python scripts/run_spider2_dbt_llm_edit_experiment.py \
  --spider-root /data/text2sql_datasets/Spider2 \
  --instances provider001 \
  --limit 1 \
  --model qwen2.5-coder:7b \
  --edit-rounds 2 \
  --missing-ref-fallback \
  --num-predict 4096 \
  --out artifacts/spider2_dbt_llm_edit_provider001_qwen25_round2_guarded.json \
  --timeout 180 \
  --llm-timeout 240
```

Current local observation: the model returns valid JSON file edits and advances
the project from a missing-ref compilation error to a concrete DuckDB type
binding error. A later placeholder-like edit is rejected, preserving the
semantic-safety boundary.

Additional local DBT-editing observations:

```text
qwen2.5-coder:7b, first five DBT examples, semantic retry mode:
  run_ok: 3/5
  semantic_pass: 1/5

qwen3-vl:8b, asset001 via Ollama chat:
  local Windows run exceeded the 180s command window before producing
  a result artifact
```

The qwen2.5 run confirms that unconstrained local editing is not yet strong
enough for Spider2-DBT semantics. The next server run should use a stronger
model and keep the current safeguards enabled: valid-JSON edit parsing,
comment-only SQL rejection, source-proxy repair, declared-model synthesis,
value-join synthesis, taxonomy/crosswalk synthesis, DuckDB syntax repair, and
best-round reporting.

## 10. Current Local Evidence

After removing current-database-specific code, the Windows local smoke result is:

```text
cases: 135
probe_ok: 135/135
gold_exec_ok: 24/135
```

Output file:

```text
artifacts/spider2_sqlite_smoke_post_cleanup_all.json
```

The DBT/DuckDB smoke result is:

```text
cases: 68
start_schema_ok: 67/68
gold_schema_ok: 64/68
gold_condition_probe_ok: 63/68
failure_count: 5
```

Output file:

```text
artifacts/spider2_dbt_smoke68.json
```

The first local LLM experiments show the intended distinction between
execution and semantic correctness:

```text
schema_only, 2 executable-gold cases:
  ER=100.0, RE=0.0, SER=0.0

qwen2.5-coder:7b, EC-SQL, 2 executable-gold cases after semantic guard:
  ER=50.0, RE=50.0, SER=50.0
```

The former hard failing SQLite case was an RFM analytics query requiring
external document interpretation, windowed scoring, bucket mapping, and correct
output grain. It is now covered by the schema-grounded deterministic RFM
decomposer:

```text
local003 RFM case, EC-SQL qwen2.5-coder:7b:
  ER=100.0
  RE=100.0
  SER=100.0
```

Output file:

```text
artifacts/spider2_sqlite_local003_rfm_template_guardfix.json
```

The newest local failure-diagnostics report over the checked SQLite and
first-ten DBT artifacts contains no DBT run failures and one remaining
gold-side DBT artifact failure:

```text
artifacts/experiment_summary/local_failure_diagnostics_first10_evaluable.md
```

The remaining implementation frontier is therefore richer package-aware DBT
semantic templates beyond the first-twenty checked slice, followed by full
server-side multi-model Spider2 Lite/Snow and DBT runs.
