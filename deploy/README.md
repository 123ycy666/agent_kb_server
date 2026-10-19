# 一键部署（本机 → 云服务器）

三步，全程幂等，可重复执行：

```bash
# 1) 本机打包（语法自检 + 排除无关文件）
bash deploy/pack.sh

# 2) 推到服务器并执行部署（密码或密钥登录二选一）
python deploy/push.py --tar dist/merged.tar.gz --host 47.121.183.81 --user root
#   密码来源优先级：--password > --password-file > 环境变量 DEPLOY_PASSWORD > 交互输入
#   用密钥：加 --key-file ~/.ssh/id_rsa

# 3) 验收
curl -s http://47.121.183.81/health
```

只上传不部署：加 `--no-deploy`。

## 服务器端会做什么（`deploy/deploy.sh`）

1. **预检**：部署包/基础镜像/PG 容器/env 文件是否就绪；磁盘不足给警告
2. **备份**：`pg_dump` + `nginx kb.conf` + `.env.production` → `/root/agentkownlg/_deploy_bak_<时间>/`
3. **换代码**：解到 `merged.new`，保留 `.env.production`，再原子切换目录（失败可回退）
4. **构建镜像**：`deploy/Dockerfile.merged` → `kb-merged:latest`，**只用现有基础镜像，不联网拉取**
5. **补表**：`t_chat_session` / `t_nc_typo` 不存在时才导入 `db/chat_tables.sql`、`db/lexicon.sql`
6. **起容器 + 健康检查**（最多 90s）；失败自动回滚到 `kb-merged:prev` 并恢复旧代码目录
7. **nginx**：只在新配置没指向 8200 时才改，改完先 `nginx -t`，失败自动还原
8. **汇总**：版本戳（`.deploy_version`）、镜像 ID、容器状态、磁盘、备份路径

## 硬约束（别改）

| 约束 | 原因 |
|---|---|
| `FROM kb-backend:latest` 不能换 | 磁盘只剩 ~2.2G，拉 `python:3.11-slim` 会直接撑爆 |
| 不能 `docker compose down` 整个 kb-prod | 会把 `kb-postgres` 一起停掉（数据库在里面） |
| `.env.production` 不重新生成 | 重新生成会轮换 `INTERNAL_SERVICE_TOKEN`，打断 sga 的对接 |
| 备份只留最近 3 份 | 磁盘紧张，脚本自动轮转 `_deploy_bak_*`（不动手工 `_bak_*`） |

## 失败怎么办

- 看 `/root/agentkownlg/deploy.log`
- 脚本回滚后服务仍是上一版本，可直接重跑
- 手工回滚：`docker rm -f kb-merged && docker run ... kb-merged:prev`（参数见 `deploy.sh` 的 `run_container`）
- 数据库回滚：`gunzip -c _deploy_bak_*/ad_new.sql.gz | docker exec -i kb-postgres psql -U kb -d ad_new`

## 首次部署（新机器）

本脚本假设这些**已存在**：Docker、`kb-backend:latest` 基础镜像、`kb-postgres` 容器（库 `ad_new`）、
`/root/agentkownlg/merged/.env.production`。全新机器需先按 `docs/Linux部署说明.md` 准备，
再用本脚本做日常发布。
