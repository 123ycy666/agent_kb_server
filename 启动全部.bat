@echo off
rem ============================================================
rem  统一知识库与智能问数平台 - 一键启动
rem
rem  ★ 合并后只有一个服务、一个端口：全部功能都在 :8200
rem    （知识库界面 + 智能问答 + 后端 API，前端由后端直接托管）
rem  ★ 浏览器只访问一个地址： http://127.0.0.1:8200
rem    —— 不需要、也不要再开 5173（那只是开发热更新用的）
rem
rem  ⚠️ 关键：后端必须在 server/ 目录下启动（app 包在那里），
rem     本脚本用 pushd 切目录后再拉起，不要手改这一处。
rem ============================================================
setlocal
cd /d "%~dp0"

rem ── Python 解释器：优先 managed venv（含全套依赖），退回系统 py ──
set PYVENV=C:\Users\易澄宇\.workbuddy\binaries\python\envs\default\Scripts\python.exe
if exist "%PYVENV%" (set PY=%PYVENV%) else (set PY=py)

rem ── 0. 配置检查 ────────────────────────────────────────────
if not exist ".env" (
  echo [错误] 缺少 .env 配置文件。请先执行： copy .env.example .env
  echo        然后填写 MySQL 密码、模型通道等配置。
  pause
  exit /b 1
)

rem ── 1. 基础设施（PostgreSQL 5433）────────────────────────
echo [1/2] 启动数据库容器（docker compose up -d）...
docker compose up -d
if errorlevel 1 (
  echo [警告] docker 启动失败。请确认 Docker Desktop 已运行。
  echo        后端连不上 PostgreSQL 时会在启动日志报错。
)

rem ── 2. 统一后端（:8200 = 知识库 + 问数智能体 + 前端托管）────
rem  ★ 必须先切到 server/ 目录：app 包在那里，项目根直接跑会
rem    报 ModuleNotFoundError: No module named 'app'（后端根本起不来）。
echo [2/2] 启动统一后端 :8200 ...
pushd "%~dp0server"
start "统一平台 :8200" cmd /k %PY% -m uvicorn app.main:app --host 0.0.0.0 --port 8200
popd

echo.
echo 服务已拉起，请稍等 20~40 秒（首次启动要初始化数据库和词典）。
echo.
echo   ★ 浏览器打开这个地址即可（唯一入口）：
echo        http://127.0.0.1:8200
echo.
echo   其他可用地址：
echo        http://localhost:8200
echo        http://192.168.0.149:8200    （局域网内其他设备访问本机）
echo.
echo   登录请使用员工账号（人力资源系统 t_employee 的 login_name 与密码）。
echo   本机测试账号： kbadmin / Kb@123456
echo.
echo   若页面空白：按 Ctrl+Shift+R 强制刷新（清掉旧缓存）。
pause