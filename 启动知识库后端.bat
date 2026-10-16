@echo off
rem 统一后端单独启动（:8200 = 知识库 + 问数智能体 + 前端托管）。
rem 前置：先启动数据库 docker compose up -d
rem
rem ⚠️ 必须在 server/ 目录下启动（app 包在那里），本脚本已 cd 到位。
setlocal
cd /d "%~dp0\server"
rem FIX(2026-10-16): the path above was hardcoded with the Chinese account name.
rem This file is UTF-8 but cmd reads .bat as GBK, so 'if exist' never matched,
rem silently fell back to "py" (system Python, no project deps) and died with
rem ModuleNotFoundError: No module named 'sqlalchemy'.
rem %USERPROFILE% expands natively, immune to the encoding mismatch.
set PYVENV=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe
if exist "%PYVENV%" (
    set PY=%PYVENV%
) else (
    echo [ERROR] project venv not found: %PYVENV%
    echo         Create it or fix the path in this script, then retry.
    pause
    exit /b 1
)
echo 启动统一后端 :8200 （首次约 40s：建表 + 同步 t_employee 员工投影 + 词典加载）
echo.
echo   ★ 浏览器访问：http://127.0.0.1:8200
echo     （知识库界面 / 智能问答 / API 文档 全在这一个地址）
echo.
rem === 0. Idempotent start: stop whatever holds :8200 ===
rem WARNING: kill ONLY the PID that owns :8200.
rem          NEVER run taskkill /IM python.exe - that kills every Python on this box.
set "STOPPED="
for /f "tokens=5" %%i in ('netstat -ano ^| findstr ":8200" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%i >nul 2>&1 && set "STOPPED=%%i"
)
if defined STOPPED echo   [i] stopped old backend PID=%STOPPED% that was holding :8200
echo.
%PY% -m uvicorn app.main:app --host 0.0.0.0 --port 8200