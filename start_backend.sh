#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
#  启动统一后端（Linux / macOS）
#
#  用法：
#      chmod +x start_backend.sh
#      ./start_backend.sh                 # 默认 0.0.0.0:8200
#      PORT=8300 ./start_backend.sh       # 换端口
#      HOST=127.0.0.1 ./start_backend.sh  # 只本机可访问
#
#  说明：
#   · 优先用项目根目录下的 .venv；没有就用 $PYTHON 或 python3
#   · .env 由 app/secretary/config.py 从**项目根目录**读取，与当前工作目录无关
#   · 首次启动会自动建表 / 装 pgvector 扩展 / 从 t_employee 同步管理员（幂等）
# ──────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT/server"

if [ -x "$ROOT/.venv/bin/python" ]; then
    PY="$ROOT/.venv/bin/python"
elif command -v "${PYTHON:-}" >/dev/null 2>&1 && [ -n "${PYTHON:-}" ]; then
    PY="$PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    PY="$(command -v python3)"
else
    echo "[错误] 找不到 python3。请先装 Python 3.12，或用 PYTHON=/path/to/python 指定。" >&2
    exit 1
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8200}"

echo "[start] 目录   : $ROOT"
echo "[start] Python : $PY ($("$PY" -V 2>&1))"
echo "[start] 监听   : $HOST:$PORT"
echo "[start] 访问   : http://<本机IP>:$PORT"
echo "──────────────────────────────────────────────"

# 生产环境不要加 --reload（会重启进程）；也不要加 --workers（本系统设计为单进程）
exec "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
