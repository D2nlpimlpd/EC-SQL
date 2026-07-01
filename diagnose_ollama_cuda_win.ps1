<#
.SYNOPSIS
English GPU startup diagnostic fix for Ollama on Windows (focus on CUDA/PyTorch)

.DESCRIPTION
- Locates Ollama executable
- Checks NVIDIA GPU presence via nvidia-smi or WMI
- Detects CUDA toolkit installation (latest version) and CUDA bin path
- Checks PyTorch CUDA availability in the current Python environment
- Outputs a concise JSON diagnostic log and practical fix commands
#>

param()

function Write-Log {
    param([string]$Message, [ConsoleColor]$Color = "Gray")
    $ts = Get-Date -Format "HH:mm:ss"
    Write-Host "[$ts] $Message" -ForegroundColor $Color
}

# 1) Must run as Administrator
if (-not ([Security.Principal.WindowsPrincipal]([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Log "Please run this script as Administrator." Red
    exit 1
}

# 2) Locate Ollama executable
Write-Log "Locating Ollama executable..." Yellow
$ollamaExe = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaExe) {
    $possible = @(
        "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe",
        "$env:USERPROFILE\AppData\Local\Programs\Ollama\ollama.exe",
        "C:\Program Files\Ollama\ollama.exe"
    )
    foreach ($p in $possible) {
        if (Test-Path $p) { $ollamaExe = $p; break }
    }
}
if (-not $ollamaExe) {
    Write-Log "Ollama executable not found. Please install or provide path." Yellow
    exit 1
}
Write-Log "Ollama executable found: $ollamaExe" Green

# 3) GPU / CUDA / PyTorch detection
Write-Log "Detecting GPU / CUDA / PyTorch availability..." Yellow

# NVIDIA GPU check
$gpuInfo = @()
$nvSmiCmd = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($nvSmiCmd) {
    try {
        $gpuInfo = (& $nvSmiCmd -L 2>&1) -split "`n" | Where-Object { $_ -ne "" }
    } catch {
        $gpuInfo = @()
    }
}
if ($gpuInfo.Count -eq 0) {
    try {
        $gpuInfo = Get-CimInstance -ClassName Win32_VideoController | Select-Object Name, DriverVersion | ForEach-Object {
            [pscustomobject]@{ name = $_.Name; driver = $_.DriverVersion }
        } | Where-Object { $_.name -ne $null }
    } catch {
        $gpuInfo = @()
    }
}

# CUDA toolkit
$cudaDir = Get-ChildItem "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA" -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
$cudaToolkitPath = if ($cudaDir) { $cudaDir.FullName } else { $null }
$cudaBinPath = if ($cudaDir) { Join-Path $cudaDir.FullName "bin" } else { $null }
$cudaVersion = if ($cudaDir) { $cudaDir.Name.Replace("CUDA-", "") } else { $null }
$cudaAvailable = $cudaDir -ne $null

# PyTorch CUDA test (optional)
$pyTorchTest = @{}
if (Get-Command python -ErrorAction SilentlyContinue) {
    try {
        $out = & python -c "import torch; print('CUDA_AVAILABLE=' + str(torch.cuda.is_available()))" 2>&1
        if ($LASTEXITCODE -eq 0) {
            $pyTorchTest = @{
                available = $out -match "CUDA_AVAILABLE=True"
            }
            if ($pyTorchTest.available) {
                $devName = & python -c "import torch; print(torch.cuda.get_device_name(0))" 2>&1
                $pyTorchTest.device = $devName.Trim()
            }
        } else {
            $pyTorchTest = @{ available = $false }
        }
    } catch {
        $pyTorchTest = @{ available = $null; device = $null }
    }
} else {
    $pyTorchTest = @{ available = $null; device = $null }
}

# 4) Diagnostics object
$diag = [ordered]@{
    timestamp = (Get-Date).ToString("o")
    os = (Get-CimInstance Win32_OperatingSystem).Caption
    arch = (Get-CimInstance Win32_OperatingSystem).OSArchitecture
    ollama_path_found = $ollamaExe -ne $null
    ollama_exe = $ollamaExe
    nvidia_smi_available = $nvSmiCmd -ne $null
    gpu_info = $gpuInfo
    cuda_toolkit = @{
        found = $cudaAvailable
        path = $cudaToolkitPath
        bin = $cudaBinPath
        version = $cudaVersion
    }
    pytorch_cuda = $pyTorchTest
    logs = @()
}

# 5) Write JSON log
$logDir = Join-Path $env:USERPROFILE "ollama_gpu_diag_logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $logDir ("diag_ollama_gpu_fix_en_" + $timestamp + ".json")
$diagJson = $diag | ConvertTo-Json -Depth 6
Set-Content -Path $logFile -Value $diagJson -Encoding UTF8

Write-Host ""
Write-Host "GPU diagnostics complete. Log written to: $logFile" Green

# 6) Provide targeted fix commands if PyTorch CUDA is not available
Write-Host ""
Write-Host "Targeted fix suggestions (copy-paste into PowerShell or CMD):" Cyan
if ($diag.pytorch_cuda.available -eq $false) {
    Write-Host "  - Install a PyTorch wheel that matches CUDA version, for Windows:" Yellow
    Write-Host "      pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121" Yellow
    Write-Host "    or with conda:" Yellow
    Write-Host "      conda install pytorch torchvision torchaudio cudatoolkit=12.1 -c pytorch -c nvidia" Yellow
    Write-Host "  - After installation, verify in Python:" Yellow
    Write-Host "      python -c 'import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))'" Yellow
}