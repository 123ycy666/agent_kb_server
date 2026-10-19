#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
#  一键启动（Linux / macOS）：先确保 PostgreSQL 在跑，再起后端
#
#  用法：
#      chmod +x start_all.sh
#      ./start_all.sh
#
#  说明：
#   · 只用 Docker 拉起 **postgres 一个服务**（Redis/Celery 已移除，不需要）
#   · 若内网用的是自己的 PostgreSQL（不走 Docker），设 SKIP_DOCKER=1：
#         SKIP_DOCKER=1 ./start_all.sh
# ──────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [ "${SKIP_DOCKER:-0}" != "1" ]; then
    if command -v docker >/dev/null 2>&1; then
        echo "[1/2] 拉起 PostgreSQL 容器 ..."
        # 只起 postgres，避免把无关服务一起带起来
        docker compose up -d postgres || echo "[警告] docker compose 失败，继续尝试直连已有数据库"
    else
        echo "[1/2] 未安装 docker，跳过（假定已有 PostgreSQL）"
    fi
else
    echo "[1/2] SKIP_DOCKER=1，跳过容器启动"
fi

echo "[2/2] 启动后端 ..."
exec "$ROOT/start_backend.sh"
