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

# ── 站点参数（2026-11-19 参数化：同一份脚本发第二套时不再改第一套）──
# NGINX_CONF     本次要改的 nginx 配置文件（**只改这一份**）
# NGINX_TEMPLATE 用哪个模板（根路径站点 / 带前缀站点）
# SITE_PREFIX    站点前缀，如 sandbox；为空 = 根路径站点
# （健康检查固定打容器内的 /health：后端不认识站点前缀，打 /sandbox/health 会命中 SPA 兜底）
# SITE_CONFIG    引导配置（现场库连接 + 初始管理员），会被挂进容器
# VOL_PREFIX     数据卷名前缀：两套实例的 uploads 必须分开
NGINX_CONF=${NGINX_CONF:-/etc/nginx/conf.d/kb.conf}
SITE_PREFIX=${SITE_PREFIX:-}
NGINX_TEMPLATE=${NGINX_TEMPLATE:-$NEW_DIR/deploy/nginx/kb.conf}
SITE_CONFIG=${SITE_CONFIG:-$APP_DIR/config/site.json}
VOL_PREFIX=${VOL_PREFIX:-kb_merged}
NGINX_BACKUP=$(basename "$NGINX_CONF")

step() { printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG"; }
warn() { printf '  ⚠ %s\n' "$*" | tee -a "$LOG"; }
die()  { printf '\n✗ %s\n' "$*" | tee -a "$LOG"; exit 1; }

# ⚠️ 探的是**容器内**的 /health（后端没有前缀路由）；站点是否可达由 nginx 那步之后自测。
health() { curl -fsS -m 5 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"status":"ok"'; }
wait_health() { local i; for i in $(seq 1 30); do health && return 0; sleep 3; done; return 1; }

run_container() {   # $1 = 镜像
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  local mount_site=()
  if [ -f "$SITE_CONFIG" ]; then
    # 挂**目录**且可写：容器里页面「保存并重连」要能把新配置写回宿主机（:ro 会 EBUSY）
    mount_site=(-v "$(dirname "$SITE_CONFIG"):/app/config" -e SITE_CONFIG_FILE=/app/config/site.json)
  else
    warn "没有引导配置 $SITE_CONFIG：容器起来后只会提供「现场配置」页"
  fi
  docker run -d --name "$CONTAINER" --network "$NET" \
    -p "127.0.0.1:$PORT:$PORT" \
    --env-file "$NEW_DIR/.env.production" \
    -e SECRETARY_ENV=/app/.env \
    -v "$NEW_DIR/.env.production:/app/.env:ro" \
    "${mount_site[@]}" \
    -v "${VOL_PREFIX}_uploads:/data/uploads" \
    -e DISPATCH_FILES_DIR=/data/uploads/dispatch \
    --restart unless-stopped \
    "$1" python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" \
         --proxy-headers --forwarded-allow-ips='*' >/dev/null
}

# ── 0. 预检 ────────────────────────────────────────────────
step "预检"
# 防呆：APP_DIR 或 SITE_PREFIX 已经是「第二套」，但库/容器/卷还是默认值 →
# 往下会 pg_dump 并往**第一套的库**里灌表，属最危险的误操作。宁可停下。
if [ -n "$SITE_PREFIX" ] || [ "$APP_DIR" != "/root/agentkownlg" ]; then
  for v in DB CONTAINER VOL_PREFIX IMAGE PREV_IMAGE; do
    val=$(eval echo \$$v)
    case "$v:$val" in
      DB:ad_new|CONTAINER:kb-merged|VOL_PREFIX:kb_merged|IMAGE:kb-merged:latest|PREV_IMAGE:kb-merged:prev)
        die "第二套实例仍用第一套的默认值 $v=$val，会动到随州的库/容器。请显式指定 $v=…"
        ;;
    esac
  done
fi
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
cp "$NGINX_CONF" "$BACKUP/$NGINX_BACKUP" 2>/dev/null || true
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

# ── 4.5 预警与任务流转 10 张表（幂等；每次部署都跑，保证索引/约束到位）──
if [ -f "$NEW_DIR/db/dispatch_tables.sql" ]; then
  echo "  导入 db/dispatch_tables.sql（幂等）"
  docker cp "$NEW_DIR/db/dispatch_tables.sql" "$PG:/tmp/dispatch.sql"
  docker exec "$PG" psql -U kb -d "$DB" -q -v ON_ERROR_STOP=1 -f /tmp/dispatch.sql >> "$LOG" 2>&1 \
    || warn "dispatch 表导入失败（新接口会 503，检查 $LOG）"
fi

# ── 4.6 提示词材料（2026-11-01；规则从知识库搬出来进这两张表）──
# ⚠️ 2026-11-02 改：**只在空表时导入**。db/prompt_materials.sql 开头是
#    `DELETE FROM prompt_material WHERE slot IN (...)` 再灌 53 条初始版本 ——
#    每次部署都跑，等于把页面上改过的正文、必带开关、补全表**全部重置**。
#    页面能改的东西必须扛得住一次部署，所以改成"首次初始化才灌"。
if [ -f "$NEW_DIR/db/prompt_materials.sql" ]; then
  ROWS=$(docker exec "$PG" psql -U kb -d "$DB" -tAc \
    "select count(*) from prompt_material" 2>/dev/null | tr -d ' \r\n' || echo '')
  if [ "$ROWS" = "0" ]; then
    echo "  导入 db/prompt_materials.sql（表是空的，首次初始化）"
    docker cp "$NEW_DIR/db/prompt_materials.sql" "$PG:/tmp/prompt_materials.sql"
    docker exec "$PG" psql -U kb -d "$DB" -q -v ON_ERROR_STOP=1 -f /tmp/prompt_materials.sql >> "$LOG" 2>&1 \
      || warn "prompt_material 导入失败（页面会空，检查 $LOG）"
  else
    echo "  跳过 db/prompt_materials.sql：库里已有 ${ROWS:-?} 行，保留页面上的改动"
  fi
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

# ── 6. nginx 指向本实例端口（只改 NGINX_CONF 这一份）────────
step "检查 nginx（$NGINX_CONF）"
if [ -n "$SITE_PREFIX" ]; then
  # 带前缀站点：剥前缀 + 结尾带斜杠 + ^~（防止被静态资源正则抢走）
  if grep -q "location ^~ /$SITE_PREFIX/" "$NGINX_CONF"; then
    echo "  已有 /$SITE_PREFIX/ 段，跳过"
  else
    [ -f "$NGINX_TEMPLATE" ] || die "缺少模板 $NGINX_TEMPLATE"
    sed -e "s/__PREFIX__/$SITE_PREFIX/g" -e "s/__PORT__/$PORT/g" "$NGINX_TEMPLATE" >> "$NGINX_CONF"
    if nginx -t >> "$LOG" 2>&1; then
      systemctl reload nginx && echo "  已加入 /$SITE_PREFIX/ → $PORT"
    else
      cp "$BACKUP/$NGINX_BACKUP" "$NGINX_CONF"
      die "nginx 配置校验失败，已还原"
    fi
  fi
else
  if grep -q "127.0.0.1:$PORT" "$NGINX_CONF"; then
    echo "  已指向 $PORT，跳过"
  else
    SN=$(sed -n 's/.*server_name[[:space:]]\+\([^;]*\);.*/\1/p' "$NGINX_CONF" | head -1)
    sed -e "s/server_name kb.example.com;/server_name ${SN:-_};/" \
        -e "s/127.0.0.1:8200/127.0.0.1:$PORT/g" "$NGINX_TEMPLATE" > "$NGINX_CONF"
    if nginx -t >> "$LOG" 2>&1; then
      systemctl reload nginx && echo "  已切换到 $PORT"
    else
      cp "$BACKUP/$NGINX_BACKUP" "$NGINX_CONF"
      die "nginx 配置校验失败，已还原"
    fi
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
