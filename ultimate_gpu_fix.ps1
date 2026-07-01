# ultimate_gpu_fix.ps1
# RTX 5080 Ollama GPU Fix

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "Ollama RTX 5080 GPU Fix Script" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan

# Step 1: Stop Ollama
Write-Host "`n[1/5] Stopping Ollama service..." -ForegroundColor Yellow
Get-Process ollama* -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

# Step 2: Remove corrupted nvcuda.dll
Write-Host "`n[2/5] Cleaning corrupted CUDA libraries..." -ForegroundColor Yellow

$problematicPaths = @(
    "C:\Windows\System32\nvcuda.dll",
    "C:\Windows\nvcuda.dll",
    "C:\WINDOWS\system32\nvcuda.dll"
)

foreach ($path in $problematicPaths) {
    if (Test-Path $path) {
        try {
            Remove-Item $path -Force -ErrorAction SilentlyContinue
            Write-Host "  OK: Removed $path" -ForegroundColor Green
        } catch {
            Write-Host "  WARNING: Cannot remove (in use): $path" -ForegroundColor Yellow
        }
    }
}

# Step 3: Copy correct libraries from CUDA Toolkit
Write-Host "`n[3/5] Copying correct libraries from CUDA Toolkit..." -ForegroundColor Yellow

$cudaSourceDir = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.1\bin"
$ollamaCudaDir = "C:\Users\wangh\AppData\Local\Programs\Ollama\lib\ollama\cuda_v12"

$cudaLibs = @(
    "nvcuda.dll",
    "nvml.dll"
)

foreach ($lib in $cudaLibs) {
    $source = Join-Path $cudaSourceDir $lib
    if (Test-Path $source) {
        Copy-Item $source -Destination $ollamaCudaDir -Force -ErrorAction SilentlyContinue
        Write-Host "  OK: Copied $lib to Ollama" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: Not found: $source" -ForegroundColor Yellow
    }
}

# Step 4: Configure environment variables
Write-Host "`n[4/5] Configuring environment variables..." -ForegroundColor Yellow

$env:CUDA_HOME = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.1"
$env:CUDA_PATH = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.1"
$env:PATH = "$($env:CUDA_HOME)\bin;$env:PATH"
$env:OLLAMA_DEBUG = "1"
$env:OLLAMA_LLM_LIBRARY = "cuda"

# Clear AMD-related variables
Remove-Item Env:\HIP_VISIBLE_DEVICES -ErrorAction SilentlyContinue
Remove-Item Env:\ROCR_VISIBLE_DEVICES -ErrorAction SilentlyContinue

Write-Host "  OK: CUDA_HOME=$env:CUDA_HOME" -ForegroundColor Green
Write-Host "  OK: OLLAMA_LLM_LIBRARY=$env:OLLAMA_LLM_LIBRARY" -ForegroundColor Green

# Step 5: Start Ollama
Write-Host "`n[5/5] Starting Ollama..." -ForegroundColor Yellow
Write-Host "GPU Info:" -ForegroundColor Cyan
nvidia-smi --query-gpu=index,name,driver_version --format=csv,noheader

Write-Host "`nLaunching Ollama..." -ForegroundColor Green
ollama serve