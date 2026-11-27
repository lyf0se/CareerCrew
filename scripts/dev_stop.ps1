<#
停止 dev_start.ps1 启动的全部内容：
1. 按 data/dev_servers.json 记录的 PID 结束后端 / 前端进程树（taskkill /T
   连同 uvicorn 的 python 子进程、vite 的 node 子进程一并结束）
2. docker compose stop postgres qdrant —— 只停容器，数据卷保留
   （需要彻底清理容器时手动执行 docker compose down，down -v 会删数据）
#>
$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$runState = Join-Path $RepoRoot 'data\dev_servers.json'
if (Test-Path $runState) {
    $state = Get-Content $runState -Raw | ConvertFrom-Json
    foreach ($name in @('backend', 'frontend')) {
        $procId = $state.$name
        if ($procId) {
            # 进程可能已手动关闭，taskkill 报错时忽略
            $null = taskkill /PID $procId /T /F 2>$null
            Write-Host "已结束 $name（PID $procId）"
        }
    }
    Remove-Item $runState -Force
} else {
    Write-Host '未找到运行记录（data/dev_servers.json），跳过进程清理' -ForegroundColor Yellow
}

Write-Host '停止 postgres / qdrant 容器（数据保留在卷中）...'
docker compose stop postgres qdrant
