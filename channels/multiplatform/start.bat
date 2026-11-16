@echo off
chcp 65001 >nul
title 多平台同步桥
cd /d "%~dp0"

set PY=python
where %PY% >nul 2>nul
if errorlevel 1 (
    set PY=py
    where %PY% >nul 2>nul
)
if errorlevel 1 (
    echo [错误] 未检测到 Python（python / py 均不存在），请先安装 Python 3
    pause
    exit /b 1
)

echo 正在启动多平台同步桥...
echo 启动成功后本窗口需保持运行，请勿关闭
echo 本地看板: http://127.0.0.1:8390
echo.
%PY% mcp_bridge.py serve
set RC=%errorlevel%
echo.
if not "%RC%"=="0" (
    echo ========================================
    echo 桥已退出（代码 %RC%）。错误日志（startup_error.log）：
    type startup_error.log 2>nul
)
pause
