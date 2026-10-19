# 给 sga 前端：直连 AI 平台的改动说明

> 一句话：**用户登录 sga 之后，前端多调一个接口换一个 token，之后所有请求带上这个 token，就能调我们的全部接口。**

后端**不用改**（除非前端拿不到当前登录名，见第 8 节）。

---

## 1. 唯一新增的接口

```
POST http://47.121.183.81/api/v1/auth/sso/login
Content-Type: application/json

{ "username": "当前登录用户的登录名" }
```

返回（和普通登录完全一致）：

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 43200,
  "user": { "id": 3, "username": "ycy1", "display_name": "ycy1" },
  "roles": ["user"],
  "permissions": ["doc:read", "doc:upload", "kb:read", "..."]
}
```

> `username` 就是你们 sa-token 登录后拿到的登录名（HR 工号对应的 `login_name`），**原样传**，不要传姓名、手机号。

---

## 2. 前端要改的地方（约 10 行）

### 2.1 进 AI 页面前换一次 token

```js
let aiToken = null;

async function getAiToken(loginName) {
  if (aiToken) return aiToken;
  const r = await fetch('http://47.121.183.81/api/v1/auth/sso/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: loginName }),   // 你们登录后已有的用户名
  });
  if (!r.ok) throw new Error('换 token 失败：' + r.status);
  aiToken = (await r.json()).access_token;
  return aiToken;
}
```

### 2.2 之后所有请求带上它

```js
// fetch
fetch('http://47.121.183.81/api/ask', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': 'Bearer ' + await getAiToken(loginName),
  },
  body: JSON.stringify({ question: '供电所有几个', kb_ids: [1] }),
});

// axios：加一个请求拦截器
axios.interceptors.request.use(async (cfg) => {
  if (cfg.url.includes('47.121.183.81')) {
    cfg.headers.Authorization = 'Bearer ' + await getAiToken(loginName);
  }
  return cfg;
});
```

### 2.3 token 过期怎么办

`expires_in` 是 43200 秒（12 小时）。**收到 401 就清掉 `aiToken`，重新调一次 sso/login**，再重试原请求即可。

---

## 3. 能调哪些接口

**全部**。清单见 `docs/对外接口文档.md`，常用：

| 功能 | 方法 + 路径 |
|---|---|
| 提问 | `POST /api/ask` |
| 继续对话 | `POST /api/ask`（带上一轮的 `session_id`） |
| 会话列表 | `GET /api/sessions` |
| 会话明细 | `GET /api/history?session_id=` |
| 评价 | `POST /api/feedback` |
| 知识库列表 | `GET /api/v1/kb` |
| 文档列表 | `GET /api/v1/kb/{kb_id}/documents` |
| 上传文档 | `POST /api/v1/kb/{kb_id}/documents`（multipart） |
| 下载 / 预览 | `GET /api/v1/documents/{id}/download` · `/content` |
| 删除文档 | `DELETE /api/v1/documents/{id}` |
| 冲突提醒 | `GET /api/v1/conflicts` |
| 检索片段 | `POST /api/v1/search` |

---

## 4. 三条联调命令（你们可以直接跑）

```bash
BASE=http://47.121.183.81

# ① 换 token（把 ycy1 换成你们自己的登录名）
TOKEN=$(curl -s -X POST $BASE/api/v1/auth/sso/login \
  -H 'Content-Type: application/json' -d '{"username":"ycy1"}' | jq -r .access_token)

# ② 确认身份
curl -s $BASE/api/v1/auth/me -H "Authorization: Bearer $TOKEN"

# ③ 提问
curl -s -X POST $BASE/api/ask \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"question":"供电所有几个","kb_ids":[1]}'
```

三条都通了，就说明前端接入没问题。

---

## 5. 几个必须知道的点

| 事项 | 说明 |
|---|---|
| CORS | 已放开为 `*`，你们从任何域名/端口调都不会被浏览器拦 |
| token 有效期 | 12 小时；过期重换，用户无感 |
| 用户身份 | 必须与 HR 登录名一致，否则 401 |
| 历史记录 | **自动按人隔离**：A 换的 token 只能看到 A 的会话，B 看不到 |
| 提问耗时 | 简单问题 5~10 秒，复杂分析 15~30 秒，**前端超时请设 ≥120 秒** |
| 上传 | 是异步的：返回 `task_id`，轮询 `GET /api/v1/tasks/{task_id}` 到 `status=done` |
| 免密登录 | 这个接口只校验用户名、不校验密码，**仅限内网/受信环境**；对外网暴露前要先收紧 |

---

## 6. 常见错误

| 返回 | 原因 | 处理 |
|---|---|---|
| 401 `USER_NOT_FOUND` | 登录名在 HR 里不存在或已停用 | 确认传的是 `login_name`，不是姓名 |
| 401 其他 | token 过期 | 重新调 sso/login |
| 403 | 没有该知识库权限 | 用有权限的账号，或让管理员开 |
| 404 | 路径写错 | 注意问数是 `/api/ask`，知识库是 `/api/v1/kb` |

---

## 7. 需要你们确认的一件事

**前端能不能拿到当前登录用户的登录名（login_name）？**

- 能 → 前端改 10 行就完事，后端不用动
- 不能 → 需要你们后端加一个接口（约 5 行），把当前登录名返回给前端：

```java
@GetMapping("/ai/login-name")
public String loginName() {
    return LoginHelper.getLoginUser().getUsername();   // sa-token
}
```

---

## 8. 联调完成的标准

1. 你们前端能拿到 token（第 4 节的 ① ② 通过）
2. 能提问并显示答案（③ 通过）
3. 换个账号登录，**看不到上一个账号的历史**（说明身份隔离生效）
4. 刷新页面后能自动重新换 token，用户不用重新登录

有任何一步卡住，把请求的 **URL + 状态码 + 返回体** 发我，我这边查日志。
