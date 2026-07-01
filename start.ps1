# EC-SQL generic service startup for local Windows development.
# Linux/server deployments should prefer scripts/start_linux.sh.

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $name, $value = $line.Split("=", 2)
            [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
        }
    }
}

if (-not $env:APP_ENTRY) {
    $env:APP_ENTRY = "ecsql_service.py"
}

if (-not $env:PYTHON) {
    $env:PYTHON = "python"
}

Write-Host "Starting EC-SQL generic service: $env:APP_ENTRY"
& $env:PYTHON $env:APP_ENTRY
