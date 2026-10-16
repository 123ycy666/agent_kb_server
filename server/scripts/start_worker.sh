#!/usr/bin/env bash
# 启动 Celery worker（文档入库异步执行引擎）
# 复用 kb-redis:6380 作为 broker/backend；任务状态仍落 DB（AsyncTask），前端轮询契约不变。
# Windows 下 Celery 不支持 prefork，必须用 -P threads（或 solo）。
set -euo pipefail
cd "$(dirname "$0")/.."
PY="/c/Users/易澄宇/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
exec "$PY" -m celery -A app.celery_app.celery worker --loglevel=info --queues=doc_ingest -P threads
