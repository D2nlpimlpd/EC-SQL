param(
    [string]$SpiderRoot = "D:\text2sql_datasets\Spider2",
    [string]$RunId = "",
    [string]$Python = "",
    [int]$SqliteSmokeLimit = 8,
    [int]$DbtSmokeLimit = 2,
    [int]$SchemaOnlyLimit = 8,
    [switch]$WithLlm,
    [string]$BoyueSqlModel = "qwen3-vl:8b",
    [string]$BaselineModels = "qwen2.5-coder:7b,sqlcoder:7b",
    [int]$LlmLimit = 2,
    [string]$OllamaBaseUrl = "http://localhost:11434"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $RunId) {
    $RunId = "local_smoke_{0}" -f (Get-Date -Format "yyyyMMdd_HHmmss")
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
        try {
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
        catch {
            continue
        }
    }
    throw "No usable Python interpreter found. Pass -Python C:\path\to\python.exe."
}

$PythonExe = Resolve-Python -Requested $Python
$SpiderRoot = (Resolve-Path $SpiderRoot).Path
$OutDir = Join-Path $ProjectRoot "artifacts\local_runs\$RunId"
$SummaryDir = Join-Path $OutDir "summary"
$Manifest = Join-Path $OutDir "spider2_manifest.csv"

New-Item -ItemType Directory -Force -Path $OutDir, $SummaryDir | Out-Null

@"
RUN_ID=$RunId
PROJECT_ROOT=$ProjectRoot
SPIDER_ROOT=$SpiderRoot
PYTHON=$PythonExe
BOYUESQL_MODELS=$BoyueSqlModel
BASELINE_MODELS=$BaselineModels
WITH_LLM=$($WithLlm.IsPresent)
"@ | Set-Content -Path (Join-Path $OutDir "run_config.env") -Encoding UTF8

Write-Host "[local-smoke] project: $ProjectRoot"
Write-Host "[local-smoke] spider root: $SpiderRoot"
Write-Host "[local-smoke] python: $PythonExe"
Write-Host "[local-smoke] out: $OutDir"
Write-Host "[local-smoke] 32B+ models are not used by this script"

& $PythonExe (Join-Path $ProjectRoot "scripts\spider2_manifest.py") `
    --spider-root $SpiderRoot `
    --out $Manifest `
    --sample 3

& $PythonExe (Join-Path $ProjectRoot "scripts\run_spider2_sqlite_smoke.py") `
    --manifest $Manifest `
    --spider-root $SpiderRoot `
    --limit $SqliteSmokeLimit `
    --out (Join-Path $OutDir "spider2_sqlite_smoke.json")

& $PythonExe (Join-Path $ProjectRoot "scripts\run_spider2_dbt_smoke.py") `
    --spider-root $SpiderRoot `
    --limit $DbtSmokeLimit `
    --out (Join-Path $OutDir "spider2_dbt_smoke.json")

& $PythonExe (Join-Path $ProjectRoot "scripts\run_spider2_sqlite_experiment.py") `
    --manifest $Manifest `
    --spider-root $SpiderRoot `
    --systems "schema_only" `
    --limit $SchemaOnlyLimit `
    --require-gold `
    --out (Join-Path $OutDir "spider2_sqlite_schema_only.json")

if ($WithLlm) {
    Write-Host "[local-smoke] checking local Ollama models"
    & $PythonExe (Join-Path $ProjectRoot "scripts\check_ollama_models.py") `
        --base-url $OllamaBaseUrl `
        --model $BoyueSqlModel `
        --model $BaselineModels

    & $PythonExe (Join-Path $ProjectRoot "scripts\run_spider2_sqlite_experiment.py") `
        --manifest $Manifest `
        --spider-root $SpiderRoot `
        --systems "boyuesql" `
        --model $BoyueSqlModel `
        --ollama-base-url $OllamaBaseUrl `
        --ollama-api "chat" `
        --limit $LlmLimit `
        --require-gold `
        --num-predict 2048 `
        --timeout 180 `
        --max-repairs 2 `
        --out (Join-Path $OutDir "spider2_sqlite_boyuesql_$($BoyueSqlModel -replace '[^A-Za-z0-9._-]+','_').json")

    foreach ($model in ($BaselineModels -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })) {
        & $PythonExe (Join-Path $ProjectRoot "scripts\run_spider2_sqlite_experiment.py") `
            --manifest $Manifest `
            --spider-root $SpiderRoot `
            --systems "direct" `
            --model $model `
            --ollama-base-url $OllamaBaseUrl `
            --ollama-api "chat" `
            --limit $LlmLimit `
            --require-gold `
            --num-predict 2048 `
            --timeout 180 `
            --max-repairs 0 `
            --out (Join-Path $OutDir "spider2_sqlite_direct_$($model -replace '[^A-Za-z0-9._-]+','_').json")
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

Write-Host "[local-smoke] done"
Write-Host "[local-smoke] summary: $(Join-Path $SummaryDir "local_$RunId.md")"
Write-Host "[local-smoke] failures: $(Join-Path $SummaryDir "local_${RunId}_failures.md")"
