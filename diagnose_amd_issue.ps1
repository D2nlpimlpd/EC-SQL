# diagnose_amd_issue.ps1

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "=== Ollama AMD/NVIDIA 冲突诊断 ===" -ForegroundColor Green

# 1. 检查 GPU 设备
Write-Host "`n1️⃣  检查 GPU 设备:" -ForegroundColor Yellow
$gpuInfo = nvidia-smi --query-gpu=index,name --format=csv,noheader
Write-Host "检测到的 NVIDIA GPU:"
$gpuInfo

# 2. 检查 AMD GPU (如果有)
Write-Host "`n2️⃣  检查 AMD GPU:" -ForegroundColor Yellow
$amdDevices = Get-PnpDevice -Status OK | Where-Object { $_.Description -like "*AMD*" -and $_.Description -like "*GPU*" }
if ($amdDevices) {
    Write-Host "⚠️  检测到 AMD GPU:"
    $amdDevices | ForEach-Object { Write-Host "  - $($_.Description)" }
    Write-Host "  这是导致 Ollama 回退到 CPU 的根本原因!" -ForegroundColor Red
} else {
    Write-Host "✓ 未检测到 AMD GPU"
}

# 3. 检查 ROCm 库
Write-Host "`n3️⃣  检查 ROCm:" -ForegroundColor Yellow
if (Test-Path "C:\Program Files\AMD\ROCm") {
    Write-Host "✓ ROCm 已安装"
} else {
    Write-Host "✗ ROCm 未安装 (这会导致 Ollama 回退到 CPU)" -ForegroundColor Red
}

# 4. 检查环境变量
Write-Host "`n4️⃣  当前环境变量:" -ForegroundColor Yellow
Write-Host "  OLLAMA_LLM_LIBRARY: $env:OLLAMA_LLM_LIBRARY"
Write-Host "  GGML_LLM_LIBRARY: $env:GGML_LLM_LIBRARY"
Write-Host "  HIP_VISIBLE_DEVICES: $env:HIP_VISIBLE_DEVICES"
Write-Host "  ROCR_VISIBLE_DEVICES: $env:ROCR_VISIBLE_DEVICES"
Write-Host "  CUDA_VISIBLE_DEVICES: $env:CUDA_VISIBLE_DEVICES"

# 5. 检查 Ollama 启动日志
Write-Host "`n5️⃣  启动 Ollama 并检查日志..." -ForegroundColor Yellow
Write-Host "请运行: ollama serve" -ForegroundColor Cyan
Write-Host "查找这些关键词:" -ForegroundColor Cyan
Write-Host "  ✓ 'ggml_cuda_init: found' = CUDA 工作正常" -ForegroundColor Green
Write-Host "  ✗ 'unable to verify rocm library' = AMD GPU 导致回退" -ForegroundColor Red
Write-Host "  ✗ 'load_tensors: CPU model buffer' = 模型运行在 CPU" -ForegroundColor Red

Write-Host "`n📋 建议:" -ForegroundColor Cyan
Write-Host "1. 方案 A: 在 BIOS 中禁用 AMD 集成显卡(最推荐)"
Write-Host "2. 方案 B: 设置环境变量 OLLAMA_LLM_LIBRARY=cuda"
Write-Host "3. 方案 C: 安装 ROCm 库以支持 AMD GPU"