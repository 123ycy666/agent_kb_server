#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
#  本机打包：语法自检 → 产出可上传的 merged.tar.gz
#  用法：bash deploy/pack.sh [输出路径]
#  依赖：bash + tar（Git Bash / WSL / Linux 均可）
# ──────────────────────────────────────────────────────────────
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:-$ROOT/dist/merged.tar.gz}
PY=${PY:-python}

cd "$ROOT"

echo "[1/4] 语法自检"
"$PY" -m compileall -q server/app > /dev/null || { echo "✗ python 语法错误，已中止"; exit 1; }

echo "[2/4] 必需文件检查"
[ -f web/dist/index.html ] || { echo "✗ 缺少 web/dist/index.html（先 npm run build）"; exit 1; }
[ -f server/app/main.py ]  || { echo "✗ 缺少 server/app/main.py"; exit 1; }
[ -f db/lexicon.sql ]      || echo "  ⚠ 缺 db/lexicon.sql（新库将没有纠错词典）"

echo "[3/4] 打包"
VC=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
printf '%s %s
' "$VC" "$(date +%F' '%T)" > "$ROOT/.deploy_version"
mkdir -p "$(dirname "$OUT")"
tar czf "$OUT" \
  --exclude=.git \
  --exclude=.workbuddy \
  --exclude=./dist \
  --exclude=node_modules \
  --exclude=__pycache__ \
  --exclude='*.pyc' \
  --exclude=./data \
  --exclude=./.env \
  --exclude=./.env.local \
  --exclude=./server/tests/output \
  .

echo "[4/4] 完成"
echo "  包：$OUT"
echo "  大小：$(du -h "$OUT" | cut -f1)"
echo
echo "下一步："
echo "  export DEPLOY_HOST=<服务器IP或域名>"
echo "  python deploy/push.py --tar \"$OUT\" --host \$DEPLOY_HOST --user root"
