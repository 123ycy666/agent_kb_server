# 与 state-grid-agent（sga）的对接方案

> 2026-10-19　适用：sga（Spring Boot + sa-token）调用本平台（FastAPI + JWT）的 AI 能力
> 本文只讲方案与落地清单，**不涉及具体代码改动**（改动另提）。

---

## 0. 问题定义

两个系统各自有登录，且共用 `t_employee` 用户表：

| | sga | 本平台 |
|---|---|---|
| 认证 | sa-token（默认 30 天） | 自签 JWT HS256（12 小时） |
| 账号 | `t_employee.login_name` | `t_employee.login_name` → `app_user.username` |
| 会话互认 | 不认我们的 token | 不认对方的 token |

**因此：跨系统调用必须过一次认证。** 目标是把这次认证做成**用户无感**，
并且**历史 / 审计 / 权限按真实用户落库**，而不是所有操作都记在一个"机器人账号"上。

两条推荐路线，按"谁发起请求"二选一：

- **路线 A（后端到后端）**：内部令牌 + `X-User-Code`
- **路线 B（浏览器直连）**：短票据换 token（SSO Token Exchange）

如果 sga 前端只是调自己的后端、由后端转发，用 **A**；
如果 sga 前端要**直接**调本平台接口，用 **B**（A 的令牌绝不能下发到浏览器）。

---

## 1. 路线 A：内部令牌 + `X-User-Code`

### 1.1 协议

请求头：

```
X-Internal-Token: <INTERNAL_SERVICE_TOKEN>     # 与服务端 .env 一致，64 hex
X-User-Code:      zhangsan                     # t_employee.login_name（也可传 uid）
```

服务端判定顺序：

1. 有 `X-Internal-Token` 且与配置**完全一致** → 进入内部模式
   - 取 `X-User-Code` 查 `app_user.username`
   - 查不到 → **401**（不得降级成匿名或默认账号）
   - 查到 → 用该用户的真实角色/权限构造 actor
2. 否则走原有 JWT 校验
3. 两者都没有 → 401

### 1.2 开放范围（重要）

| 接口 | 是否开放内部令牌 | 理由 |
|---|---|---|
| `POST /api/ask` | ✅ | 核心诉求 |
| `GET /api/sessions` `/api/history` | ✅ | 用户看自己的历史 |
| `POST /api/feedback` | ✅ | 评价自己的回答 |
| `POST /api/v1/search` | ✅ | 检索片段 |
| `GET /api/v1/kb`、`/kb/{id}`、`/kb/{id}/documents`、`/documents/{id}`、`/download`、`/content` | ✅（只读） | 选库、看文档 |
| 写入类：建库/删库/上传/删文档/改权限/标签 | ❌ | 令牌泄露即可破坏数据 |
| 管理类：用户/角色/审计/模型切换/全库扫描 | ❌ | 同上 |

> 原则：**内部令牌只给"读 + 问答"，写操作一律要求真实用户 JWT。**

### 1.3 安全约束

1. **令牌只在服务端**：写进 sga 的配置（环境变量），不进前端、不进日志。
2. **来源限制**：服务端校验 `request.client.host` 属于内网（`127.0.0.1` / `172.16.0.0/12` / `10.0.0.0/8`），
   防止令牌经 nginx 被公网利用；nginx 侧**不要**转发 `X-Internal-Token` 头（当前 nginx 未透传自定义头，安全）。
3. **审计**：`audit_log` 记录 `actor=真实用户`，并额外记 `auth_mode=internal`，便于事后追溯。
4. **轮换**：改 `.env.production` 的 `INTERNAL_SERVICE_TOKEN` → 重启容器；sga 同步改配置。
5. **最小权限**：`X-User-Code` 对应的用户权限**照常生效**（`kb_ids` 越权仍返回 403）。

### 1.4 sga 侧改造清单（约 30 行）

1. 配置项：`AI_BASE_URL=http://127.0.0.1:8200`、`AI_INTERNAL_TOKEN=...`
2. HTTP 客户端统一注入两个头（拦截器）
3. 用户上下文里取 `login_name` 作为 `X-User-Code`
4. 超时：问答接口 **≥ 120s**（复杂题 15~30s）
5. 保存返回的 `session_id`（按 sga 用户 + 会话存），追问时带回

### 1.5 本平台改造清单（约 60 行，不改现有鉴权逻辑）

1. `core/config.py`：`internal_service_token`、`internal_allow_cidrs`
2. `core/deps.py`：新增 `get_current_actor_flexible`
   （内部令牌分支 → 查 `app_user` → 复用现有 actor 构造）
3. 将 1.2 表中"✅"的端点依赖从 `get_current_actor` 换成 flexible 版本（逐个替换，未列出的保持原样）
4. `audit` 增记 `auth_mode`
5. 新增测试：① 正确令牌+用户 → 200；② 错令牌 → 401；③ 正确令牌+不存在用户 → 401；
   ④ 内部令牌访问写接口 → 401/403；⑤ `kb_ids` 越权 → 403

### 1.6 验收标准

- sga 不登录也能调通问答（用同一员工账号的历史可见）
- 拿内部令牌调 `DELETE /api/v1/kb/1` → 被拒
- 审计里能看到"某员工通过内部令牌提问"

---

## 2. 路线 B：短票据换 token（SSO，浏览器直连）

> 适用：sga 有自己的前端，且希望**前端直接调本平台**（不转发）。
> 核心：**内部令牌永远不下发浏览器**，浏览器拿到的是"一次性的短票据"换来的本平台 JWT。

### 2.1 流程（5 步）

1. 用户在 sga 登录（sa-token 已有），sga 后端知道 `login_name`
2. sga 前端（或后端）向本平台请求换 token 前，先由 **sga 后端签一张短票据**：
   `ticket = JWT{ sub: login_name, exp: now+60s, jti: 随机串 }`，用双方共享密钥 HS256 签名
3. 浏览器 → `POST /api/v1/auth/sso/exchange`，body `{"ticket": "..."}`
4. 本平台验签（共享密钥）→ 校验过期 / `jti` 未用过（60s 内一次性）→ 查 `app_user` →
   **返回本平台 JWT**（12h）+ 用户信息 + 权限
5. 之后浏览器直接带本平台 JWT 调 `/api/ask` 等；过期（401）时回到第 2 步重换，用户无感

### 2.2 安全约束（比路线 A 更严，因为票据会经过浏览器）

| 风险 | 对策 |
|---|---|
| 票据被截获重放 | `exp ≤ 60s` + `jti` 一次性（Redis/PG 记录，用过即废） |
| 共享密钥泄露 → 伪造任意用户 | 密钥只在两个后端；支持**双密钥并存**（`SSO_SECRET` / `SSO_SECRET_OLD`）滚动轮换 |
| 明文传输 | **必须 HTTPS**（当前本平台是 HTTP，这是硬前置，见 2.4） |
| 越权 | 票据只带身份不带权限；权限一律以本平台 `app_user` 为准 |
| 跨域 | `CORS_ORIGINS` 加入 sga 前端 origin，且 `allow_headers` 含 `Authorization`、`Content-Type` |

### 2.3 本平台改造清单（约 80 行）

1. `POST /api/v1/auth/sso/exchange`：验签 → 查用户 → 签发本平台 JWT → 写审计
2. 配置：`SSO_SHARED_SECRET`、`SSO_SECRET_OLD`（可选）、`SSO_TICKET_TTL=60`
3. `jti` 去重表（可复用 `app_kv` 或建 `t_sso_ticket`，TTL 清理）
4. CORS 白名单加入 sga 前端域名
5. 测试：正常 / 过期 / 重放 / 错签 / 用户不存在

### 2.4 前置条件（必须先解决）

- **HTTPS**：浏览器里传 token，明文 HTTP 等于公开凭据。需要域名 + 证书（certbot 或已有证书）。
- **CORS**：sga 前端域名要加进白名单并重启。

### 2.5 验收标准

- 用户只在 sga 登录一次，就能在 sga 页面里问答、看历史
- 同一张票据第二次使用 → 401
- 票据过期 61s 后使用 → 401
- 本平台历史里能看到该用户自己的会话

---

## 3. 两条路线怎么选

| 场景 | 选 | 说明 |
|---|---|---|
| sga 前端只调 sga 后端，后端转发 AI | **A** | 最简单，今天就能联调；无需 HTTPS |
| sga 前端直接调本平台 | **B** | 需要 HTTPS + CORS + 票据表 |
| 两者都有（部分页面直连） | **A + B** | 后端转发用 A，直连页面用 B；两者互不冲突 |
| 以后还会有第三、四个系统接入 | **统一 SSO（OIDC）** | 现在是两系统，用 B 即可；系统数 ≥3 再上统一认证中心 |

**推荐落地顺序**：先用 **A** 打通联调（0 改动，服务账号也能跑）→ 再按需要实现 **A 的用户级版本**或 **B**。

---

## 4. 关于"合并成一个项目"

**不建议合并代码**：Java Spring Boot（sa-admin 底座）与 Python FastAPI 技术栈不同，
合并等于重写一侧，且会让发布节奏、故障域耦合。

**建议合并"体验"**（且不需要 iframe）：

- 一个域名（或同一主域的子路径）：`sga` 与 `ai` 由 nginx 分流，用户只看到一套导航
- 一个登录：走上面的 B（sga 前端直接换我们的 token），用户不再看到第二个登录框
- 能力通过 API 提供：sga 前端按 `docs/01-接口/对外接口文档.md` 调用

> 如果 sga 前端愿意只调自己的后端，那连 SSO 都不需要，走路线 A 即可——
> 这种情况下"两个系统"对用户是完全透明的。

---

## 5. 需要对方（sga）提供的信息

1. 调用方：**前端直连** 还是 **后端转发**（决定 A / B）
2. sga 前端域名（CORS）
3. 是否有 HTTPS 域名可用于本平台（路线 B 前置）
4. 联调用的测试员工账号（`login_name`），用于验证历史归属
5. 他们的 HTTP 客户端超时设置（问答需 ≥120s）
