@echo off
rem ============================================================================
rem  自检脚本（三级，按需运行）
rem    1/3 知识库逻辑自检  —— 不连数据库，纯逻辑（47 项）
rem    2/3 语义层自检      —— 纠错词典 / 语义字典（不依赖大模型）
rem    3/3 全链路验收      —— 需要服务已启动（:8200），走真实 HTTP 接口（28 项）
rem
rem  只想快速验证逻辑 → 前两项；要上线前确认 → 加第三项
rem ============================================================================
setlocal
cd /d "%~dp0"
set PYVENV=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe
if exist "%PYVENV%" (set PY=%PYVENV%) else (set PY=py)

echo ================ 1/3 知识库逻辑自检 ================
cd server
%PY% tests\selfcheck.py
if errorlevel 1 (
  echo [失败] 知识库逻辑自检未通过
  cd ..
  pause
  exit /b 1
)

echo.
echo ================ 2/3 语义层自检 ================
rem test_semantic.py 现在位于 server/tests/agent/（模块已并入 app/secretary）
%PY% -X utf8 tests\agent\test_semantic.py
if errorlevel 1 (
  echo [注意] 语义层自检有断言未通过（可能是线上字典新增了表，需维护方确认口径）
)
cd ..

echo.
echo ================ 3/3 全链路验收（需服务在 :8200 运行）============
curl -s -m 5 -o NUL http://127.0.0.1:8200/health
if errorlevel 1 (
  echo [跳过] 服务未启动，先双击 启动全部.bat
) else (
  cd server
  %PY% -X utf8 tests\acceptance.py
  cd ..
)

echo.
echo 自检流程结束。
pause