@echo off
chcp 65001 >nul
rem CareerCrew 一键停止：结束后端/前端进程树并停 postgres/qdrant 容器（数据保留）。
rem IDEA 中右键本文件选 Run，或资源管理器双击均可。
where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev_stop.ps1"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev_stop.ps1"
)
echo.
pause
