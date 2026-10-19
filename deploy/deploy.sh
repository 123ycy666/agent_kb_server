#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
#  服务器端部署（幂等，可重复执行）：备份 → 构建 → 换代码 → 起容器 → 健康检查 → 失败回滚
#  用法：bash deploy.sh /root/agentkownlg/merged.tar.gz
#  前提：基础镜像 kb-backend:latest 已存在（磁盘紧张，**禁止拉新镜像**）
# ──────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR=${APP_DIR:-/root/agentkownlg}
SRC=${1:-$APP_DIR/merged.tar.gz}
NEW_DIR=$APP_DIR/merged
BASE_IMAGE=${BASE_IMAGE:-kb-backend:latest}
IMAGE=${IMAGE:-kb-merged:latest}
PREV_IMAGE=${PREV_IMAGE:-kb-merged:prev}
CONTAINER=${CONTAINER:-kb-merged}
PG=${PG:-kb-postgres}
DB=${DB:-ad_new}
NET=${NET:-kb-prod_kbnet}
PORT=${PORT:-8200}
LOG=$APP_DIR/deploy.log

step() { printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG"; }
warn() { printf '  ⚠ %s\n' "$*" | tee -a "$LOG"; }
die()  { printf '\n✗ %s\n' "$*" | tee -a "$LOG"; exit 1; }

health() { curl -fsS -m 5 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"status":"ok"'; }
wait_health() { local i; for i in $(seq 1 30); do health && return 0; sleep 3; done; return 1; }

run_container() {   # $1 = 镜像
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker run -d --name "$CONTAINER" --network "$NET" \
    -p "127.0.0.1:$PORT:$PORT" \
    --env-file "$NEW_DIR/.env.production" \
    -e SECRETARY_ENV=/app/.env \
    -v "$NEW_DIR/.env.production:/app/.env:ro" \
    -v kb_merged_uploads:/data/uploads \
    --restart unless-stopped \
    "$1" python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" \
         --proxy-headers --forwarded-allow-ips='*' >/dev/null
}

# ── 0. 预检 ────────────────────────────────────────────────
step "预检"
[ -f "$SRC" ] || die "找不到部署包：$SRC"
docker image inspect "$BASE_IMAGE" >/dev/null 2>&1 || die "基础镜像 $BASE_IMAGE 不存在（本机磁盘紧张，禁止拉取）"
docker inspect "$PG" >/dev/null 2>&1 || die "PostgreSQL 容器 $PG 不存在"
[ -f "$NEW_DIR/.env.production" ] || die "缺少 $NEW_DIR/.env.production（首次部署请先生成）"
FREE_KB=$(df -P / | awk 'NR==2{print $4}')
if [ "$FREE_KB" -lt 800000 ]; then
  warn "磁盘可用仅 $((FREE_KB/1024))MB，建议先清理"
fi

# ── 1. 备份 ────────────────────────────────────────────────
BACKUP=$APP_DIR/_deploy_bak_$(date +%Y%m%d_%H%M%S)
step "备份 → $BACKUP"
mkdir -p "$BACKUP"; chmod 700 "$BACKUP"
docker exec "$PG" sh -c "pg_dump -U kb -d $DB" | gzip > "$BACKUP/$DB.sql.gz"
cp /etc/nginx/conf.d/kb.conf "$BACKUP/kb.conf"
cp "$NEW_DIR/.env.production" "$BACKUP/env.production.bak"
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker tag "$IMAGE" "$PREV_IMAGE"
fi
echo "  备份完成：$(du -sh "$BACKUP" | cut -f1)"

# ── 2. 换代码（保留 .env.production）────────────────────────
step "解包并切换代码目录"
rm -rf "$NEW_DIR.new" "$NEW_DIR.old"
mkdir -p "$NEW_DIR.new"
tar xzf "$SRC" -C "$NEW_DIR.new"
cp "$BACKUP/env.production.bak" "$NEW_DIR.new/.env.production"
chmod 600 "$NEW_DIR.new/.env.production"
mv "$NEW_DIR" "$NEW_DIR.old"
mv "$NEW_DIR.new" "$NEW_DIR"

# ── 3. 构建（只用现有基础镜像，不联网拉取）──────────────────
step "构建镜像 $IMAGE"
cp "$NEW_DIR/deploy/Dockerfile.merged" "$NEW_DIR/Dockerfile" || die "缺少 deploy/Dockerfile.merged"
cp "$NEW_DIR/deploy/dockerignore.merged" "$NEW_DIR/.dockerignore" 2>/dev/null || true
if ! docker build -q -t "$IMAGE" "$NEW_DIR" >> "$LOG" 2>&1; then
  warn "构建失败，回滚代码目录"
  rm -rf "$NEW_DIR"; mv "$NEW_DIR.old" "$NEW_DIR"
  die "镜像构建失败，详见 $LOG"
fi

# ── 4. 空库补表（幂等）────────────────────────────────────
step "检查数据库结构"
if [ -z "$(docker exec "$PG" psql -U kb -d "$DB" -tAc "select to_regclass('public.t_chat_session')")" ]; then
  echo "  导入 db/chat_tables.sql"
  docker cp "$NEW_DIR/db/chat_tables.sql" "$PG:/tmp/chat.sql"
  docker exec "$PG" psql -U kb -d "$DB" -q -f /tmp/chat.sql >> "$LOG" 2>&1
fi
if [ -z "$(docker exec "$PG" psql -U kb -d "$DB" -tAc "select to_regclass('public.t_nc_typo')")" ]; then
  echo "  导入 db/lexicon.sql（4 万行，稍慢）"
  docker cp "$NEW_DIR/db/lexicon.sql" "$PG:/tmp/lexicon.sql"
  docker exec "$PG" psql -U kb -d "$DB" -q -f /tmp/lexicon.sql >> "$LOG" 2>&1
fi

# ── 5. 起容器 + 健康检查 + 回滚 ────────────────────────────
step "启动新容器"
run_container "$IMAGE"
if wait_health; then
  echo "  健康检查通过"
else
  warn "新版本健康检查失败，回滚到 ${IMAGE}:prev"
  docker logs --tail 40 "$CONTAINER" >> "$LOG" 2>&1 || true
  if docker image inspect "$PREV_IMAGE" >/dev/null 2>&1; then
    rm -rf "$NEW_DIR"; mv "$NEW_DIR.old" "$NEW_DIR"
    run_container "$PREV_IMAGE"
    wait_health && die "已回滚到上一版本（服务可用），请查 $LOG" || die "回滚后仍不健康，需人工介入"
  fi
  die "无上一版本可回滚，请查 $LOG"
fi

# ── 6. nginx 指向 8200（仅在未指向时改，改前后都校验）─────
step "检查 nginx"
if grep -q "127.0.0.1:$PORT" /etc/nginx/conf.d/kb.conf; then
  echo "  已指向 $PORT，跳过"
else
  SN=$(sed -n 's/.*server_name[[:space:]]\+\([^;]*\);.*/\1/p' /etc/nginx/conf.d/kb.conf | head -1)
  sed "s/server_name kb.example.com;/server_name ${SN:-_};/" "$NEW_DIR/deploy/nginx/kb.conf" \
      > /etc/nginx/conf.d/kb.conf
  if nginx -t >> "$LOG" 2>&1; then
    systemctl reload nginx && echo "  已切换到 $PORT"
  else
    cp "$BACKUP/kb.conf" /etc/nginx/conf.d/kb.conf
    die "nginx 配置校验失败，已还原"
  fi
fi

rm -rf "$NEW_DIR.old"
ls -1dt "$APP_DIR"/_deploy_bak_* 2>/dev/null | tail -n +4 | xargs -r rm -rf

# ── 7. 汇总 ───────────────────────────────────────────────
step "完成"
echo "  版本：$(cat "$NEW_DIR/.deploy_version" 2>/dev/null || echo '未知')"
echo "  镜像：$(docker image inspect "$IMAGE" --format '{{.Id}}' | cut -c1-19)"
echo "  容器：$(docker ps --filter "name=$CONTAINER" --format '{{.Status}}')"
echo "  磁盘：$(df -h / | awk 'NR==2{print $4" 可用 / "$5" 已用"}')"
echo "  备份：$BACKUP"
echo "  日志：$LOG"
