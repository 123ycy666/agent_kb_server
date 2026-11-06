---
name: local-pg-portable-setup
description: 本机没有 Docker，本地 PG 用绿色版 PostgreSQL 16 + pgvector 装在 D:\pg16local，端口 5433
metadata:
  node_type: memory
  type: project
---

**结论**：这台机器（Fred 的 Windows）**没有装任何容器运行时**（docker / podman / WSL 发行版全无），也没有管理员权限。
本地数据库不用 `docker-compose.yml`，改用**绿色版 PostgreSQL 16.14 + pgvector 0.8.7**，装在 `D:\pg16local`。

| 项 | 值 |
|---|---|
| 程序 | `D:\pg16local\pgsql`（EDB binaries zip 解压，带 pgvector DLL） |
| 数据 | `D:\pg16local\data`（initdb 生成，kb / kb_dev_pwd） |
| 库 / 端口 | `ad_new` / `127.0.0.1:5433`（与 `.env` 的 `DATABASE_URL` 一致） |
| Python | `%USERPROFILE%\.workbuddy\binaries\python\envs\default`（重建过，依赖已装全） |
| 后端 | `:8200`，`cd server && python -m uvicorn app.main:app --host 127.0.0.1 --port 8200` |

**Why**：2026-11-03 Fred 要「本地下个 pg，用 docker 把项目跑起来」。Docker Desktop 装不了（需 UAC 提权 + WSL2 + 可能重启），
所以选免管理员的绿色版。装的过程中踩到两个坑，记下来免得重来：

1. **中文路径会让 initdb 失败** —— `initdb` 在 `D:\秘书智能体\...` 下报
   `FATAL: invalid byte sequence for encoding "UTF8": 0xc3 0xd8`。**程序和数据都必须在纯 ASCII 路径**，所以放 `D:\pg16local`。
2. **改 postgresql.conf 不能带 BOM** —— 用 PowerShell `Set-Content -Encoding utf8` 写出的 BOM 会让 PG 报
   `syntax error in file "postgresql.conf" line 1`。必须用 `[IO.File]::WriteAllText($p,$c,[Text.UTF8Encoding]::new($false))`。

**How to apply**：
- ⚠️ **别用 agent 的后台作业起常驻服务** —— 2026-11-03 我用后台作业起的 PG 和后端，在这一轮会话结束、
  后台作业被回收时**一起被杀**（Fred 打开页面看到「连不上」，问「什么情况」）。常驻服务必须用
  `Invoke-CimMethod Win32_Process Create` 创建（不在 agent 的作业对象里）；后端入口用
  `D:\pg16local\serve_backend.py`（纯 ASCII 路径，避开 cmd 对中文路径的代码页破坏，日志写 `.local\backend.log`）。
- 查是否被回收：`Get-NetTCPConnection -State Listen -LocalPort 5433,8200`，两个端口都要有 PID。
- 启停：`D:\pg16local\pgsql\bin\pg_ctl.exe -D D:\pg16local\data -l D:\pg16local\pg.log start|stop`；查状态用 `status`。
- `启动全部.bat` 已加分支：`where docker` 找不到就自动走绿色版 PG 的 `pg_ctl start`，Fred 双击照旧能用。
- pg_bigm 没装（项目有意不用，`db_init.py` 只启用 vector），`/health` 里 `pg_bigm=✗` 是正常的。
- 员工认证走远端 MySQL `47.121.183.81:3306` 的 `t_employee`（125 人），**不依赖本地 PG 里的 app_user**。
- 名称纠错词典要单独导：`psql -h 127.0.0.1 -p 5433 -U kb -d ad_new -f db\lexicon.sql`（6 张 `t_nc_*` 表、4 万行）。
  不导的话 `/health` 里 `name_fix=false`，日志刷 `词典表不存在：t_nc_typo`。
