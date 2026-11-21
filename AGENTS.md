# AGENTS.md — 统一知识库与智能问数平台

项目：一个 FastAPI 单进程在 :8200 同时供知识库 API（`/api/v1/*`）、问数智能体（`/api/*`，代码在 `server/app/secretary/`）和 React SPA（`web/dist` 由后端托管），内容库是 PG `ad_new`（pgvector），员工库 MySQL `dlj_data` 只读。

## 怎么起

- 双击 `启动全部.bat`：有 docker 用 docker，没有就用绿色版 PG16（:5433），然后拉起后端；只起后端用 `启动知识库后端.bat`。
- 后端必须在 `server/` 目录下起（`app` 包在那里），脚本已 `pushd` 切目录，不要手改这一处。
- 启动命令没有 `--reload`，改完代码必须重启后端。唯一访问地址 `http://127.0.0.1:8200`，不要开 5173。
- `.env` 必须已存在（`copy .env.example .env` 后填 MySQL 密码与 `MODEL_CHANNEL`），缺了脚本直接退出。

## 怎么验

- `cd server` 后跑 `py -X utf8 tests\selfcheck.py`：末行 `结果：47/47 通过` 且退出码 0 才算 pass，出现任何 `[FAIL]` 即 fail。
- `自检.bat` 串三级（逻辑 / 语义 / 全链路），第三级要求 :8200 在跑，服务没起会打印 `[跳过]`。
- `check_env.bat` 退出码 0 才可上线，它查环境连通性（PG、两个 MySQL、LLM、Embedding 维度、端口）。
- README 第五节列的两条验证命令（`tests\agent\test_merge_contract.py`、`tests\test_skills_paths.py`）实测已不存在，别照着跑。

## 不要动

- `.env`：唯一配置源且不入库，改配置只改它。
- `server/app/secretary/skills/`：运行时被读取，删文件或重命名等于改模型行为。
- `data/uploads/`：上传原件落盘处，下载原文件从这里读。
- 不要引入 alembic：建表与增量迁移都在 `server/app/db_init.py`。

## 跨会话记忆

开工前先读 `.dsh/memory/MEMORY.md`，命中条目再读正文文件。
