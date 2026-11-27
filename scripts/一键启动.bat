@echo off
rem CareerCrew 一键启动：依赖容器 + 后端 uvicorn(:8100) + 前端 Vite(:5276)
rem IDEA 中右键本文件选 Run，或资源管理器双击均可。
rem 优先使用 PowerShell 7（pwsh），未安装时回退 Windows 自带的 powershell。
where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev_start.ps1"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev_start.ps1"
)
echo.
pause
