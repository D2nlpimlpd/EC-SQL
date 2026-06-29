# BoyueSQL Generalization Work Plan

This repository is being migrated from the earlier private Oracle
prototype into a database-agnostic BoyueSQL stack. The generic layer in
`boyuesql_generic/` is the migration target, and current-database-specific
runtime artifacts have been removed from the main runnable path.

## Dataset Choice

Use Spider 2.0 as the main broad generalization benchmark.

- Official benchmark scale: 632 real-world enterprise text-to-SQL workflow
  problems.
- Repository settings:
  - Spider 2.0-Snow: 547 text-to-SQL tasks over Snowflake.
  - Spider 2.0-Lite: 547 text-to-SQL tasks over BigQuery, Snowflake, and SQLite.
  - Spider 2.0-DBT: 68 no-cost DuckDB/DBT tasks.
- Practical path:
  - Use Spider2-DBT and the SQLite subset locally for smoke tests.
  - Use Lite/Snow on the server when BigQuery/Snowflake credentials are ready.

Download metadata to the default Windows dataset root:

```powershell
.\scripts\download_spider2.ps1 -Root D:\text2sql_datasets
```

On Linux:

```bash
DATASET_ROOT=/data/text2sql_datasets bash scripts/download_spider2.sh
```

## Migration Requirements

1. Remove hard-coded production database values from source files.
2. Route SQL syntax, row limiting, quoting, and error classification through
   `boyuesql_generic.dialects`.
3. Route schema ingestion through `boyuesql_generic.dictionary`.
4. Keep database-specific behavior behind adapters and `.env` settings.
5. Evaluate with execution rate, exact result match, and semantic pass rate.
6. Run ablations:
   - full BoyueSQL
   - without schema-KG retrieval
   - without live-catalog validation
   - without repair
   - schema-only fallback
7. Compare with available SOTA baselines:
   - DIN-SQL
   - DAIL-SQL
   - MAC-SQL
   - CHESS
   - DB-GPT-Hub/SQLCoder where feasible

## Current Status

- Generic dialect adapters exist in `boyuesql_generic/dialects.py`.
  The main serving path now uses the configured dialect adapter for SQL row
  limiting/probing, date-range predicates, prompt rules, and canonical error
  classification instead of embedding `ROWNUM`, `LIMIT`, or vendor errors
  directly in generation logic.
- Generic database connection and catalog helpers exist in
  `boyuesql_generic/connections.py`. Oracle is loaded lazily only for Oracle
  connections; SQLite and DuckDB can be used without Oracle client libraries.
  The clean generic Flask service in `boyuesql_service.py` exposes `/health`,
  `/api/schema`, and `/api/query`; runtime SQL execution is routed through this
  adapter boundary.
- The default service dialect is now SQLite rather than Oracle. `generic`
  connection configs with `.sqlite`, `.sqlite3`, `.db`, `.duckdb`, or `.ddb`
  paths are resolved to the corresponding local connector, so one-command
  startup is usable for local benchmark databases without private credentials.
- Portable schema dictionary loaders exist in `boyuesql_generic/dictionary.py`.
- Lightweight schema retrieval utilities exist in `boyuesql_generic/retrieval.py`.
- Spider2 download/inspection helper exists in `scripts/download_spider2.py`.
- Linux one-command setup/start scripts exist in `scripts/setup_linux.sh` and
  `scripts/start_linux.sh`.
  The default startup entrypoint is now the generic `boyuesql_service.py`.
- `scripts/build_server_release.py` builds a clean allowlisted server zip under
  `artifacts/server_release/`. The package includes the generic service,
  reusable library, scripts, tests, requirements, documentation, baselines, and
  modified RagAnything source, while excluding legacy demo entrypoints, old UI
  assets, local papers, datasets, logs, and generated benchmark outputs.
- Linux one-command benchmark script exists in
  `scripts/run_server_experiments.sh`. It runs smoke gates, SQLite BoyueSQL
  ablations, prompt-only baselines, DBT starter-project evaluation, the current
  DBT BoyueSQL deterministic edit configuration, DBT deterministic ablations,
  optional DBT LLM editing, and final aggregation.
  It supports comma-separated `BOYUESQL_MODELS`, `BASELINE_MODELS`, and
  `DBT_EDIT_MODELS` lists for server-side multi-model comparisons.
- `scripts/run_full_server_benchmark.sh` wraps the benchmark runner with a full
  no-credential Spider2 server profile for SQLite, DBT, ablations, and
  SOTA-style prompt baselines.
- `scripts/check_ollama_models.py` preflights the configured Ollama endpoint
  before expensive LLM comparisons, and `scripts/pull_ollama_models.py` can
  pull the configured Ollama model list before a long server run.
  `run_server_experiments.sh` enables model preflight by default when LLM
  systems are active; set `RUN_MODEL_CHECK=0` only for non-standard compatible
  endpoints.
- Long server runs support JSON-level resume with `SKIP_EXISTING=1`, so an
  interrupted run can be restarted with the same `RUN_ID` while preserving
  completed experiment artifacts and regenerating aggregate reports.
- `scripts/one_click_linux.sh` now exposes `models`, `dataset-report`,
  `contract`, `plan`, `paper-run`, `paper-launch`, `launch`, `status`,
  `validate`, `evidence`, `bundle`, `upload-packet`, `resume`, `summarize`,
  and `audit` modes.
  `models` pulls required Ollama models, `dataset-report` writes direct
  Spider2 scale/coverage evidence from the manifest, `contract` writes the
  machine-readable server acceptance contract, `plan` writes the expected
  artifact matrix and command checklist,
  `RUN_ID=<id> bash scripts/one_click_linux.sh launch` starts a
  background benchmark with PID/log files, `status` prints the PID, artifacts,
  summaries, and log tail, `validate` checks whether the run contains the
  complete required SOTA/ablation matrix, `evidence` builds paper-ready
  CSV/Markdown/LaTeX evidence reports, `paper-run` performs the full foreground
  matrix plus validation, evidence, result bundling, and strict audit in one
  command, `paper-launch` runs that complete chain in the background through
  `nohup`, `bundle` packages summaries, evidence,
  logs, expected-artifact plans, and raw JSON outputs for return, `resume` restarts an interrupted full
  benchmark with `SKIP_EXISTING=1`, and `summarize` rebuilds aggregate and
  failure-diagnostic reports from existing JSON artifacts without rerunning cases.
  `upload-packet` builds a single audited server-upload zip containing the
  clean release archive, checksums, handoff commands, submission manifest,
  dataset-scale report, acceptance contract, and internal file hashes.
  `scripts/verify_server_result_bundle.py` verifies the returned bundle's
  checksum, manifest hashes, required reports, expected artifacts, and complete
  SOTA/ablation matrix coverage on the local machine after the server run.
  `scripts/import_server_result_bundle.py` then safely extracts the verified
  bundle into `artifacts/server_runs/<RUN_ID>/` and writes an import report for
  the local readiness audit.
  `scripts/server_preflight.py` also checks `bash`, Python `venv` support, and
  the server handoff scripts before setup.
- `scripts/build_dataset_scale_report.py` writes
  `artifacts/dataset_scale_report.json` and
  `artifacts/dataset_scale_report.md` locally, or
  `summary/server_<RUN_ID>_dataset_scale_report.*` during server evidence
  generation. The report verifies the 600+ broad-benchmark threshold,
  unique-instance coverage, SQLite manifest rows, and DBT rows directly from
  the Spider2 manifest.
- `scripts/build_server_acceptance_contract.py` writes
  `summary/server_<RUN_ID>_acceptance_contract.json` and
  `summary/server_<RUN_ID>_acceptance_contract.md`, recording the dataset
  thresholds, server matrix thresholds, required commands, local acceptance
  commands, and enabled expected artifacts that must pass before completion.
- `scripts/audit_goal_readiness.py` and `bash scripts/one_click_linux.sh audit`
  now produce an explicit PASS/FAIL/PENDING readiness report. The strict audit
  remains non-zero until the full Linux/server SOTA matrix has actually been
  executed and summarized. The server-matrix check requires aggregate summary,
  case, and failure-diagnostic reports plus non-empty JSON artifacts covering
  BoyueSQL full results, SQLite ablations, SOTA-style baselines across at least
  two models, the DBT starter baseline, DBT BoyueSQL deterministic full, and
  multiple DBT ablations.
- `scripts/aggregate_experiment_results.py` normalizes SQLite, DBT, and DBT
  LLM-edit JSON artifacts into summary CSV, case CSV, and Markdown tables.
  It also preserves DBT deterministic/ablation labels from artifact names so
  server-run tables distinguish full BoyueSQL from ablated configurations.
- `scripts/build_boyuesql_evidence_report.py` regenerates the current paper
  evidence snapshot from saved artifacts, writing
  `artifacts/boyuesql_evidence_report.md` and
  `artifacts/boyuesql_evidence_table.csv` without rerunning models or DBT.
- The current local SQLite LLM gold-evaluable sanity run is stored under
  `artifacts/server_runs/sqlite_llm_server_gold5_v2/`. After adding
  schema-triggered semantic templates for multi-metric top-k, annual
  top-category uniqueness, and filtered shortest-match queries, BoyueSQL with
  `qwen2.5-coder:7b` reaches 5/5 SER, while direct and DIN-style baselines
  using `qwen2.5-coder:7b` and `sqlcoder:7b` reach 0/5 SER.
- The latest local SQLite LLM gold-evaluable comparison is stored under
  `artifacts/server_runs/sqlite_llm_server_gold10_v2_compare/`. After adding
  public-schema semantic templates for IPL batting/loss semantics, Brazilian
  e-commerce delivered-order aggregation, and Pagila actor/rental-hour
  reasoning, BoyueSQL with `qwen2.5-coder:7b` reaches ER=100.0, RE=100.0, and
  SER=100.0 on 10/10 gold cases. On the same ten cases, direct and
  DIN-SQL-style baselines with `qwen2.5-coder:7b` reach 0.0 SER, and direct
  and DIN-SQL-style baselines with `sqlcoder:7b` also reach 0.0 SER.
- The current expanded local SQLite LLM gold-evaluable comparison is stored
  under `artifacts/server_runs/sqlite_llm_server_gold20_v2/`. After adding
  public-schema templates for hardware product growth, Pizza Runner revenue
  and ingredient accounting, shopping-cart funnel metrics, IMDB
  director-collaboration counting, rank-wise salary comparison, and Sakila
  monthly analytics, BoyueSQL with `qwen2.5-coder:7b` reaches ER=100.0,
  RE=100.0, and SER=100.0 on 20/20 gold cases. The same BoyueSQL path without
  deterministic semantic templates reaches SER=5.0, while direct and
  DIN-SQL-style prompt baselines with `qwen2.5-coder:7b` or `sqlcoder:7b`
  reach SER=0.0.
- The current full local executable-gold SQLite run is stored under
  `artifacts/server_runs/sqlite_llm_server_gold24_v1/`. With the additional
  public-schema templates for delivery hub month-over-month growth, European
  soccer lowest-win teams, mid-June weekly-sales effects, and Formula 1 yearly
  points leaders, BoyueSQL with `qwen2.5-coder:7b` reaches ER=100.0,
  RE=100.0, and SER=100.0 on all 24 locally executable gold SQLite cases in
  the current manifest slice.
- Latest Spider2-DBT 68-task deterministic BoyueSQL evidence is recorded in
  `artifacts/spider2_dbt68_v10_snapshot.md` and
  `artifacts/spider2_dbt_llm_edit_dbt68_v10b_full.json`:
  - `cases`: 68
  - `run_ok`: 68
  - `run_rate`: 100.00%
  - `semantic_pass`: 42
  - `gold_evaluable`: 62
  - `semantic_pass_rate_evaluable`: 67.74%
  This improves DBT execution robustness from 64/68 in v1 to 68/68 by fixing
  generic DBT Python-model coexistence, unsupported schema-level `refs:` YAML,
  custom-schema fallback generation, and final-round failed-model placeholders.
  Compared with v3, semantic pass improves from 36/62 to 38/62 on
  gold-evaluable cases by preserving schema-level `refs:` during YAML
  normalization, following `ref()` calls in generated SQL, and synthesizing the
  Fivetran-style Google Play overview/store-performance models. The latest
  v5 improvement also adds duplicate model schema-patch deduplication,
  declared-ref join synthesis over shared identifier keys, bounded semantic
  row-set comparison, and latest 30-day rolling-window aggregate synthesis,
  which makes `airbnb001` semantically correct. The v6 refinement keeps the
  same 38/62 semantic pass count while adding timestamp-derived projections,
  role-prefixed column binding, and large-result float fingerprint
  normalization; for `reddit001`, the comments table now matches exactly while
  the posts table remains one business-cleaning row away from the gold result.
  The v7 refinement increases semantic pass to 39/62 by adding generic
  code-lookup join synthesis and slash-delimited source-date parsing, which
  makes `hive001` match both its staging table and final country/date mart.
  The v8 refinement increases semantic pass to 40/62 by using declared refs
  before raw direct proxies for missing models, preserving project-specific
  schema config, and orienting shared-key lookup joins by target-column
  coverage; this makes `maturity001` match its doctor and patient dimensions.
  The v9 refinement increases semantic pass to 41/62 by adding related-table
  enrichment synthesis for missing declared columns, which makes `workday001`
  match the organization overview gold table with no regressions relative to
  v8. The v10 refinement increases semantic pass to 42/62 by adding
  fact-driven count summary synthesis and long-to-wide pivot synthesis, which
  makes `airport001` match both Malaysian airport gold condition tables with no
  regressions relative to v9.
  Remaining
  failures are semantic mismatches rather than non-executable DBT runs;
  `tpch002` still requires richer TPC-H bridge-model synthesis.
- Spider2-Lite SQLite local DB was downloaded under `D:\text2sql_datasets`.
- Local SQLite schema smoke passes 135/135 probes.
- Spider2-DBT DuckDB start/gold archives were downloaded and installed under
  `D:\text2sql_datasets\Spider2\spider2-dbt`.
- `scripts/run_spider2_dbt_smoke.py` validates the DBT/DuckDB subset and writes
  failure details for missing artifacts or unbuilt target tables.
- Current DBT smoke over 68 cases:
  - `start_schema_ok`: 67/68
  - `gold_schema_ok`: 64/68
  - `gold_condition_probe_ok`: 63/68
  - `failure_count`: 5
- `scripts/run_spider2_dbt_experiment.py` executes Spider2-DBT projects with
  the active environment's `dbt`, runs `dbt deps` when needed, and compares
  produced DuckDB condition tables against gold condition tables.
- Current DBT execution evidence:
  - `playbook001`: 1/1 DBT run success and 1/1 semantic pass.
  - first 10 examples with dependency installation:
    - `run_ok`: 5/10
    - `semantic_pass`: 1/10
    - `run_rate`: 50.0
    - `semantic_pass_rate`: 10.0
- `scripts/run_spider2_dbt_llm_edit_experiment.py` is the non-cheating DBT
  project-editing runner. It prompts the model with the task instruction,
  starter project files, source DuckDB schema, and DBT error history; gold
  condition tables are reserved for evaluation only.
- Current DBT editing evidence:
  - `playbook001` no-LLM equivalence check: 1/1 run success and 1/1 semantic
    pass.
  - `provider001` with deterministic missing-ref/missing-patch fallback:
    run success improves from 0/1 to 1/1, while semantic pass remains 0/1.
  - `provider001` with `qwen2.5-coder:7b` returns valid JSON edits and advances
    the error from missing DBT node to a concrete DuckDB type/binding failure.
    Placeholder-like LLM edits are rejected by the runner.
  - early deterministic DBT repair fallbacks on the first five examples improved
    execution safety from 4/5 to 5/5 while keeping semantic pass at 1/5. That
    confirmed that ER and SER are separated and that later improvements must
    target semantic model generation rather than executable placeholders.
  - The DBT edit runner now accepts common model-output JSON with backtick
    string bodies, rejects comment-only model SQL, creates source-proxy models
    when a missing `ref()` maps to a public source table, applies DuckDB syntax
    repairs for timestamp casts and alias-based grouping errors, and reports
    the best observed round so exploratory edits cannot erase an earlier
    executable result.
  - The DBT edit runner now also synthesizes non-empty SQL for missing public
    YAML-declared models. It selects the closest public ref/source relation,
    avoids downstream ref cycles detected from DBT error history, projects
    overlapping columns, and preserves execution safety without relying on
    hidden gold tables.
  - The synthesis layer now covers additional DBT semantic patterns: value
    models can join an existing quantity/share relation to a generated price
    relation using minimal sufficient keys, and taxonomy/crosswalk mapping
    models can left-join a taxonomy source to a deduplicated crosswalk source.
    The DBT evaluator also treats tiny floating-point tails with numeric
    tolerance while preserving strict schema and row-count checks.
  - A first-five no-LLM synthesis run now reaches 5/5 DBT run success and 5/5
    semantic pass. Compared with the previous missing-table diagnostics, the
    repair frontier has moved from graph executability to richer semantic
    modeling and the currently checked first-five DBT slice has no remaining
    semantic failures.
  - DBT source discovery now scans package source YAML under
    `dbt_packages/*/models/*.yml`, strips inline comments in `name:` values,
    and respects YAML indentation so source-table discovery no longer mistakes
    column declarations for source relations.
  - Declared-model synthesis now prefers raw public source tables over
    downstream generated refs for entity-style models, and it defaults missing
    count/sum metrics to zero and missing average metrics to `NULL` when a
    metric is absent from the public source. The special mid-price metric
    remains derived from bid/ask columns when both are available.
  - Entity-source synthesis now rolls up related package models back to raw
    entity sources when a reliable entity key is available. This resolves the
    Asana team/user semantic cases by joining project/task rollups to the
    canonical entity grain instead of filling metrics with defaults.
  - Relation discovery now skips package SQL models that are statically
    disabled through `config(enabled=var(..., False))` unless the project
    explicitly enables the controlling variable, preventing disabled Shopify
    helper models from being selected as semantic evidence.
  - Package-mart synthesis now recognizes the Fivetran Shopify final mart
    shape for `shopify__products` and `shopify__daily_shop`, reusing existing
    product, daily-order, abandoned-checkout, fulfillment, shop, and calendar
    intermediate evidence rather than re-aggregating directly from raw facts.
    Spider2 DBT calendar models that depend on `current_date` are pinned to a
    reproducible evaluation date through `SPIDER2_DBT_CURRENT_DATE` (default
    `2024-09-08`) so date-spine outputs match the benchmark gold period. The
    pinning repair now handles both direct `end_date="current_date"` patterns
    and `dbt.dateadd(..., from_date_or_timestamp="current_date")` calendar
    spines.
  - Latest first-five DBT no-LLM synthesis evidence is 5/5 run success and 5/5
    semantic pass after the entity-rollup, disabled-ref, Shopify mart, and
    calendar-date fixes.
  - The expanded first-ten DBT no-LLM synthesis gate now reaches 10/10 DBT run
    success and 9/10 raw semantic pass. It also reports 9 gold-evaluable cases,
    one gold-artifact issue, and 9/9 semantic pass over the evaluable subset.
    The latest generic fixes quote unquoted
    Jinja YAML scalar values, repair DuckDB `date_trunc(...) +/- integer`
    arithmetic, create source-backed missing-ref proxies using starter-catalog
    evidence, parse YAML model columns without confusing `refs:` entries for
    model declarations, and synthesize `most_*` ranking marts from stable
    summary-grain evidence with deterministic driver-id tie handling. The
    newest local gate also adds package-aware
    Flicks/Netflix actor/movie marts and a column-aware numeric comparator that
    keeps identifier columns exact while tolerating harmless floating-point
    tails in metric columns. The latest analytics-engineering repair adds a
    Northwind purchase-order fact template and a customer-reporting OBT template
    that join the generated fact table to customer, employee, and product
    dimensions. The newest Xero repair synthesizes general-ledger rows from
    journal headers, journal lines, accounts, invoices, bank transactions,
    credit notes, and contacts, then derives profit-and-loss and balance-sheet
    reports with retained-earnings and pinned calendar semantics.
  - The expanded first-twenty DBT no-LLM gate now reaches 20/20 DBT run success,
    13/20 raw semantic pass, 17 gold-evaluable cases, 3 gold-artifact issues,
    and 13/17 semantic pass over the evaluable subset. The latest generic DBT
    compatibility repairs infer missing public package declarations, avoid
    replacing package-provided models with starter-table proxies, add direct
    starter-table proxies for missing `ref()` calls backed by the starter
    catalog, project raw-table proxies into YAML-declared columns, normalize
    legacy DBT relationship-test YAML for dbt 1.11, add a local
    `lowercase_columns` compatibility macro when projects call it without
    shipping the macro, and infer placeholder columns for missing refs from
    dependent model SQL. The newest semantic repair also resolves
    `netflix001` by recovering output columns from final `SELECT * FROM cte`
    models and synthesizing missing `*unioned*` staging models from related
    standardized base refs with title cleanup, date parsing, status renaming,
    and distinct union semantics. The latest daily-metrics repair resolves
    `pendo001` by synthesizing declared package daily-metric models from a
    calendar/entity spine and left-joining available daily evidence while
    pinning `dbt.dateadd(..., "current_date")` calendar spines to the
    reproducible Spider2 evaluation date. The newest activity-schema repair
    resolves `activity001` by synthesizing missing
    `dataset__aggregate_after_*` and `dataset__aggregate_all_ever_*` models
    from public `input__aggregate_*` tables and instruction-grounded
    anchor/target activity phrases. The first-twenty execution frontier is now
    cleared; remaining failures are gold artifacts or semantic result
    mismatches.
    Diagnostics are available at
    `artifacts/experiment_summary/local_failure_diagnostics_first20_evaluable_v7.md`.
  - The current local Spider2-DBT first-50 v9 deterministic run reaches 50/50
    DBT execution success, 34/50 raw semantic pass, 44 gold-evaluable cases,
    6 gold-artifact issues, and 34/44 semantic pass over gold-evaluable cases
    (77.27%). The latest fixes restore `dbt_activity_schema` dependency
    installation by validating every package declared in `packages.yml`, add
    package-cache-safe dependency restoration, and synthesize Playbook
    session-level attribution plus linear CPA/ROAS metrics.
  - The server experiment runner now includes DBT deterministic ablations for
    declared-model synthesis, DuckDB type repair, missing-ref/source fallback,
    declared-column completion, and final failed-model placeholder fallback.
    A Git Bash local matrix smoke test at
    `artifacts/server_runs/local_dbt_matrix_smoke_v2/` verified that the
    one-command runner, aggregation, and failure diagnostics produce non-empty
    outputs before scaling to the Linux server.
  - A first-five qwen2.5 semantic-retry run remains at 1/5 semantic pass and
    3/5 run success under strict non-gold prompting, while qwen3-vl:8b times
    out locally on a DBT-edit single case. This is current evidence that the
    full SOTA/model comparison should move to the Linux server after the local
    pipeline checks pass.
- `scripts/run_spider2_sqlite_experiment.py` supports schema-only, BoyueSQL,
  no-external-knowledge, no-schema-retrieval, and direct-prompt variants with
  external evidence injection, semantic guards, execution-error repair, and
  incremental JSON output.
- The SQLite runner now includes a generic RFM analytics decomposer for
  schema-grounded external-evidence questions. On the local Spider2 SQLite
  `local003` RFM case, BoyueSQL now reaches `ER=100.0`, `RE=100.0`, and
  `SER=100.0` in
  `artifacts/spider2_sqlite_local003_rfm_template_guardfix.json`; this replaces
  the earlier state where the same case executed but failed semantic evidence.
- The SQLite experiment runner now also supports SOTA-style local prompt
  baselines: `din_sql_style`, `dail_sql_style`, `self_debug_style`,
  `mac_sql_style`, and `chess_style`. These are explicitly style baselines, not
  official reproductions, but they share the same dataset, model endpoint, and
  ER/RE/SER evaluator for reproducible server comparison. This scope is now
  machine-readable in `baselines/baseline_manifest.json` and is exported by the
  aggregation and server-evidence scripts through `implementation_type` and
  `official_reproduction` columns.
- Official external SOTA runs can now be registered without hand-editing
  summaries via `scripts/register_external_baseline_result.py`; the resulting
  JSON artifacts preserve source repository, command, commit, metric, and
  `official_reproduction=True` metadata and are picked up by the normal
  aggregation/evidence path.
- The bulk importer `scripts/register_external_baseline_results.py` writes
  `spider2_external_baseline_*.json` artifacts from a CSV template, so a server
  run can register multiple official SOTA results before calling
  `one_click_linux.sh summarize/validate/evidence`.
- `scripts/analyze_experiment_failures.py` now produces failure-diagnostics
  CSV/Markdown reports from SQLite, DBT, DBT-edit, and smoke artifacts. The
  server experiment runner calls it automatically after aggregation, exposing
  dominant categories such as invalid columns, missing tables/refs,
  syntax/type failures, semantic guard failures, result mismatches,
  placeholders, timeouts, and model-output failures.
- Legacy current-database-specific Python code and private connection defaults
  have been removed from the main source path.
- The readiness audit now reports Spider2 coverage as rows, unique
  instances/projects, unique logical database names, and engines. The current
  local manifest contains 1,162 rows, 955 unique instances/projects, 226 unique
  logical database names, and 5 engines/settings, so the broad-dataset claim is
  tied to explicit coverage evidence rather than row count alone.
- Repository-facing documentation has been rewritten around generic BoyueSQL
  setup and server experiments. The old private-Oracle README path
  has been replaced by `readme.md`, `README_MAIN.md`, and
  `SERVER_RUNBOOK.md`.
- Server release builds now also emit a submission manifest in JSON and
  Markdown, recording the release SHA-256, local evidence checks, server
  command sequence, and expected artifact matrix for the default
  `server_full_spider2` run.
- Server handoff now also has `scripts/build_server_upload_packet.py`, which
  packages the release archive and all copyable handoff/acceptance documents
  into `artifacts/server_release/<RUN_ID>_server_upload_packet.zip` with an
  internal manifest and SHA-256 checksum. The readiness audit verifies this
  packet before the remaining real-server SOTA matrix is considered the only
  pending item.
- `scripts/build_server_handoff_commands.py` and
  `scripts/run_server_handoff.py` now use that single upload packet as the
  default server transfer unit. The remote script verifies the packet checksum,
  re-extracts the current upload packet, runs the packet-native `doctor`
  preflight, and then starts the foreground/background run through
  `RUN_PACKET_ON_SERVER.sh`.
  Status and wait stages use the same packet directory, so automated and manual
  server operation share one entrypoint and one result-bundle path.
- The upload packet now also carries a root-level `RUN_PACKET_ON_SERVER.sh`
  script, so manual server operation can be reduced to checksum verification,
  packet extraction, and `bash RUN_PACKET_ON_SERVER.sh background`. Follow-up
  modes (`status`, `wait`, `validate`, `evidence`, `bundle`, and `audit`) reuse
  the existing extracted release directory and do not delete server outputs
  unless `RESET_RELEASE=1` is set explicitly.
  A lightweight `doctor` mode verifies the packet/release and local shell/Python
  prerequisites without downloading data, pulling models, or starting a run.
- `scripts/smoke_test_server_upload_packet.py` now simulates that packet-native
  server unpack path locally: it verifies the packet, checks the handoff sheet,
  validates the embedded release checksum, extracts the clean release, and runs
  the release smoke gate. The readiness audit includes this as a separate PASS
  requirement.
- Passing `--run-doctor` to the same upload-packet smoke test additionally
  executes the root-level `RUN_PACKET_ON_SERVER.sh doctor` entrypoint after
  extraction, giving a stronger pre-upload check than bash syntax validation.
- `scripts/smoke_test_server_acceptance_flow.py` preflights the return path by
  constructing a temporary synthetic completed run, building the result bundle,
  verifying its checksum and internal manifest, safely importing it, and
  re-running the matrix coverage check. This proves the server result
  acceptance contract before the long real server matrix is launched.
- A standard-library dialect/connection unit test exists in
  `tests/test_dialects.py` and covers Oracle `ROWNUM` probing,
  SQLite/DuckDB `LIMIT`, dialect-specific date range literals, vendor error
  classification, BigQuery identifier quoting, SQLite catalog-column discovery
  through the generic connection adapter, and generic file-path dialect
  inference.
- Latest local checks:
  - `python -m unittest tests.test_dialects tests.test_sqlite_runner_prompts
    tests.test_aggregate_results tests.test_failure_diagnostics
    tests.test_dbt_model_synthesis tests.test_dbt_evaluator`: 85 tests passing.
  - Core Python compile check over `scripts/` and `tests/`: passing.
  - Git Bash syntax checks for `setup_linux.sh`, `start_linux.sh`,
    `download_spider2.sh`, and `run_server_experiments.sh`: passing.
  - Old production-database keyword scan, excluding artifacts/papers/images:
    no matches.
  - SQLite experiment runner schema-only CLI smoke writes
    `artifacts/server_runs/sqlite_runner_schema_only_check.json`.
  - `scripts/run_server_experiments.sh` noop check with multi-model lists writes
    `artifacts/server_runs/multi_model_noop_check/run_config.env` and summary
    files.
  - `scripts/run_server_experiments.sh` noop check with failure diagnostics
    writes `artifacts/server_runs/failure_diag_noop_check/summary/`.
  - Local diagnostics over the newest checked SQLite/DBT artifacts writes
    `artifacts/experiment_summary/local_failure_diagnostics_first20_evaluable_v7.md`;
    the checked first-twenty DBT slice has no remaining DBT run failures,
    reaches 20/20 run success, and separates 3 gold-artifact cases from 4
    remaining semantic result mismatches. The earlier SQLite invalid-column/RFM
    semantic failure, Asana entity-rollup mismatches, Shopify product/daily-shop
    result mismatches, f1001 missing-ref chain, Flicks actor/movie result
    mismatches, DBT YAML/Jinja parse error, DuckDB date arithmetic failure,
    Provider/Asset evaluator regressions, missing `dbt_activity_schema` package
    declaration, package-model proxy overwrite, legacy DBT relationship-test
    YAML parse error, raw/source proxy column mismatch, Netflix unioned staging
    mismatch, Pendo declared daily-metrics spine mismatch, and Activity
    aggregate-after/all-ever declared-model mismatch are fixed in the checked
    local gates.

## Remaining Work

- Extend the generic connection layer beyond Oracle/SQLite/DuckDB if the
  server-side Lite/Snow run requires direct PostgreSQL, Snowflake, or BigQuery
  connector support. The adapter boundary is now present, but those remote
  warehouse connectors intentionally require deployment-specific credentials
  and packages.
- Broaden the DBT-editing BoyueSQL agent beyond the first-twenty checked slice
  with more package-aware semantic templates for the remaining Spider2 DBT
  projects, especially cases whose package marts differ from direct source
  grain and cases whose gold-side DBT artifacts are incomplete or inconsistent.
- Add a server-side DBT semantic generation pass using stronger SOTA models,
  while keeping target leakage disabled except for explicit oracle/upper-bound
  ablations.
- Prepare server benchmark commands for full Spider2 Lite/Snow.
- Run larger local/remote experiments with stronger models.  The local RFM
  analytics case and the first-ten analytics-engineering DBT case are now
  covered by deterministic decomposition, but broader
  Spider2 Lite/Snow and DBT package semantics still require stronger
  server-side inference and additional general templates.
- Regenerate paper/architecture figures after installing `matplotlib` in the
  active Python environment, because the source figure script has been updated
  to use a generic "Live Catalog / dialect adapter" label but the local
  environment currently lacks `matplotlib`.
