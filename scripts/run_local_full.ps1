param(
    [string]$SpiderRoot = "D:\text2sql_datasets\Spider2",
    [string]$RunId = "",
    [string]$Python = "",
    [int]$SqliteLimit = 135,
    [int]$DbtLimit = 68,
    [int]$DbtAblationLimit = 20,
    [switch]$WithLlm,
    [int]$LlmLimit = 8,
    [string]$EcSqlModel = "qwen3-vl:8b",
    [string]$BaselineModels = "qwen2.5-coder:7b,sqlcoder:7b",
    [string]$OllamaBaseUrl = "http://localhost:11434",
    [switch]$SkipExisting
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $RunId) {
    $RunId = "local_full_{0}" -f (Get-Date -Format "yyyyMMdd_HHmmss")
}

function Resolve-Python {
    param([string]$Requested)
    $candidates = @()
    if ($Requested) {
        $candidates += $Requested
    }
    $candidates += @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $ProjectRoot ".tmp_server_install_check_venv\Scripts\python.exe"),
        (Join-Path $ProjectRoot ".tmp_server_runtime_install_check_venv\Scripts\python.exe"),
        "C:\Users\wangh\.conda\envs\nl2sql\python.exe",
        "C:\Users\wangh\.conda\envs\database\python.exe",
        "python"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -eq "python") {
            $cmd = Get-Command python -ErrorAction SilentlyContinue
            if ($cmd -and $cmd.Source -notlike "*WindowsApps*") {
                return $cmd.Source
            }
            continue
        }
        $resolved = Resolve-Path $candidate -ErrorAction SilentlyContinue
        if ($resolved) {
            return $resolved.Path
        }
    }
    throw "No usable Python interpreter found. Pass -Python C:\path\to\python.exe."
}

function Invoke-JsonStep {
    param(
        [string]$Label,
        [string]$OutFile,
        [string[]]$CommandArgs
    )
    if ($SkipExisting -and (Test-Path $OutFile) -and ((Get-Item $OutFile).Length -gt 0)) {
        Write-Host "[local-full] skip existing $Label -> $OutFile"
        return
    }
    Write-Host "[local-full] $Label"
    & $script:PythonExe @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "[local-full] step failed with exit code ${LASTEXITCODE}: $Label"
    }
}

$script:PythonExe = Resolve-Python -Requested $Python
$SpiderRoot = (Resolve-Path $SpiderRoot).Path
$OutDir = Join-Path $ProjectRoot "artifacts\local_runs\$RunId"
$SummaryDir = Join-Path $OutDir "summary"
$Manifest = Join-Path $OutDir "spider2_manifest.csv"
$DbtCache = Join-Path $OutDir "dbt_package_cache"

New-Item -ItemType Directory -Force -Path $OutDir, $SummaryDir, $DbtCache | Out-Null
$env:SPIDER2_DBT_PACKAGE_CACHE = $DbtCache
$env:SPIDER2_DBT_CURRENT_DATE = "2024-09-08"

@"
RUN_ID=$RunId
PROJECT_ROOT=$ProjectRoot
SPIDER_ROOT=$SpiderRoot
PYTHON=$PythonExe
SQLITE_LIMIT=$SqliteLimit
DBT_LIMIT=$DbtLimit
DBT_ABLATION_LIMIT=$DbtAblationLimit
WITH_LLM=$($WithLlm.IsPresent)
LLM_LIMIT=$LlmLimit
EC_SQL_MODELS=$EcSqlModel
BASELINE_MODELS=$BaselineModels
OLLAMA_BASE_URL=$OllamaBaseUrl
"@ | Set-Content -Path (Join-Path $OutDir "run_config.env") -Encoding UTF8

Write-Host "[local-full] project: $ProjectRoot"
Write-Host "[local-full] spider root: $SpiderRoot"
Write-Host "[local-full] python: $PythonExe"
Write-Host "[local-full] out: $OutDir"
Write-Host "[local-full] 32B+ models are disabled"

Invoke-JsonStep "Spider2 manifest" $Manifest @(
    (Join-Path $ProjectRoot "scripts\spider2_manifest.py"),
    "--spider-root", $SpiderRoot,
    "--out", $Manifest,
    "--sample", "3"
)

Invoke-JsonStep "SQLite smoke full" (Join-Path $OutDir "spider2_sqlite_smoke.json") @(
    (Join-Path $ProjectRoot "scripts\run_spider2_sqlite_smoke.py"),
    "--manifest", $Manifest,
    "--spider-root", $SpiderRoot,
    "--limit", "$SqliteLimit",
    "--out", (Join-Path $OutDir "spider2_sqlite_smoke.json")
)

Invoke-JsonStep "DBT smoke full" (Join-Path $OutDir "spider2_dbt_smoke.json") @(
    (Join-Path $ProjectRoot "scripts\run_spider2_dbt_smoke.py"),
    "--spider-root", $SpiderRoot,
    "--limit", "$DbtLimit",
    "--out", (Join-Path $OutDir "spider2_dbt_smoke.json")
)

Invoke-JsonStep "SQLite schema-only full" (Join-Path $OutDir "spider2_sqlite_schema_only.json") @(
    (Join-Path $ProjectRoot "scripts\run_spider2_sqlite_experiment.py"),
    "--manifest", $Manifest,
    "--spider-root", $SpiderRoot,
    "--systems", "schema_only",
    "--limit", "$SqliteLimit",
    "--require-gold",
    "--out", (Join-Path $OutDir "spider2_sqlite_schema_only.json")
)

Invoke-JsonStep "DBT starter-project baseline" (Join-Path $OutDir "spider2_dbt_existing_project.json") @(
    (Join-Path $ProjectRoot "scripts\run_spider2_dbt_experiment.py"),
    "--spider-root", $SpiderRoot,
    "--limit", "$DbtLimit",
    "--timeout", "240",
    "--out", (Join-Path $OutDir "spider2_dbt_existing_project.json")
)

function Invoke-DbtEcSql {
    param(
        [string]$Label,
        [int]$Limit,
        [string[]]$ExtraArgs
    )
    $outFile = Join-Path $OutDir "spider2_dbt_llm_edit_$Label.json"
    $workDir = Join-Path $OutDir "work_dbt_$Label"
    $args = @(
        (Join-Path $ProjectRoot "scripts\run_spider2_dbt_llm_edit_experiment.py"),
        "--spider-root", $SpiderRoot,
        "--limit", "$Limit",
        "--work-dir", $workDir,
        "--out", $outFile,
        "--timeout", "240",
        "--edit-rounds", "5",
        "--no-llm",
        "--spider2-current-date", "2024-09-08"
    ) + $ExtraArgs
    Invoke-JsonStep "DBT deterministic $Label" $outFile $args
}

$fullFlags = @(
    "--missing-ref-fallback",
    "--missing-source-fallback",
    "--duckdb-type-fallback",
    "--declared-column-fallback",
    "--declared-model-synthesis",
    "--declared-model-fallback"
)
Invoke-DbtEcSql "ecsql_deterministic_full" $DbtLimit $fullFlags

Invoke-DbtEcSql "ecsql_ablation_no_declared_model_synthesis" $DbtAblationLimit @(
    "--missing-ref-fallback",
    "--missing-source-fallback",
    "--duckdb-type-fallback",
    "--declared-column-fallback",
    "--declared-model-fallback"
)
Invoke-DbtEcSql "ecsql_ablation_no_duckdb_type_repair" $DbtAblationLimit @(
    "--missing-ref-fallback",
    "--missing-source-fallback",
    "--declared-column-fallback",
    "--declared-model-synthesis",
    "--declared-model-fallback"
)
Invoke-DbtEcSql "ecsql_ablation_no_missing_ref_source_fallback" $DbtAblationLimit @(
    "--duckdb-type-fallback",
    "--declared-column-fallback",
    "--declared-model-synthesis",
    "--declared-model-fallback"
)
Invoke-DbtEcSql "ecsql_ablation_no_declared_column_completion" $DbtAblationLimit @(
    "--missing-ref-fallback",
    "--missing-source-fallback",
    "--duckdb-type-fallback",
    "--declared-model-synthesis",
    "--declared-model-fallback"
)

if ($WithLlm) {
    Write-Host "[local-full] checking local Ollama models"
    & $PythonExe (Join-Path $ProjectRoot "scripts\check_ollama_models.py") `
        --base-url $OllamaBaseUrl `
        --model $EcSqlModel `
        --model $BaselineModels

    Invoke-JsonStep "SQLite EC-SQL LLM sample $EcSqlModel" (Join-Path $OutDir "spider2_sqlite_ecsql_$($EcSqlModel -replace '[^A-Za-z0-9._-]+','_').json") @(
        (Join-Path $ProjectRoot "scripts\run_spider2_sqlite_experiment.py"),
        "--manifest", $Manifest,
        "--spider-root", $SpiderRoot,
        "--systems", "ecsql,no_semantic_templates,no_schema_retrieval,no_repair",
        "--model", $EcSqlModel,
        "--ollama-base-url", $OllamaBaseUrl,
        "--ollama-api", "chat",
        "--limit", "$LlmLimit",
        "--require-gold",
        "--num-predict", "2048",
        "--timeout", "180",
        "--max-repairs", "2",
        "--out", (Join-Path $OutDir "spider2_sqlite_ecsql_$($EcSqlModel -replace '[^A-Za-z0-9._-]+','_').json")
    )

    foreach ($model in ($BaselineModels -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })) {
        Invoke-JsonStep "SQLite direct baseline $model" (Join-Path $OutDir "spider2_sqlite_direct_$($model -replace '[^A-Za-z0-9._-]+','_').json") @(
            (Join-Path $ProjectRoot "scripts\run_spider2_sqlite_experiment.py"),
            "--manifest", $Manifest,
            "--spider-root", $SpiderRoot,
            "--systems", "direct",
            "--model", $model,
            "--ollama-base-url", $OllamaBaseUrl,
            "--ollama-api", "chat",
            "--limit", "$LlmLimit",
            "--require-gold",
            "--num-predict", "2048",
            "--timeout", "180",
            "--max-repairs", "0",
            "--out", (Join-Path $OutDir "spider2_sqlite_direct_$($model -replace '[^A-Za-z0-9._-]+','_').json")
        )
    }
}

$resultFiles = @(Get-ChildItem -Path $OutDir -Filter "spider2*.json" | ForEach-Object { $_.FullName })
if ($resultFiles.Count -gt 0) {
    & $PythonExe (Join-Path $ProjectRoot "scripts\aggregate_experiment_results.py") `
        --inputs $resultFiles `
        --out-dir $SummaryDir `
        --summary-name "local_$RunId"

    & $PythonExe (Join-Path $ProjectRoot "scripts\analyze_experiment_failures.py") `
        --inputs $resultFiles `
        --out-dir $SummaryDir `
        --name "local_${RunId}_failures"
}

Write-Host "[local-full] done"
Write-Host "[local-full] summary: $(Join-Path $SummaryDir "local_$RunId.md")"
Write-Host "[local-full] failures: $(Join-Path $SummaryDir "local_${RunId}_failures.md")"
