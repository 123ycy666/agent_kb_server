@echo off
rem ============================================================
rem  前端【开发模式】热更新服务（:5173）
rem
rem  ⚠️ 日常使用不需要这个脚本！
rem     统一后端 :8200 已经自带前端托管，浏览器直接访问
rem         http://127.0.0.1:8200
rem     即可，界面与这里完全一样。
rem
rem  只有需要「改前端代码即时热更新」时才用本脚本，
rem  此时浏览器访问 http://127.0.0.1:5173（vite 会把 /api 代理到 :8200）。
rem  使用前提：统一后端 :8200 必须已在运行。
rem ============================================================
setlocal
cd /d "%~dp0\web"

if not exist node_modules (
  echo [?] 未检测到依赖，正在安装（首次约 1 分钟）...
  call npm install --no-audit --no-fund
  if errorlevel 1 (
    echo [错误] 依赖安装失败。日常使用请改用 启动全部.bat，直接访问 :8200。
    pause
    exit /b 1
  )
)

echo.
echo   前端开发服务 :5173 启动中（仅开发热更新用）
echo   ★ 日常使用请访问统一入口： http://127.0.0.1:8200
echo.
call npm run dev