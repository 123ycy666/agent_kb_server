@echo off
rem 统一后端单独启动（:8200 = 知识库 + 问数智能体 + 前端托管）。
rem 前置：先启动数据库 docker compose up -d
rem
rem ⚠️ 必须在 server/ 目录下启动（app 包在那里），本脚本已 cd 到位。
setlocal
cd /d "%~dp0\server"
set PYVENV=C:\Users\易澄宇\.workbuddy\binaries\python\envs\default\Scripts\python.exe
if exist "%PYVENV%" (set PY=%PYVENV%) else (set PY=py)
echo 启动统一后端 :8200 （首次约 40s：建表 + 同步 t_employee 员工投影 + 词典加载）
echo.
echo   ★ 浏览器访问：http://127.0.0.1:8200
echo     （知识库界面 / 智能问答 / API 文档 全在这一个地址）
echo.
%PY% -m uvicorn app.main:app --host 0.0.0.0 --port 8200