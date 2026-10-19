# 统一知识库与智能问数平台

企业内部**知识库管理** + **自然语言问数**一体化系统。两个原本独立的系统已合并为**一个进程、一个端口、一个内容库、一套用户表**。

| 原系统 | 合并后位置 | 职责 |
|---|---|---|
| 本地知识库系统（FastAPI + React + PostgreSQL/pgvector） | `server/` + `web/` | 知识库 / 文档 / 切片 / 标签 / 权限 / 冲突检测 / 检索 |
| PMS-fresh 问数智能体（secretary） | `server/app/secretary/` | 自然语言问数（纠错 → 补全 → 模型自主循环查库 → 作答 → 落库） |

- **统一用户表**：全系统账号统一到 `dlj_data.t_employee`（HR 系统，MySQL），本系统**只读**。
- **模型三通道**：`MODEL_CHANNEL` 一个开关切换 `local`（内网自建）/ `online`（线上 API）。

---

## 一、功能清单

**知识库侧**
- 知识库 / 文件夹式管理，文档上传异步入库（解析 → 切片 → 向量化 → 冲突检测）
- 多格式：`.md .txt .pdf .docx .xlsx .xls`（`.xls` 走 xlrd，`.xlsx` 走 openpyxl）
- 切片级**冲突检测**：同一事实不同说法自动比对，文档级呈现，支持人工裁定
- 标签词表与治理、版本历史与回滚、可见性（公开 / 私有）
- 混合检索（向量召回 → 版本压制 → 重排），切片级权限过滤

**问数侧**
- 自然语言提问 → 名称纠错归一 → 时间/地点补全 → 模型**自主循环调工具**（查业务库 / 查知识库）→ 作答
- 全部问答落库，可按会话回看：完整对话 + 查询口径 + 工具轨迹 + 质量标记
- 语音输入（阿里云百炼 Fun-ASR）、分析页面自动生成、技能（预算分配 / 风险预控 / 审批等 15+ 个）

**办公文档侧（2026-10-19 加，`/api/files` + `/api/docs/jobs`）**
- 文件进出：Word / Excel / PDF / PPT / TXT 上传即解析（保留章节路径与段号，能回指原文）
- 带文件问答：短文件整篇、长文件**按问题召回章节**（20 页文件不整篇塞模型），回答必须标出处
- 三个文档任务（异步 + 进度 + 成果下载）：
  - `digest` 长文速读：一页纸结论 + 待办清单（事项/责任单位/时限/需报材料/出处）+ docx/xlsx
  - `proofread` 公文审校：错别字/标点/数字用法/层次序号（规则）+ 病句与前后矛盾（模型）+ 问题清单 docx
  - `table_audit` 表格审查：空值/重复行/合计不平/单位混写/文本数字/日期混用，定位到 sheet+单元格
- 自检：抽出的时限/金额逐条回指原文，找不到标「待核实」而不是丢掉

**平台侧**
- 统一登录（HR argon2 密码校验 + 本地投影 + 角色）、审计日志、模型配置与用量、任务中心

---

## 二、架构

```
                    ┌────────────────────────────────────┐
                    │  统一后端 :8200（FastAPI，单进程）   │
                    │  ├─ /api/v1/*   知识库与管理面 API   │
                    │  ├─ /api/*      问数智能体 API       │
                    │  ├─ /agent/     问答 / 语音 / 技能台  │
                    │  └─ /           前端 SPA（React 构建物）│
                    └──────┬────────────────────┬────────┘
                           │                    │
              ┌────────────▼─────────┐  ┌──────▼─────────────────┐
              │ PostgreSQL  ad_new    │  │ MySQL（只读）            │
              │ （docker 宿主机 5433） │  │ · dlj_data  员工/业务库  │
              │ · 知识库+向量(pgvector) │  │ · ai_data   业务字典     │
              │ · 对话记录/纠错词典     │  └────────────────────────┘
              │ · 用户投影/审计         │  ┌────────────────────────┐
              └───────────────────────┘  │ 模型服务（HTTP）          │
                                         │ local=内网 / online=公网 │
                                         └────────────────────────┘
```

**四个"一个"**：一个进程、一个端口（**8200**）、一个内容库（PG `ad_new`）、一套用户表（`t_employee`）。
文档入库用 FastAPI **BackgroundTasks**（**无 Redis / 无 Celery**）。

⚠️ **模型接口全部是 HTTP**（vLLM/Xinference/Ollama 与公网 API 都通过 HTTP 暴露），所以内网部署只是把地址换到内网，协议不变。

---

## 三、快速开始（Windows）

```bat
:: 0. 配置：复制模板并填写（.env 不入库，是唯一配置源）
copy .env.example .env
::    必填：MYSQL_PASSWORD（MySQL root 密码）

:: 1. 一键启动：双击
启动全部.bat
::    依次拉起 docker(PostgreSQL) → 统一后端 :8200

:: 2. 访问（唯一入口）
::    http://127.0.0.1:8200        本机
::    http://<本机IP>:8200         局域网其他设备
```

| 脚本 | 用途 |
|---|---|
| `启动全部.bat` | 拉起 PostgreSQL + 后端（日常用这个） |
| `启动知识库后端.bat` | 只拉起后端 |
| `启动前端.bat` | 仅前端热更新（vite `:5173`，**日常不用**） |
| `自检.bat` | 环境自检（依赖 / 端口 / 数据库连通） |

> ⚠️ 不要访问 `:5173`，那是开发热更新用的；`Ctrl+Shift+R` 强刷可清掉旧前端缓存。

---

## 四、目录结构

| 路径 | 说明 |
|---|---|
| `server/` | 统一后端（FastAPI） |
| `server/app/main.py` | 入口：注册路由 + 挂 `/agent/` + 托管前端 SPA |
| `server/app/db_init.py` | 建表 / 扩展 / 增量迁移 / 种子管理员（**不用 alembic**） |
| `server/app/api/v1/` | 知识库与管理面接口（`/api/v1/*`）+ 问数接口（`/api/*`） |
| `server/app/secretary/` | 问数智能体：`agent.py`(自主循环) `qa_log.py`(记录) `semantic.py`(语义层) `tools_*.py` |
| `server/app/secretary/skills/` | ⚠️ **技能文档（运行时读取，勿删）** |
| `server/app/core/` | 配置 / 双库引擎 / JWT / 权限矩阵 |
| `server/app/providers/` | **模型适配层**（mock / local / online 三档，换模型不改业务代码） |
| `server/app/services/` | 业务逻辑：入库流水线、检索编排、冲突检测、权限、审计 |
| `server/app/models/` `schemas/` | ORM 表定义 / 出入参模型 |
| `server/scripts/` | 运维脚本（重置库、重切片、修孤儿数据、导种子文档） |
| `server/tests/` | 回归与验收脚本 |
| `web/` | 前端（React + Ant Design），`dist/` 由后端托管 |
| `data/seed_docs/` | 种子文档（首次导入演示用） |
| `data/uploads/` | 上传原件落盘（下载原文件读这里） |
| `deploy/` `docker/` | nginx 配置 / 容器初始化 SQL |
| `docs/` | 接口文档、内网部署指南、打包清单 |

---

## 五、关键配置（`.env`）

| 配置 | 说明 |
|---|---|
| `MODEL_CHANNEL` | **总开关**：`local`(内网自建) / `online`(公网 API) |
| `DATABASE_URL` | PG 内容库（默认 `127.0.0.1:5433/ad_new`） |
| `MYSQL_*` | 员工认证源 `dlj_data.t_employee`（只读） |
| `LOCAL_MODELS` | 内网**实际部署**的模型清单（逗号分隔）；留空则只有 `LOCAL_LLM_MODEL` |
| `JWT_SECRET` | 生产必须替换（`openssl rand -hex 32`） |
| `EMBED_BATCH_CONCURRENCY` | 向量化并发度（online 单请求硬限 10 条） |

> ⚠️ 切换 `MODEL_CHANNEL` 后 **向量空间不兼容，必须重建索引**（知识库详情页有「重建索引」）。

---

## 六、文档

| 文档 | 内容 |
|---|---|
| `docs/接口文档.md` | 全部 HTTP 接口、认证、错误码、调用示例 |
| `docs/办公文档接口文档.md` | **办公文档能力接口文档 v1.0**（`/api/files`、带文件 `/api/ask`、`/api/docs/jobs`；字段表、错误码、示例、联调清单） |
| `docs/内网部署指南.md` | 内网离线部署全流程与排查 |
| `docs/内网打包清单.md` | **交付给内网部署人员时需要打包哪些文件** |

---

## 七、常见问题

| 现象 | 处理 |
|---|---|
| 页面空白 | `Ctrl+Shift+R` 强刷；确认 `web/dist/` 已构建 |
| **改了代码不生效** | 启动命令**没有 `--reload`**，必须重启后端 |
| 端口被占 / 行为随机 | `netstat -ano \| findstr :8200`，**只应有一个 PID**；双击 `.bat` 重启（已做幂等处理） |
| 问数没有历史记录 | ① 历史页**不会自动刷新**，点「刷新」或 F5；② 看响应里 `qa_id` 是否为 `null`；③ 后端窗口搜 `[qa_log] 落库失败` |
| 回答「模型不可用」 | 该模型未在当前网关部署，换一个（内网档请配 `LOCAL_MODELS`） |
| 启动报数据库初始化失败 | **先看异常类型**（日志已带类型名），不要默认是 PG 没启动 |

---

## 八、技术栈

后端 FastAPI + SQLAlchemy 2.x + psycopg3 + pgvector + PyJWT + argon2-cffi；
内容库 PostgreSQL 16 + pgvector；员工库 MySQL（只读）；
前端 React 18 + TypeScript + Vite + Ant Design；
模型层 HTTP 调用，三档可切换（内网 vLLM/Xinference/Ollama、公网 compatible-mode、mock 演示）。

---

## 九、2026-10-19 合流说明（本地秘书线并入本仓库）

本仓库是**平台线**（知识库 + 管理面）与**秘书线**（问数/预警）合流后的仓库。
合流时按「新仓库没有的功能以本地为准」处理，具体如下：

| 项 | 结论 |
|---|---|
| 秘书本体位置 | server/app/secretary/（原本地仓库的 secretary/，导入路径统一为 from app.secretary import x） |
| 服务端口 | **8200 单进程**（/api/v1/* 知识库 + /api/* 问数 + /agent/* 三张问答页 + / 前端 SPA） |
| 内容库 | PostgreSQL 16 + pgvector，库名 ad_new（宿主 5433 → 容器 5432） |
| 对话记录 / 纠错词典 | 与内容库同库（ad_new），走 pgdb 兼容层；词典导入用 db/lexicon.sql（6 张 t_nc_*，41563 行）；对话记录建表用 db/chat_tables.sql（**全新库必跑**，平台自动建表流程里没有这 5 张表） |
| 每日预警 | POST /api/alerts/daily（server/app/api/v1/agent.py），鉴权 = 请求头 X-Internal-Token（.env 的 INTERNAL_SERVICE_TOKEN）；**前端不做页面**，由外部定时任务调用 |
| 页面生成（分析大屏） | **已关闭并注释**（config.PAGE_ON=0；agent.render_page* 直接返回空串） |
| 技能文档 | server/app/secretary/skills/（跟代码同目录，打包不会漏） |
| 预警口径配置 | server/app/secretary/alert_rules.py（改预警只改这里） |

验证入口（都不连模型）：

    py -X utf8 server/tests/agent/test_merge_contract.py    # 合流契约：工具/守卫/配置/目录/预警
    py -X utf8 server/tests/test_skills_paths.py            # 技能目录必须存在且可读
