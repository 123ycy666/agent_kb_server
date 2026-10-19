@echo off
rem ============================================================
rem  One-click environment preflight (deploy readiness check)
rem  Runs server\scripts\preflight_check.py, which verifies:
rem    env/config, PostgreSQL+pgvector, t_nc_* lexicon,
rem    MySQL dlj_data (login), MySQL ai_data (semantics),
rem    LLM, Embedding (dimension), dist, upload dir, disk, port
rem  Exit code 0 = ready, 1 = has FAIL items.
rem  NOTE: keep this file pure ASCII (cmd reads .bat as GBK).
rem ============================================================
setlocal
cd /d "%~dp0"

set "PYV=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PYV%" (
    echo [ERROR] project venv not found:
    echo         %PYV%
    echo         Fix the path in this script, then retry.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo    Environment preflight - deploy readiness
echo ============================================================
pushd "%~dp0server"
"%PYV%" -X utf8 scripts\preflight_check.py
set "RC=%ERRORLEVEL%"
popd

echo.
if "%RC%"=="0" (
    echo [RESULT] OK - environment is ready.
) else (
    echo [RESULT] FAILED - fix the items marked [X] above.
)
echo.
echo Tip: after startup, verify with:
echo      curl http://127.0.0.1:8200/health
echo.
pause
exit /b %RC%
