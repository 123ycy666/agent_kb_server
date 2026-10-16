# 统一知识库与智能问数平台

由两个原系统合并而成：

| 原系统 | 并入后的位置 | 职责 |
|---|---|---|
| 本地知识库系统（FastAPI + React + PostgreSQL/pgvector） | `server/` + `web/` | 知识库 / 文档 / 标签 / 权限 / 冲突检测 / 检索 |
| PMS-fresh 问数智能体（secretary） | `server/app/secretary/` | 自然语言问数（五段流程：信息补全 → 问题补全 → 理解问题 → 查库 → 总结回答） |

**统一用户表**：全系统账号统一到 `dlj_data.t_employee`（人力资源系统，MySQL），本系统只读。
**模型三通道**：大模型与向量模型支持 `local`（内网自建）/ `online`（线上 API）/ `mock`（零依赖演示），
一个开关 `MODEL_CHANNEL` 切换。

---

## 一、架构（2026-10-15 单进程合并后）

```
                    ┌──────────────────────────────┐
                    │  统一后端 :8200（FastAPI）     │
                    │  ├─ /api/v1/*  知识库 API     │
                    │  ├─ /api/*      问数智能体 API │
                    │  ├─ /agent/     问答/语音/技能台 │
                    │  └─ /           前端 SPA 托管   │
                    └───────┬──────────────┬───────┘
                            │              │
              ┌─────────────▼──────┐  ┌───▼───────────────┐
              │ PostgreSQL ad_new   │  │ MySQL 47.121.183.81│
              │ （docker 5433）      │  │ · t_employee 员工  │
              │ · 知识库+向量(pgvector)│  │ · dlj_data 业务只读│
              │ · 对话记录/纠错词典   │  │ · ai_data 字典只读 │
              │ · 用户投影/审计      │  └───────────────────┘
              └─────────────────────┘

一个进程、一个端口（8200）、一个数据库（PG ad_new）、一个用户表（t_employee）。
文档入库用 FastAPI BackgroundTasks（无 Redis/Celery）。
## 二、目录结构

```
合并系统+统一用户表/
├── 启动全部.bat / 启动知识库后端.bat / 启动前端.bat / 自检.bat
├── .env.example / .env          # 唯一配置源
├── docker-compose.yml           # 仅 PostgreSQL+pgvector
├── README.md / docs/合并设计方案.md / docs/接口契约.md
├── server/                      # 统一后端（FastAPI，:8200）
│   ├── app/
│   │   ├── main.py              # 入口：路由 + /agent/ 静态 + SPA 托管
│   │   ├── api/v1/              # 知识库 API + agent.py（问数接口）
│   │   ├── secretary/           # ⭐ 问数智能体（agent.py/semantic/tools_db/
│   │   │                        #   qa_log/name_fix/kb_adapter/pgdb/libs/skills/static）
│   │   ├── core/                # config/security( argon2id )/database(PG+MySQL)/deps(JWT)
│   │   ├── models/ providers/ services/ tasks/
│   │   ├── tests/               # selfcheck.py（47 项）+ agent/（回归脚本）
│   └── scripts/
├── web/                         # 前端（React + Ant Design，dist 由后端托管）
│   └── src/pages/AgentChat.tsx  # 智能问答页：SPA 内 iframe 嵌 /agent/（保留侧边栏）
└── data/seed_docs/              # 种子文档
```

## 三、快速开始

```bash
# 0. 配置（复制模板后填写）
copy .env.example .env
#    必填：MYSQL_PASSWORD（MySQL root 密码）
#    默认 MODEL_CHANNEL=local；本地端点未就绪可改 online（.env 已带百炼 Key）

# 1. 一键启动（Windows）：双击
启动全部.bat
#    依次拉起：docker(PostgreSQL) → 统一后端 :8200
#    （知识库界面 + 智能问答 + API，前端由后端托管，只有一个端口）

# 2. 访问（唯一入口）
#    浏览器打开   http://127.0.0.1:8200
#    同机其他写法 http://localhost:8200
#    局域网其他设备 http://<本机IP>:8200   （如 http://192.168.0.149:8200）
#
#    ★ 不要再访问 :5173（那是仅开发热更新用的 vite 服务，日常用不到）
#    ★ 页面空白时按 Ctrl+Shift+R 强制刷新（清旧缓存）
```

**登录**：使用员工账号（`t_employee.login_name` + HR 系统密码）。
`administrator_flag=1` 的员工（如 `admin`）自动成为管理员，其他为普通用户。
首次登录自动创建本地投影；密码修改请联系 HR 系统。

> ⚠️ Docker 拉镜像失败（国内网络常见）时，用国内代理拉取后重命名再启动，详见
> `docs/` 下知识库原文档。

## 四、模型三通道（mock / local / online）

`.env` 中一个总开关，两套端点：

```bash
MODEL_CHANNEL=local        # mock | local | online

# 本地通道（内网 vLLM / Xinference / Ollama，OpenAI 兼容）
LOCAL_LLM_BASE_URL=http://127.0.0.1:8000/v1
LOCAL_LLM_MODEL=deepseek-chat
LOCAL_EMBEDDING_URL=http://127.0.0.1:9997/v1/embeddings
LOCAL_EMBEDDING_MODEL=bge-m3
LOCAL_RERANK_URL=http://127.0.0.1:9997/v1/rerank
LOCAL_RERANK_MODEL=bge-rerank-m3

# 线上通道（阿里云百炼 compatible-mode）
ONLINE_LLM_BASE_URL=https://llm-xxxx.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
ONLINE_LLM_API_KEY=sk-...
ONLINE_LLM_MODEL=qwen3.8-max
ONLINE_EMBEDDING_MODEL=text-embedding-v4     # 1024 维
ONLINE_RERANK_MODEL=gte-rerank-v2
```

- 知识库后端与 agent **共用同一份 .env**，切换通道两边同时生效
- 「模型配置」页面可查看当前通道、切换通道（写回 .env）、测试连接
- ⚠️ **向量空间不兼容**：切换 embedding 通道后必须重建索引
  （知识库列表页会提示哪些库有待重建切片）

## 五、统一用户表

| 表 | 角色 |
|---|---|
| MySQL `dlj_data.t_employee` | **唯一权威源**（只读）：账号 / 密码（argon2id）/ 姓名 / 部门 / 停用删除标记 / 管理员标记 |
| PG `app_user` | 本地投影（登录时自动 upsert）：`external_id=employee_id`，**`role` 字段**（admin/user）由 `administrator_flag` 自动对齐 |
| 角色与权限 | **不再是数据表**：`app/core/rbac_config.py` 常量矩阵（19 个权限码）；接口层 `require_permission()` 不变 |

- 员工新增 / 停用 / 删除 / 改密码：全部在 HR 系统操作，本系统只读
- 「员工列表」页展示全部员工 + 本地投影状态 + 当前角色
- 本系统不存员工密码、不提供改密码

## 六、问数智能体与知识库的关系（单进程内直调）

2026-10-15 单进程合并后，问数智能体（`app/secretary/`）与知识库在同一进程：
检索走 **进程内直调**（`secretary/kb_adapter.py` → `services/retrieval/search.py`），
**不再有 HTTP 回调、不需要内部服务令牌**（`X-Internal-Service-Token` 已删除）。
检索身份 = **当前登录用户**，与 Web 界面口径一致（public + 自己上传的）。

## 七、测试

```bash
自检.bat                              # 三级：逻辑自检 → 语义层 → 全链路验收(31项)
# 或手动：
cd server && python tests/selfcheck.py            # 47 项逻辑自检（无数据库）
cd server && python tests/acceptance.py           # 31 项全链路验收（需服务在 8200）
cd server && python -X utf8 tests/agent/test_semantic.py   # 语义层断言
```

- `server/tests/e2e_check.py`：端到端（需数据库 + 后端运行，会自动清理测试数据）
- `agent/tests/e2e_test.py` / `regression.py`：问数链路（会真实调用大模型）

## 八、与两个原项目的关系

- 原项目 **未做任何修改**；本仓库是复制后合并改造的独立副本
- 原知识库的 PG 数据（文档/切片/用户）如 Docker volume 尚在，启动后直接继承
  （投影行按 username 对齐，历史文档归属不变）
- agent 侧 `agent_data` 库的对话记录、纠错词典原样沿用
- 已废弃不再合并：API Key 双认证、阿里云百炼知识库管理面、`agent_data.t_kb_*` 旧表、
  原 `_backup_online_providers` 备份目录、各部署 tar 包

## 九、验收状态（2026-10-15 全部实测通过）

| 测试 | 项数 | 结果 | 说明 |
|---|---|---|---|
| `server/tests/selfcheck.py` | 47 | ✅ 全通过 | 知识库逻辑，不连数据库 |
| `server/tests/acceptance.py` | 33 | ✅ 全通过 | **全链路验收**（含静态资源 MIME 防白屏、问答页知识库多选等断言） |
| `server/tests/agent/regression.py` | 30 题 | ✅ 全跑通 | 真实查库 + LLM + 知识库，平均 12.7s/题 |
| `server/tests/agent/e2e_test.py` | 27 题 | ✅ 全跑通 | 口语补全 → 自主查库 → 回答 → 拒答 |
| `server/tests/agent/env_check.py` | — | ✅ | MySQL 真库连通 |
| `server/tests/agent/schema_check.py` | 13 表 | ✅ | 6 表不在业务字典 → 正确 SKIP |
| `server/tests/agent/test_semantic.py` | — | ⚠️ 1 条历史失败 | 见下 |

**`acceptance.py` 覆盖的四阶段**（一个账号 kbadmin 走完全部功能）：

- **A 统一用户**：登录 → 错误密码被拒 → /me 返回 19 权限点 → 员工列表 123 人 → 投影角色对齐 admin
- **B 知识库**：库列表 → 文档上传 202 受理 → 入库任务 done(ready) → 检索命中 3 条
  → 文档列表 → **上传人 owner_id = 登录用户 id** → 标签
- **C 问数智能体**：health(name_fix=True / kb=True / channel=online) → 真实业务问答 →
  「我的会话」按 user_id 过滤 → 会话归属当前用户 → 会话历史 → 统计 → 缺口 → 反馈
- **D 页面**：SPA / 登录页 / 问答页 / 语音页 / 技能台 / health / docs —— 全部 200

**运行方式**：双击 `自检.bat`（三级：逻辑自检 → 语义层 → 全链路验收，需服务在 8200）

### 唯一遗留：test_semantic 一条断言

`t_power_work_cost`（定额表）不在语义层白名单里。这条断言在**原 PMS-fresh 项目同样失败**
——线上 `ai_data` 字典新增了这张表，需要字典维护方确认是否纳入白名单。与合并改造无关。

---

## 十、已知待办

1. **内网嵌入**：本期不做（用户明确要求）
2. ~~`web_broken/` 残留目录~~ ✅ 已清理（2026-10-15）
3. **MySQL `agent_data` 原库**：数据已迁到 PG `ad_new`，原库保留未删（可回溯，确认稳定后清理）
4. **`agent_data.t_kb_*` 三张废弃表**：生产库，未动（从权限考虑）
5. **local 档内网端点**：代码与配置完整保留，等内网模型服务地址确定后填 `.env` 即可切换
