<#
CareerCrew 一键开发启动：依赖容器（postgres + qdrant）→ 后端 uvicorn → 前端 Vite。
用法：
  powershell -ExecutionPolicy Bypass -File scripts\dev_start.ps1
可选参数：
  -Chrome   顺带启动带 CDP 调试端口的 Chrome（Boss直聘/猎聘实时抓取需要）
  -NoSync   跳过 uv sync（假定 .venv 已按 uv.lock 就绪）
  -NoDocker 跳过 postgres/qdrant 容器（假定已在运行）
停止全部：powershell -ExecutionPolicy Bypass -File scripts\dev_stop.ps1
#>
[CmdletBinding()]
param(
    [switch]$Chrome,
    [switch]$NoSync,
    [switch]$NoDocker
)

$ErrorActionPreference = 'Stop'
# 仓库根 = 脚本所在目录的上一级；含空格路径，后续拼接统一走变量
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Write-Step { param($Msg) Write-Host "==> $Msg" -ForegroundColor Cyan }
function Write-Done { param($Msg) Write-Host "    $Msg" -ForegroundColor Green }

# ── 1. 前置检查：工具链与 .env ──
foreach ($tool in @('uv', 'docker', 'npm')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "未找到 $tool，请先安装（uv 见 https://docs.astral.sh/uv/）"
    }
}
if (-not (Test-Path .env)) {
    throw '缺少 .env：请先复制 .env.example 为 .env 并填写 API key 与密钥'
}

# ── 1.5 端口预检：8100（后端）/ 5276（前端）被占用时提前报错并指出占用者 ──
foreach ($need in @(
    @{ Port = 8100; Label = '后端 uvicorn' },
    @{ Port = 5276; Label = '前端 vite' }
)) {
    $conn = Get-NetTCPConnection -LocalPort $need.Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        $owner = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        $ownerName = if ($owner) { $owner.ProcessName } else { '未知' }
        throw "$($need.Label) 需要端口 $($need.Port)，已被 PID $($conn.OwningProcess)（$ownerName）占用。若占用者是其他项目的 dev server，请先关闭它再重试本脚本。"
    }
}

# ── 2. Docker Desktop 未运行时尝试拉起（等待就绪，超时中止）──
if (-not $NoDocker) {
    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Step 'Docker Desktop 未运行，尝试启动...'
        $dockerDesktop = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
        if (-not (Test-Path $dockerDesktop)) {
            throw '未找到 Docker Desktop，请手动启动后重试'
        }
        Start-Process $dockerDesktop
        $deadline = (Get-Date).AddSeconds(120)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 5
            docker info *> $null
            if ($LASTEXITCODE -eq 0) { break }
        }
        docker info *> $null
        if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop 120 秒内未就绪，请手动启动后重试' }
        Write-Done 'Docker Desktop 已就绪'
    }
}

# ── 3. Python 环境（uv，依赖锁定于 uv.lock）──
if (-not $NoSync) {
    Write-Step '同步 uv 环境（uv sync --all-extras）...'
    uv sync --all-extras
    if ($LASTEXITCODE -ne 0) { throw "uv sync 失败（exit $LASTEXITCODE）" }
    Write-Done 'Python 环境就绪'
}

# ── 4. 依赖容器：postgres + qdrant，等待 healthcheck 通过 ──
if (-not $NoDocker) {
    Write-Step '启动 postgres / qdrant 容器...'
    docker compose up -d postgres qdrant
    if ($LASTEXITCODE -ne 0) { throw "docker compose 启动失败（exit $LASTEXITCODE）" }
    foreach ($container in @('careercrew-postgres', 'careercrew-qdrant')) {
        $status = $null
        $deadline = (Get-Date).AddSeconds(60)
        while ((Get-Date) -lt $deadline) {
            $status = docker inspect --format '{{.State.Health.Status}}' $container 2>$null
            if ($status -eq 'healthy') { break }
            Start-Sleep -Seconds 2
        }
        if ($status -ne 'healthy') { throw "容器 $container 60 秒内未就绪（当前：$status）" }
        Write-Done "$container healthy"
    }
}

# ── 5. 后端 uvicorn：独立窗口运行，便于看日志 / Ctrl+C ──
Write-Step '启动后端 uvicorn（:8100，首次惰性加载模型较慢）...'
$backend = Start-Process pwsh -PassThru `
    -ArgumentList '-NoExit', '-Command',
    "`$Host.UI.RawUI.WindowTitle = 'CareerCrew 后端 uvicorn'; Set-Location '$RepoRoot'; uv run uvicorn careercrew_api.main:app --reload --port 8100"

# ── 6. 前端 Vite：独立窗口运行 ──
Write-Step '启动前端 Vite dev server（:5276，strictPort）...'
$frontend = Start-Process pwsh -PassThru `
    -ArgumentList '-NoExit', '-Command',
    "`$Host.UI.RawUI.WindowTitle = 'CareerCrew 前端 vite'; Set-Location '$RepoRoot\careercrew_web'; npm run dev"

# ── 7. 可选：CDP 调试 Chrome（Boss直聘/猎聘抓取通道）──
if ($Chrome) {
    Write-Step '启动 CDP 调试 Chrome...'
    & (Join-Path $PSScriptRoot 'start_chrome_cdp.ps1')
}

# ── 8. 记录 PID 供 dev_stop.ps1 结束进程树 ──
$runState = Join-Path $RepoRoot 'data\dev_servers.json'
New-Item -ItemType Directory -Force (Split-Path $runState) *> $null
@{
    backend  = $backend.Id
    frontend = $frontend.Id
    started  = (Get-Date).ToString('s')
} | ConvertTo-Json | Set-Content -Encoding UTF8 $runState

Write-Host ''
Write-Host '全部已启动：' -ForegroundColor Green
Write-Host '  前端        http://localhost:5276'
Write-Host '  后端 API    http://localhost:8100（/docs 为 Swagger UI）'
Write-Host '  就绪探针    http://localhost:8100/readyz'
Write-Host '  Qdrant 控制台 http://localhost:6333/dashboard'
Write-Host ''
Write-Host '停止全部：powershell -ExecutionPolicy Bypass -File scripts\dev_stop.ps1'
