param(
    [string]$Root = "D:\text2sql_datasets",
    [switch]$Force,
    [switch]$DryRun,
    [switch]$InspectOnly,
    [switch]$LocalDb
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$python = if ($env:PYTHON) { $env:PYTHON } else { "python" }

$argsList = @("$scriptDir\download_spider2.py", "--root", $Root)
if ($Force) { $argsList += "--force" }
if ($DryRun) { $argsList += "--dry-run" }
if ($InspectOnly) { $argsList += "--inspect-only" }
if ($LocalDb) { $argsList += "--localdb" }

Push-Location $projectRoot
try {
    & $python @argsList
}
finally {
    Pop-Location
}
