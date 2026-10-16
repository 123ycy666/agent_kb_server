# 推送到 GitHub —— 操作方案

> **目标仓库**：https://github.com/123ycy666/agent_kb_server.git
> 更新：2026-10-16

## 一、仓库定位

本仓库 = 「统一知识库与智能问数平台」（**合并版 v2**）：
本地知识库系统（FastAPI + React）与 PMS-fresh 问数智能体（secretary）合并后的统一工程。

与旧仓库 `HengKang666/Project-Management-Secretary`（合并**前**的 secretary 单体）**无历史关联**——
两边路径交集几乎为零，因此是「另起新仓库」，而非增量推送。

## 二、首次推送步骤

```bash
cd "D:/Temporary download/work-space/合并系统+统一用户表"

# 1) 关联远程（本地仓库已 git init，分支 main）
git remote add origin https://github.com/123ycy666/agent_kb_server.git

# 2) 推送
git push -u origin main
```

> 若远程已有初始提交导致 non-fast-forward：
> `git push -u origin main --force`（远程为空仓库时不需要）。

## 三、推送前自检

- [ ] `git status --short` 无 `.env`、`node_modules`、`dist`、`data/`
- [ ] `.gitignore` 已排除 `.env` / `node_modules` / `dist` / `data/` / `.workbuddy/` / `__pycache__`
- [ ] 远程仓库你有写权限（owner 或 collaborator）

## 四、本次清理记录（2026-10-16）

推送前对项目做了一轮「去废弃、保整洁」清理：

| 类别 | 处理 |
|---|---|
| 废弃脚本 | 删除 `server/scripts/start_worker.sh`（Celery worker，已无对应代码） |
| 废弃目录 | 删除 `server/alembic/`（空目录，建表实际用 `db_init.py`） |
| 缓存 | 清空全部 `__pycache__` / `.pyc` |
| 废弃依赖 | `requirements.txt` 去 `celery`/`redis`；`pyproject.toml` 去 `alembic` |
| 死配置 | `config.py` 去未实现的限流字段与已失效的 bootstrap 管理员字段 |
| 过时编排 | 重写 `docker-compose.prod.yml`（去 Redis/Celery worker，端口 8200，库名 `ad_new`） |
| 过时模板 | 重写 `.env.production.example`（原为废弃变量体系）；修正 `.env.example` |
| 过时文档 | 8 份旧架构文档归档至 `docs/历史/` |

## 五、后续推送

```bash
git add -A
git commit -m "描述本次改动"
git push
```
