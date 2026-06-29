param(
    [string]$OutputDir = ".\baselines",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$repos = @(
    @{ Name = "DAIL-SQL"; Repo = "https://github.com/BeachWang/DAIL-SQL.git"; Category = "few-shot prompt engineering" },
    @{ Name = "MAC-SQL"; Repo = "https://github.com/wbbeyourself/MAC-SQL.git"; Category = "multi-agent text-to-SQL" },
    @{ Name = "CHESS"; Repo = "https://github.com/ShayanTalaei/CHESS.git"; Category = "multi-agent scalable SQL synthesis" },
    @{ Name = "DB-GPT-Hub"; Repo = "https://github.com/eosphoros-ai/DB-GPT-Hub.git"; Category = "open text-to-SQL benchmark/fine-tuning hub" },
    @{ Name = "SQLCoder"; Repo = "https://github.com/defog-ai/sqlcoder.git"; Category = "SQL-specialized code model" }
)

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

foreach ($item in $repos) {
    $dest = Join-Path $OutputDir $item.Name
    if ((Test-Path $dest) -and -not $Force) {
        Write-Host "exists $($item.Name) -> $dest"
        continue
    }
    if ((Test-Path $dest) -and $Force) {
        Remove-Item -LiteralPath $dest -Recurse -Force
    }
    Write-Host "cloning $($item.Name) [$($item.Category)]"
    git clone --depth 1 --filter=blob:none $item.Repo $dest
}

Write-Host "baseline repositories are ready in $OutputDir"
