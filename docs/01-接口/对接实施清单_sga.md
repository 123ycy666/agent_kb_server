# 与 sga 对接实施清单（内部令牌 + SSO）

> ⚠️ **本文已归档，不要按它实施。** 现行方案见 `docs/01-接口/给sga前端_改动说明.md`（免密换 token，一页版）。
> 本文保留仅为记录“安全版”思路（内部令牌 / 短票据），日后收紧权限时可回看。

> 2026-10-19　场景：**sga 前端只调 sga 后端；sga 后端转发 AI**；同时希望 **sga 前端能直接调本平台的功能接口**，
> 且**用户是同一个、历史记录也在**。
> 本文可直接转给 sga 的同事。

---

## 0. 先回答一个问题：能做到吗？

**能。** 关键是分清两条通道，它们共用同一套身份映射（`t_employee.login_name` → `app_user.username`），
所以无论走哪条，**落库的用户、历史、权限都是同一个人**。

| 能力 | 通道 A（sga 后端转发） | 通道 B（sga 前端直连） | 身份 / 历史 |
|---|---|---|---|
| AI 问答 `/api/ask` | ✅ 推荐 | ✅ 也可 | 真实员工，历史按人 |
| 会话列表 / 明细 | ✅ | ✅ | 只能看到自己的 |
| 知识库 / 文档**查询** | ✅ | ✅ | 按该员工权限过滤 |
| 文档**上传 / 删除 / 改可见性** | ❌（不开放写） | ✅ 必须走 B | 按该员工权限 |
| 评价 / 检索 | ✅ | ✅ | — |

**为什么上传删除必须走 B**：通道 A 用的是内部令牌，一旦泄露就能替任何人写数据；
所以 A 只开放「读 + 问答」，写操作必须由浏览器携带**该用户自己的 JWT**（通道 B）。

---

## 1. 两条通道分别怎么用

```
通道 A（后端到后端）
sga 前端 → sga 后端 → [X-Internal-Token + X-User-Code] → 本平台 /api/ask
                        ↑ 令牌只在服务端，浏览器永远看不到

通道 B（浏览器直连）
sga 前端 → sga 后端 /ai/ticket  ← 后端用共享密钥签 60s 票据
         → 本平台 /api/v1/auth/sso/exchange（用票据换 JWT）
         → 之后直接调 /api/ask、/api/sessions、/api/v1/documents…
```

两条通道可以**同时开**：AI 走 A（后端把问答结果转给前端），文档管理走 B（前端直连）。

---

## 2. 本平台要改什么（我方，约 150 行）

### 2.1 配置（`server/app/core/config.py`）

```python
internal_service_token: str = ""          # 通道 A 的令牌；为空 = 不启用 A
internal_allow_cidrs: str = "127.0.0.1/32,172.16.0.0/12,10.0.0.0/8,192.168.0.0/16"
sso_shared_secret: str = ""               # 通道 B 的票据签名密钥
sso_secret_old: str = ""                  # 轮换期并存（可选）
sso_ticket_ttl: int = 60                  # 票据有效期（秒）
```

### 2.2 身份依赖（`server/app/core/deps.py`）

新增 3 个东西，**不动现有 JWT 逻辑**：

```python
def _actor_for_username(db, username) -> CurrentActor:
    """按 login_name 取 app_user，构造与 JWT 完全一致的 actor。"""
    user = db.scalar(select(AppUser).where(AppUser.username == username,
                                           AppUser.status == "active"))
    if user is None:
        raise HTTPException(401, {"code": "USER_NOT_FOUND", "message": "用户不存在或已停用"})
    role_ids, role_codes, codes = _load_user_permissions(db, user.id)
    return CurrentActor(type="user", user_id=user.id, username=user.username,
                        display_name=user.display_name or user.username,
                        role_ids=role_ids, role_codes=role_codes, permissions=codes)


def _cidr_ok(request, allow: str) -> bool:
    """来源必须是内网（防止令牌经 nginx 被公网利用）。"""
    ip = ipaddress.ip_address(request.client.host)
    return any(ip in ipaddress.ip_network(n.strip()) for n in allow.split(",") if n.strip())


def get_current_actor_flexible(
    request, authorization=Header(None),
    x_internal_token=Header(None, alias="X-Internal-Token"),
    x_user_code=Header(None, alias="X-User-Code"),
    db=Depends(get_db),
) -> CurrentActor:
    tok = settings.internal_service_token
    if tok and x_internal_token and secrets.compare_digest(x_internal_token, tok):
        if not _cidr_ok(request, settings.internal_allow_cidrs):
            raise HTTPException(401, {"code": "INTERNAL_NOT_ALLOWED", "message": "来源不允许"})
        if not x_user_code:
            raise HTTPException(401, {"code": "MISSING_USER_CODE", "message": "缺少 X-User-Code"})
        actor = _actor_for_username(db, x_user_code)
        request.state.auth_mode = "internal"      # 审计用
        request.state.actor = actor
        return actor
    return get_current_actor(request, authorization, db)   # 回落原有 JWT
```

再补一个 `require_permission_flexible(*codes)`：与现有 `require_permission` 相同，只把依赖换成上面的。

### 2.3 换端点的依赖（只读接口，逐个替换，未列出的保持不动）

| 文件 | 端点 |
|---|---|
| `api/v1/agent.py` | `POST /ask`、`GET /sessions`、`GET /history`、`POST /feedback`、`GET /stats`、`GET /models` |
| `api/v1/kb.py` | `GET /kb`、`GET /kb/{id}`、`GET /kb/{id}/stats`、`GET /kb/{id}/documents`（把 `require_permission` 换成 `require_permission_flexible`） |
| `api/v1/documents.py` | `GET /documents/{id}`、`/content`、`/download`、`/chunks`、`/versions` 及其权限依赖 |
| `api/v1/search.py` | `POST ""` 及其权限依赖 |

**明确不换**：建库/改库/删库、上传、删文档、改可见性、标签写、角色/用户、审计、模型切换、全库扫描。

### 2.4 新增 SSO 换 token（`api/v1/auth.py`）

```python
class SsoTicketIn(BaseModel):
    ticket: str

@router.post("/sso/exchange", response_model=LoginResponse, summary="用 sga 短票据换本平台 JWT")
def sso_exchange(payload: SsoTicketIn, request: Request,
                 db: Session = Depends(get_db),
                 mysql_db: Session = Depends(get_mysql_db)):
    claims = verify_sso_ticket(payload.ticket)      # HS256，支持 sso_secret_old 轮换
    if not claims: raise HTTPException(401, {"code": "BAD_TICKET", "message": "票据无效或已过期"})
    jti, login_name = claims["jti"], claims["sub"]

    if not consume_jti(db, jti, claims["exp"]):     # 一次性：唯一索引插入，冲突=重放
        raise HTTPException(401, {"code": "TICKET_REPLAYED", "message": "票据已被使用"})

    emp = employee.fetch_employee(mysql_db, login_name)   # 以 HR 为准，停用/离职直接拒
    if emp is None: raise HTTPException(401, {"code": "USER_NOT_FOUND", "message": "员工不存在或已停用"})

    user = employee.sync_projection(db, emp)
    token = create_access_token(user.id, extra={"username": user.username})
    audit.log_action(db, actor=CurrentActor(type="user", user_id=user.id, username=user.username),
                     action=audit.AuditAction.LOGIN, resource_type="user",
                     detail={"via": "sso"}, request=request, commit=True)
    return LoginResponse(access_token=token,
                         expires_in=settings.access_token_expire_minutes * 60,
                         user=UserBrief.model_validate(user),
                         roles=[user.role],
                         permissions=sorted(permissions_for(user.role)))
```

配套：

- `db/sso_ticket.sql`：`t_sso_ticket(jti varchar(64) primary key, login_name varchar(64), exp timestamptz, created_at timestamptz default now())`，定时清理 `exp < now() - interval '1 day'`
- `verify_sso_ticket`：先用 `sso_shared_secret` 解，失败再试 `sso_secret_old`（PyJWT 对 HS256 传 key 列表会报错，
  必须**逐个 try**），两种情况都校验 `exp/iat`
- 环境变量：`.env.production` 加 `SSO_SHARED_SECRET`（64 hex）
- CORS：`CORS_ORIGINS` 追加 sga 前端 origin（例如 `http://47.121.183.81:8080`）

---

## 3. sga 要改什么（同事那边，约 60 行 + 前端 30 行）

### 3.1 通道 A：后端转发（Java）

配置：

```yaml
ai:
  base-url: http://127.0.0.1:8200
  internal-token: ${AI_INTERNAL_TOKEN}   # 放环境变量/配置中心，不要进代码库
  connect-timeout: 5000
  read-timeout: 120000                   # 问答可能 15~30s，务必 ≥120s
```

请求拦截器（RestTemplate / Feign / WebClient 三选一，思路一致）：

```java
public void apply(HttpRequest req) {
    String loginName = LoginHelper.getLoginUser().getUsername();   // sa-token 里的登录名
    req.header("X-Internal-Token", aiProps.getInternalToken());
    req.header("X-User-Code", loginName);
    req.header("Content-Type", "application/json");
}
```

要点：

1. **`X-User-Code` 必须是真实登录人**，不能写死；否则历史全落在一个账号上
2. 问答返回的 `session_id` 存到 sga 自己的会话表（关联 sga 的会话/用户），追问时原样带回
3. 超时 ≥120s；建议前端也把 loading 做到 30s 以上
4. 错误处理：`401`（令牌错/用户不存在）→ 记告警；`403` → 提示“无该知识库权限”

### 3.2 通道 B：前端直连（他们前端 + 后端各一小段）

sga 后端新增一个接口（给前端换 token 用）：

```java
@GetMapping("/ai/ticket")
public String aiTicket() {
    String loginName = LoginHelper.getLoginUser().getUsername();
    return JwtUtil.sign(Map.of(
        "sub", loginName,
        "jti", UUID.randomUUID().toString(),
        "exp", System.currentTimeMillis()/1000 + 60     // 60 秒
    ), aiProps.getSsoSharedSecret());                    // HS256
}
```

sga 前端：

```js
// 进入 AI 页面前换一次，之后直接调我们的接口
async function ensureAiToken() {
  if (aiToken && !isExpired(aiToken)) return aiToken;
  const ticket = await api.get('/ai/ticket');                 // 调 sga 自己的后端
  const r = await fetch('http://47.121.183.81/api/v1/auth/sso/exchange', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ ticket }),
  });
  aiToken = (await r.json()).access_token;                    // 我们自己签的 JWT，12h
  return aiToken;
}
// 之后：fetch('http://47.121.183.81/api/ask', {headers:{Authorization:`Bearer ${await ensureAiToken()}`}})
```

要点：

- **票据 60 秒 + 一次性**：拿到就立刻换，不要缓存票据
- 我们的 token 建议存**内存**（刷新页面重换一次即可），不要长期写 localStorage
- 遇到 `401` 自动重换一次
- 需要 HTTPS（浏览器里传 token，HTTP 等于裸奔）

---

## 4. 怎么做测试

### 4.1 我方单元测试（新增 2 个文件，均可独立运行）

`server/tests/api/test_internal_token.py`

| # | 用例 | 期望 |
|---|---|---|
| 1 | 正确令牌 + 存在的用户 | 200，返回身份是该用户 |
| 2 | 错误令牌 | 401 `INVALID_TOKEN`（回落 JWT 后失败） |
| 3 | 正确令牌 + 不存在的用户 | 401 `USER_NOT_FOUND` |
| 4 | 内部令牌调 `DELETE /api/v1/kb/{id}` | 403/401（写接口不开放） |
| 5 | 来源 IP 不在允许网段 | 401 `INTERNAL_NOT_ALLOWED` |

`server/tests/api/test_sso_exchange.py`

| # | 用例 | 期望 |
|---|---|---|
| 1 | 正常票据 | 200 + access_token |
| 2 | 过期票据（ttl+1s） | 401 `BAD_TICKET` |
| 3 | 同一票据用两次 | 第二次 401 `TICKET_REPLAYED` |
| 4 | 错签名 | 401 |
| 5 | 票里的人已停用 | 401 |

### 4.2 联调 curl（可原样贴给同事）

```bash
BASE=http://47.121.183.81

# ── 通道 A：后端转发 ─────────────────────────────
curl -s -X POST $BASE/api/ask \
  -H "X-Internal-Token: $AI_TOKEN" -H "X-User-Code: zhangsan" \
  -H 'Content-Type: application/json' \
  -d '{"question":"供电所有几个","kb_ids":[1]}' | head -c 300

# 该员工的历史（应能看到刚才那条）
curl -s "$BASE/api/sessions?limit=5" -H "X-Internal-Token: $AI_TOKEN" -H "X-User-Code: zhangsan"

# 反向验证：内部令牌不能删库
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE $BASE/api/v1/kb/1 \
  -H "X-Internal-Token: $AI_TOKEN" -H "X-User-Code: zhangsan"     # 期望 403/401

# ── 通道 B：前端直连 ─────────────────────────────
# 1) sga 后端签一张 60s 票据（他们那边执行）
# 2) 换我们的 token
OUR_TOKEN=$(curl -s -X POST $BASE/api/v1/auth/sso/exchange \
  -H 'Content-Type: application/json' -d "{\"ticket\":\"$TICKET\"}" | jq -r .access_token)
# 3) 直接调
curl -s $BASE/api/sessions -H "Authorization: Bearer $OUR_TOKEN" | head -c 200
```

### 4.3 业务验收表（联调当天逐条打勾）

| # | 场景 | 期望 |
|---|---|---|
| 1 | 员工 A 在 sga 提问 | 我们的 `t_chat_session` 里 A 名下多一条 |
| 2 | A 打开历史 | 能看到这条；**B 登录后看不到** |
| 3 | A 追问“那意见工单呢” | 沿用上一轮时间/地点口径，同一 session |
| 4 | A 选了一个没权限的库 | 403，前端提示“无权限” |
| 5 | 前端上传一个 PDF（通道 B） | 202 → 轮询 done → 列表出现，owner 是 A |
| 6 | 删除该文档 | 只有上传者/管理员能删 |
| 7 | 30 秒以上的复杂问题 | 不超时（sga read-timeout ≥120s） |

### 4.4 灰度与回滚

- 两条通道都是**显式请求头/新端点**触发的，不影响现有账号密码登录
- 回滚：清空 `INTERNAL_SERVICE_TOKEN` / `SSO_SHARED_SECRET` 并重启 → 通道立即失效，原有登录不受影响
- 建议先只在测试环境打开，验收通过后再上生产

---

## 5. 还需你确认的两个参数

1. **sga 前端访问本平台的来源**（协议+域名+端口），用于 CORS 白名单
2. **联调测试账号的 `login_name`**（一名普通员工 + 一名管理员更佳）

这两个给我，我就可以开始写代码（约 150 行，含单测），预计一次部署就能联调。
