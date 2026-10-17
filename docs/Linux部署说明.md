# Linux 部署说明

> 目标机：**Linux（ARM64）/ Python 3.12.10**。
> 本文只讲 **Linux 与 Windows 的差异**；通用内容（配置项含义、验证清单、故障排查）
> 仍以 `docs/内网部署指南.md` 为准。

---

## 一、先说结论：这台机器是「更新」，不是「全新安装」

从服务器现状看（`docker ps` 显示 MySQL 8.0.46 healthy、vLLM 在 18000、`curl /api/ask` 能返回
`model=kimi-k3`、`steps=6`、`qa_id` 有值、答案里是真实业务数据），**系统已经在这台机器上跑起来了**。

所以最省事的更新流程只有 3 步：

```bash
# 1. 备份当前代码（保留原目录名，出事好回滚）
cp -a /path/to/现有项目 /path/to/现有项目.bak.$(date +%F)

# 2. 用新代码覆盖（保留 .env 和 data/ 不动！）
#    只覆盖 server/ web/ docs/ 等代码目录，不要动 .env、data/

# 3. 重启后端
./start_backend.sh          # 或 systemctl restart kb-backend
```

**关键：`.env` 和 `data/` 千万不要覆盖**（`.env` 是这台机器专用的配置，`data/uploads` 是用户上传的原件）。

**⚠️ 本次更新没有新增任何 Python 依赖**（改的都是纯代码：线程池、异常类型、模型清单、技能路径），
所以 **不需要 `pip install`，也不需要 `wheels` 包**——除非你打算重建虚拟环境。

---

## 二、Windows 与 Linux 的差异对照

| 事项 | Windows（原文档） | Linux（本文） |
|---|---|---|
| 启动脚本 | `启动全部.bat` / `启动知识库后端.bat` | **`./start_all.sh` / `./start_backend.sh`** |
| 脚本权限 | 双击即可 | **`chmod +x *.sh`** 后才能执行 |
| 换行符 | CRLF | **脚本必须是 LF**（CRLF 会报 `bad interpreter: ^M`） |
| 虚拟环境 | `.venv\Scripts\activate` | `source .venv/bin/activate` |
| Python | `python` | **`python3`**（或 `$PYTHON` 指定） |
| 常驻运行 | NSSM | **systemd**（见下） |
| 路径 | `D:\kb` | `/opt/kb`（建议） |
| 依赖包 | `win_amd64` 的 wheel | **`manylinux*_aarch64`** 的 wheel |

---

## 三、全新 Linux 部署（若非更新场景）

```bash
# ① 解压
mkdir -p /opt/kb && cd /opt/kb
unzip kb-delivery-linux.zip      # 得到 /opt/kb/kb-delivery/ 之类，按实际调整

# ② 建虚拟环境（Python 3.12）
python3.12 -m venv .venv
source .venv/bin/activate

# ③ 装依赖（二选一）
pip install -r server/requirements.txt                      # 有内网 PyPI 源
pip install --no-index --find-links=wheels -r server/requirements.txt   # 完全离线

# ④ 配置
cp .env.example .env
vi .env      # 必改：MODEL_CHANNEL=local、LOCAL_* 、DATABASE_URL、MYSQL_*、JWT_SECRET

# ⑤ 数据库（二选一）
docker compose up -d postgres          # 用容器（镜像 pgvector/pgvector:pg16，多架构自动选 arm64）
# 或：内网已有 PostgreSQL 16 → CREATE EXTENSION vector; CREATE DATABASE ad_new;

# ⑥ 启动
chmod +x start_backend.sh start_all.sh
./start_all.sh
```

---

## 四、ARM64 的两个特别注意点

### 1. SQLAlchemy 少一个可选加速包（正常现象）

`greenlet` 在 aarch64 上可能装的是源码版或缺失，SQLAlchemy 的**同步**用法完全不受影响
（本项目只用同步 Session）。若 `pip install` 报 greenlet 相关警告，可以忽略。

### 2. pgvector 镜像用多架构版即可

`pgvector/pgvector:pg16` 官方镜像**同时提供 amd64 与 arm64**，`docker compose up -d` 会自动选对。
所以 **Windows 那套 `pgvector-pg16.tar`（amd64）在这台机器上用不了，也不需要**——
直接 `docker compose up -d` 拉 arm64 版即可。

> 若内网完全离线且无法拉镜像，需要让**能出网的 arm64 机器**上执行：
> `docker pull pgvector/pgvector:pg16 && docker save pgvector/pgvector:pg16 -o pgvector-arm64.tar`
> （在 amd64 机器上 `docker pull --platform linux/arm64` 也能拿，见交付说明）

---

## 五、systemd 常驻（替代 NSSM）

`/etc/systemd/system/kb-backend.service`

```ini
[Unit]
Description=KB Backend (统一知识库与智能问数平台)
After=network.target docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/kb/kb-delivery/server
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/kb/kb-delivery/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8200
Restart=always
RestartSec=3
StandardOutput=append:/var/log/kb-backend.log
StandardError=append:/var/log/kb-backend.err.log

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now kb-backend
systemctl status kb-backend
journalctl -u kb-backend -f          # 看实时日志
```

> ⚠️ **不要加 `--workers`**（本系统设计为单进程：进程内缓存、ContextVar 身份、BackgroundTasks 队列都依赖它）
> ⚠️ **不要加 `--reload`**（生产会反复重启）

---

## 六、Linux 上的排查命令对照

| 目的 | 命令 |
|---|---|
| 端口占用 | `ss -lntp \| grep 8200` |
| 进程 | `ps -ef \| grep uvicorn` |
| 停止 | `pkill -f "uvicorn app.main:app"` |
| 看日志（systemd） | `journalctl -u kb-backend -n 200 --no-pager` |
| 看日志（手工启动） | 前台直接看窗口输出 |
| 测健康 | `curl -s localhost:8200/health` |
| 测 PG | `psql "postgresql://kb:密码@127.0.0.1:5433/ad_new" -c "select extname from pg_extension"` |

**历史记录为空时**：先看 `/api/ask` 返回的 `qa_id` 是否为 `null`，再在日志里搜 `[qa_log] 落库失败`。
（历史页本身**不会自动刷新**，点「刷新」或 F5。）
