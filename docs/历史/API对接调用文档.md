# 知识库系统 · 对外 API 对接文档

> 整理时间：2026-10-12 ｜ 线上地址：`http://47.121.183.81`
> 交互式文档（Swagger）：`http://47.121.183.81/docs` ｜ OpenAPI：`http://47.121.183.81/openapi.json`
> 本文档中的**请求与响应均为线上真实实测结果**（除特别标注外）。

---

## 0. 快速上手（TL;DR）

| 你要做的事 | 调用 |
|---|---|
| **认证** | `POST /api/v1/auth/login`（用**专用账号**登录）拿 JWT，之后所有请求带 `Authorization: Bearer <JWT>` |
| 建知识库 | `POST /api/v1/kb` |
| 上传文档 | `POST /api/v1/kb/{kb_id}/documents`（multipart，**异步受理**） |
| 拿上传结果 | 轮询 `GET /api/v1/tasks/{task_id}` |
| 文档列表 | `GET /api/v1/kb/{kb_id}/documents` |
| 删除文档 | `DELETE /api/v1/documents/{doc_id}` |
| 下载原文件 | `GET /api/v1/documents/{doc_id}/download`（**需鉴权**） |
| **预览原文件** | `GET /api/v1/documents/{doc_id}/content`（inline，需鉴权，前端走 Blob） |
| 冲突提醒 / 处理 | `GET /api/v1/conflicts` → `POST /api/v1/conflicts/{id}/resolve` |

> ⚠️ **2026-10-12 变更**：系统**已移除「应用身份 API Key」**（`kb_live_*` 令牌），
> 对接请使用**专用账号 + JWT**，见第 2 节。

---

## 1. 通用约定

- **Base URL**：生产 `http://47.121.183.81/api/v1`；本机 `http://127.0.0.1:8000/api/v1`
- ⚠️ **必须用 IP 访问**：nginx 该站 `server_name` 是 `47.121.183.81`。用域名或 `127.0.0.1` 会落到默认站
  （`xibuyj.com`，其 `/api/ → 11999`）→ 返回 **502**。
- **认证（单一通道）**：`Authorization: Bearer <JWT>`。
  JWT 由 `POST /api/v1/auth/login` 取得，**有效期 12 小时**，过期后重新登录即可。
  > ⚠️ 2026-10-12 起，系统**已移除「应用身份 API Key」**（`kb_live_*` 令牌）。
  > 所有调用方（含对接系统）统一使用**账号登录**拿 JWT。
- **分页**：`?page=1&page_size=20`（page_size ≤ 200），响应 `{items,total,page,page_size}`
- **错误体**：`{"detail":{"code":"XXX","message":"中文说明","detail":null}}`
  - `401` 未认证 ｜ `403` 无权限（如 `NOT_OWNER`）｜ `404` 不存在**或**无权限 ｜ `400` 参数错误
- **时间**：ISO-8601 UTC，如 `2026-10-12T02:57:09.699398Z`（前端按需转本地时区）
- **CORS**：跨域调用需后端 `.env.production` 的 `CORS_ORIGINS` 白名单包含你的来源。
- ⚠️ **当前仅 HTTP、无 HTTPS**：账号密码与 JWT 均为**明文过公网**。
  若在不受信网络调用，请自行确保链路安全（后续会补 HTTPS）。

---

## 2. 认证（对接方必读）

### 2.1 用专用账号登录，拿 JWT

系统**不再提供应用令牌**。对接方使用管理员分配的**专用账号**登录：

```http
POST /api/v1/auth/login
Content-Type: application/json

{"username":"integration","password":"<管理员分配的密码>"}
```

实测响应（HTTP 200）：

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 43200,
  "user": {"id": 2, "username": "integration", "display_name": "对接账号(同事)"},
  "roles": ["admin"],
  "permissions": ["audit:read","conflict:read","conflict:resolve","doc:delete","doc:read",
                  "doc:tag","doc:update","doc:upload","kb:create","kb:delete","kb:read",
                  "kb:update","search:query","system:config","system:scan","tag:manage",
                  "tag:read","user:manage","user:read"]
}
```

拿到后，**所有业务接口**都带上这个头：

```bash
curl -H "Authorization: Bearer $ACCESS_TOKEN" \
  http://47.121.183.81/api/v1/kb
```

- `expires_in` = **43200 秒（12 小时）**。
  ⚠️ **对接方必须实现「401 自动重登」**：收到 401 → 用账号密码重新登录 → 换新 token 重试。
- 密码错误返回 `401` + `{"code":"BAD_CREDENTIALS"}`。
- 其他认证接口：`GET /auth/me`（当前身份与权限）、`POST /auth/change-password`。

### 2.2 对接身份说明（为什么是账号而不是应用令牌）

原设计有「应用身份」（`kb_live_*` 令牌：独立于账号、永不过期、可单独吊销），
**现已按需求移除**，统一到账号体系。对接方需注意：

| 维度 | 现在（账号 + JWT） |
|---|---|
| 凭据来源 | 管理员分配的专用账号（如 `integration`） |
| 有效期 | **12 小时**，需自动重登 |
| 上传者身份 | 即该账号（文档的 `owner_id` = 该账号 id） |
| 审计留痕 | 可区分（`actor_id` / `actor_name` 记录为该账号） |
| 停用/改密 | 管理员在「用户管理」页面操作 |

> **对接方最小实现**：把「登录 → 缓存 token → 带 token 调用 → 401 时重登」封成一个 HTTP 客户端即可。

---


## 3. 知识库（KB）

### 3.1 创建

```http
POST /api/v1/kb
Content-Type: application/json

{"name":"公司制度库","description":"行政制度","chunk_size":512,"chunk_overlap":64}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | ✅ | 1~128 字 |
| `description` | ✖ | |
| `embedding_model` | ✖ | 不传则用当前档位默认（`online/text-embedding-v4`，1024 维） |
| `chunk_size` | ✖ | 默认 512（64~4096） |
| `chunk_overlap` | ✖ | 默认 64（0~1024） |

实测响应（HTTP 200）：

```json
{
  "id": 4, "name": "API对接测试库", "embedding_model": "online/text-embedding-v4",
  "embedding_dim": 1024, "chunk_size": 512, "chunk_overlap": 64, "status": "active",
  "created_at": "2026-10-12T02:57:09.194961Z",
  "document_count": 0, "chunk_count": 0, "visible_document_count": 0
}
```

### 3.2 其他

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/kb?page=1&page_size=20` | 列表（分页） |
| GET | `/kb/{kb_id}` | 详情（含统计） |
| PATCH | `/kb/{kb_id}` | 改名/描述/切片参数/归档 |
| DELETE | `/kb/{kb_id}` | **级联删除**库内全部文档与切片 |
| GET | `/kb/{kb_id}/stats` | 统计（文档数/切片数/可见数） |
| POST | `/kb/{kb_id}/embedding-model` | 切换向量模型（**需重建索引**） |
| POST | `/kb/{kb_id}/reindex` | 重建索引（`?only_stale=true` 默认只重建过期切片） |

> ⚠️ **切片参数改了不会自动重切**，接口会在 `description` 里追加提醒；
> ⚠️ **切换 embedding 模型必须 `reindex`**，否则旧向量在不同向量空间里会**静默检索不到**。

## 4. 上传文档

### 4.1 先理解：这是「异步受理」，不是同步入库

```
POST /kb/{kb_id}/documents   →  202 {task_id}        ← 只是"已受理"
        ↓  后台 Celery worker 解析 → 切片 → 向量化 →（自动冲突检测）
GET /tasks/{task_id}         →  done + result{doc_id,chunk_count,...}  ← 真正的结果在这
```

⚠️ **202 响应里没有 `doc_id`**！`doc_id`、`chunk_count`、`conflict_count` 全部在
**任务完成后的 `task.result`** 里。直接读 202 的 `conflict_count` 会恒为 0，导致「检出冲突」提示静默失效。

### 4.2 请求

```http
POST /api/v1/kb/{kb_id}/documents
Content-Type: multipart/form-data
Authorization: Bearer <token>

file      : 二进制文件（必填）
is_public : true / false（可选，默认 false）
tags      : JSON 数组字符串（可选，默认 "[]"）
```

**curl 实测：**

```bash
curl -X POST "http://47.121.183.81/api/v1/kb/4/documents" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@./制度.md" \
  -F "is_public=true" \
  -F 'tags=["行政","制度"]'
```

实测 202 响应：

```json
{
  "task_id": "80494e1f-c6e8-4cb8-bc05-3cbbd046683b",
  "status": "accepted",
  "message": "已受理，正在后台解析与向量化；可轮询任务状态获取最终结果",
  "doc_id": null, "conflict_count": 0, "conflict_docs": [], "warnings": []
}
```

### 4.3 标签到底怎么传（重点）

- `tags` 是 **multipart 的一个表单字段**，值是**字符串形式的 JSON 数组**：
  - ✅ `-F 'tags=["行政","制度"]'`
  - ✅ 前端：`form.append('tags', JSON.stringify(['行政','制度']))`
  - ❌ `tags=行政,制度`（非法）
  - ❌ 传真正的 multipart 多值字段（后端按字符串解析）
- **最多 10 个**（超出按 `max_length=10` 校验）；标签名**自由填写**，系统会自动登记进标签词表。
- **传空数组 `"[]"` = 不带标签**。
- 上传后还可改：见第 7 节。

### 4.4 支持的格式与限制

以 `GET /api/v1/system/formats` 为准（实测）：

```
.csv .docx .markdown .md .pdf .txt .xls .xlsm .xlsx
```

- ⚠️ **不支持 OCR**：PDF 必须是**纯文本型**（扫描件无法解析）。
- 单文件上限由后端 `MAX_UPLOAD_MB` 控制（**当前 100MB**）；nginx 侧 `client_max_body_size` 需与之对齐，
  否则会先被 nginx 拦成 413。
- **内容去重**：同一知识库内 **SHA256 相同**的文件会被拒绝（实测报错：
  `该文件内容与已有文档《xxx.md》完全相同（SHA256 一致），无需重复上传。`，任务状态 `failed`）。

### 4.5 其他上传错误码

| HTTP | code | 含义 |
|---|---|---|
| 400 | `UNSUPPORTED_FORMAT` | 扩展名不支持 |
| 400 | `FILE_TOO_LARGE` | 超过 `MAX_UPLOAD_MB` |
| 400 | `BAD_TAGS` | tags 不是合法 JSON 数组 |
| 404 | `KB_NOT_FOUND` | 知识库不存在 |

---

## 5. 任务轮询（上传后必做）

```http
GET /api/v1/tasks/{task_id}
```

`status` 取值：`pending` → `running` → `done` / `failed` / `conflict_pending`

实测（done）：

```json
{
  "id": "80494e1f-c6e8-4cb8-bc05-3cbbd046683b",
  "task_type": "doc_ingest", "kb_id": 4,
  "status": "done", "progress": 100, "stage": "ready", "error_msg": null,
  "result": {
    "doc_id": 2,
    "version_no": 1,
    "chunk_count": 5,
    "chunk_stats": {"count":5,"avg_chars":54.2,"max_chars":64,"min_chars":41,"total_chars":271},
    "conflict_count": 0, "conflict_docs": [], "is_new_version": false,
    "parse_meta": {"heading_count": 4}
  },
  "created_at": "2026-10-12T02:57:09.685363Z",
  "started_at": "2026-10-12T02:57:09.697102Z",
  "finished_at": "2026-10-12T02:57:10.474355Z"
}
```

**前端建议**：上传后每 1~2 秒轮询一次，最多 60 次；`done` 时用 `result.doc_id` 刷新列表；
`failed` 时展示 `error_msg`；`conflict_pending` 时提示"需人工处理冲突"并跳转冲突页。

```js
// 轮询伪代码
async function waitTask(taskId) {
  for (let i = 0; i < 60; i++) {
    const t = await http.get(`/tasks/${taskId}`)
    if (['done', 'failed', 'conflict_pending'].includes(t.status)) return t
    await new Promise(r => setTimeout(r, 1500))
  }
  throw new Error('处理超时')
}
```

任务列表：`GET /api/v1/tasks?status=done&task_type=doc_ingest&page=1&page_size=20`

## 6. 文档列表与字段说明（前端刷新用）

```http
GET /api/v1/kb/{kb_id}/documents?page=1&page_size=20&parse_status=ready&has_conflict=false&tags=行政&tags=制度
```

| 查询参数 | 说明 |
|---|---|
| `page` / `page_size` | 分页（page_size ≤ 200） |
| `parse_status` | 按解析状态过滤：`ready` / `failed` … |
| `has_conflict` | `true` / `false` |
| `tags` | **可重复传**，语义是「**同时包含**这些标签」（AND） |

### 实测响应（关键字段全在此）

```json
{
  "items": [{
    "id": 2,
    "kb_id": 4,
    "root_id": 2, "version_no": 1, "is_active": true,
    "filename": "apitest.md",
    "file_ext": ".md",
    "file_size": 497,
    "content_hash": "df1dc61cc271fb17384ccc8d91576a02c964496c0269a5fdb6a71c00f781978e",
    "parse_status": "ready", "parse_error": null,
    "has_conflict": false,
    "chunk_count": 5,
    "storage_path": "/data/uploads/kb_4/20261012025709685156_apitest.md",
    "visibility": "public",
    "owner_id": 1,
    "uploaded_by_app": null,
    "created_at": "2026-10-12T02:57:09.699398Z",
    "updated_at": "2026-10-12T02:57:09.699398Z",
    "tags": ["行政", "制度"],
    "conflict_count": 0
  }],
  "total": 1, "page": 1, "page_size": 20
}
```

**你点名要的字段，逐个对应：**

| 你要的 | 字段 | 说明 |
|---|---|---|
| **上传人 id** | `owner_id` | 对应用户表 id（1 = admin）。API Key 上传时为绑定的服务账号 |
| **上传人（应用）** | `uploaded_by_app` | 若由 API 应用上传，这里是 app_id |
| **上传时间** | `created_at` | ISO8601 UTC |
| 原文件位置 | `storage_path` | ⚠️ **是后端容器内路径，不是 URL**，见第 8 节 |
| 标签 | `tags` | 字符串数组 |
| 是否有冲突 | `has_conflict` + `conflict_count` | 前端据此显示红点/角标 |
| 文件大小/类型 | `file_size` / `file_ext` | |
| 版本 | `version_no` / `is_active` / `root_id` | |

> **列表刷新**：没有增量接口，直接重新 `GET` 列表即可（按 `created_at desc` 排序）。
> 上传完成后用 `result.doc_id` 定位新文档；若想省流量，可用 `total` 判断有没有变化。

其他：`GET /documents/{id}`（详情）、`/documents/{id}/chunks`（切片预览）、
`/documents/{id}/versions`（版本历史，返回该文件所有版本）。

---

## 7. 原文件位置与「文件预览」

### 7.1 `storage_path` 是什么（⚠️ 它不是 URL）

`GET /kb/{kb_id}/documents` 返回的 `storage_path` 形如：

```
/data/uploads/kb_3/20261012021014282638_工作时间.docx
```

这是**后端容器内的绝对路径**（文件实际落在 Docker 命名卷 `kb_prod_uploads`）。
**它对浏览器没有意义，不能当 URL 用** —— 它的用途是**排障**（文件到底存哪了、是否被人为删掉）。

系统**没有静态文件挂载**，也不存在「把 uploads 目录直接暴露成公网地址」的入口，
**一切取件都走带鉴权的文档接口**。

### 7.2 取原文件的两个接口

| 接口 | 响应头 | 权限 | 用途 |
|---|---|---|---|
| `GET /api/v1/documents/{doc_id}/download` | `Content-Disposition: attachment` | **上传者本人或管理员** | **下载**原件（触发保存） |
| `GET /api/v1/documents/{doc_id}/content` | `Content-Disposition: inline` | 可见即可（更宽松） | **内联预览**（浏览器原地渲染） |

`/content` 实测响应头：

```
HTTP/1.1 200 OK
content-type: application/octet-stream
content-length: 16374
content-disposition: inline
```

### 7.3 前端怎么用（⚠️ 必须走 Blob）

两个接口**都需要 `Authorization` 头**，所以
`<iframe src="/api/v1/documents/1/content">`、`<img src>`、`window.open(直链)`
**全部不可用**（这些标签带不上 token，会 **401**）。

正确做法：**拉成 Blob → objectURL → 塞进 `<iframe>`**。项目里已封装：

```ts
// web/src/api/index.ts
const { url, revoke } = await docApi.preview(docId)
// <iframe src={url} title={filename} />
// ⚠️ 抽屉关闭时务必 revoke()，否则 objectURL 一直占内存
```

- **能预览**：PDF、图片、纯文本（浏览器原生支持的类型）。
- **不能预览**：`.docx` / `.xlsx` 等 —— 浏览器不认，`Content-Type` 会退化成
  `application/octet-stream`，只能「下载后本地打开」。
  若必须在线预览 Office 文档，需引入第三方预览服务（OnlyOffice / kkFileView 等）。

### 7.4 附：文件丢失的情况

若文件被从磁盘删除（本机 `data/uploads/` 曾被清空过），
`download`/`content` 均返回 `404 FILE_MISSING`，**但文档与切片仍在、检索不受影响**。
前端遇到该错误应提示「原件已丢失，可重新上传」，而不是以为整条记录坏了。

## 8. 标签管理

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/documents/{doc_id}/tags` | **覆盖式**设置，body `{"tags":["行政","制度"]}`，传 `[]` 即清空 |
| DELETE | `/documents/{doc_id}/tags/{tag_name}` | 移除单个标签（`tag_name` 要 URL 编码） |
| GET | `/tags?page=1&page_size=20` | 标签词表（含 `usage_count`） |
| GET | `/tags/options` | **下拉候选项**（前端选择框直接用这个） |
| POST / PATCH / DELETE | `/tags`、`/tags/{id}`、`/tags/{name}` | 词表增改删 |
| POST | `/tags/rename` | 重命名（批量） |

- 权限点 `doc:tag`，且**只能改自己上传的文档**。
- 标签是**自由字符串**，不需要预先创建；系统会自动登记进词表用于下拉复用。
- 覆盖式设置后，标签会**冗余下沉到切片**，供检索时按标签过滤。

```bash
curl -X POST "http://47.121.183.81/api/v1/documents/2/tags" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  --data-binary @- <<'EOF'
{"tags":["行政","制度","2026"]}
EOF
```

> ⚠️ 在 Windows Git Bash 里，**不要**把中文直接写在 `-d '...'` 里（会被转成 GBK 导致
> `400 error parsing the body`）。用 `--data-binary @文件` 或 heredoc 传。

## 9. 可见性（公开 / 私有）

```http
PATCH /api/v1/documents/{doc_id}/visibility
{"visibility": "public"}     # 或 "private"
```

- 上传时 `is_public=false`（默认）→ **private：仅上传者本人可见可检索**（管理员在管理视图可见）。
- 这是**最小权限**设计：不做「默认公开」，避免漏勾选导致文档被所有人搜到。
- 权限点 `doc:update`，仅上传者或管理员。

---

## 10. 删除文档、版本恢复、重建索引

### 10.1 删除

```http
DELETE /api/v1/documents/{doc_id}
```

实测响应：

```json
{"ok": true, "message": "文档《apitest.md》已删除", "warnings": []}
```

- 权限点 `doc:delete`，**仅上传者本人或管理员**（公开文档也不许他人删）。
- ⚠️ **`warnings` 必须展示给用户**，它不是装饰字段：
  - 删除「覆盖过旧版的新版」时，后端会**自动把旧版本复活**并在这里告知，
    如 `已自动恢复被其取代的旧版本《xxx》(v1)，已重新可检索`；
  - 若旧版切片向量已过期，还会提示「需重建索引后才能检索」。
  - **静默忽略它，用户会以为删干净了，实际旧版本已回到检索里。**

### 10.2 版本恢复 / 重建索引

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/documents/{doc_id}/restore` | 把某个历史版本恢复为活跃版本 |
| POST | `/documents/{doc_id}/reindex` | 重建该文档的向量与关键词索引 |

---

## 11. 冲突：产生、提醒、查询、处理

### 11.1 冲突从哪来

上传的文档在后台任务里会**自动做冲突检测**（向量召回 → rerank → LLM 判定
「矛盾 / 取代 / 重复」）。命中后：

- 生成一条 `ConflictRecord`；
- **只把「冲突涉及的那几片」切片隔离**（`is_retrievable=false`），**同文档其余切片照常可检索**；
- 文档打上 `has_conflict=true`；
- 上传任务状态变为 **`conflict_pending`**。

### 11.2 「提醒」怎么拿（三种口径）

| 场景 | 调用 |
|---|---|
| 上传后立刻知道 | 轮询 `GET /tasks/{id}`，状态 = `conflict_pending` |
| 列表页红点 | `GET /kb/{kb_id}/documents` 里的 `has_conflict` / `conflict_count` |
| 顶部角标 | `GET /conflicts/stats` → `pending` |
| 冲突待办列表 | `GET /conflicts?status=pending&page=1&page_size=20`（可加 `kb_id` / `doc_id`） |

```json
{ "total": 0, "pending": 0, "resolved": 0, "ignored": 0, "by_type": {}, "by_relation": {} }
```

### 11.3 冲突记录字段（`ConflictOut`）

| 字段 | 说明 |
|---|---|
| `id` | 冲突 id（处理时用） |
| `new_doc_id` / `new_doc_name` | 新上传的文档 |
| `old_doc_id` / `old_doc_name` / `old_doc_version` | 被撞到的已有文档 |
| `new_chunk_id` / `old_chunk_id` | 冲突的**具体切片**（切片级隔离的依据） |
| `conflict_type` / `relation` | 冲突类型 / 关系（矛盾、取代、重复…） |
| `similarity_score` / `rerank_score` | 召回与重排分数 |
| `reason` | **LLM 给出的判定理由**（给人工决策看） |
| `new_snippet` / `old_snippet` | **左右对照片段**（前端并排展示） |
| `status` | `pending` / `resolved` / `ignored` |
| `resolution` / `resolved_by` / `resolved_at` | 处理结果与处理人 |

### 11.4 处理冲突

```http
POST /api/v1/conflicts/{conflict_id}/resolve
{"resolution": "overwrite", "note": "新版为准"}
```

| `resolution` | 语义 | 后果 |
|---|---|---|
| `overwrite` | 新版覆盖旧版 | **仅隔离被撞的那几片旧切片**；旧文档整篇下架**仅当**其全部切片都被取代 |
| `keep_old` | 保留旧版，丢弃新上传 | 删除新文档（含物理文件） |
| `coexist` | 两者并存 | 解除新文档冲突切片的隔离，仅留记录 |
| `cancel` | 取消本次上传 | 同 `keep_old` |
| `ignore` | 忽略该冲突 | 解除隔离，标记 `ignored` |

- 权限点 `conflict:resolve`，且**只能处理「自己上传文档」的冲突**（管理员不受限）；
- 处理完若该文档**已无其他 pending 冲突**，会自动解除检索排除。

### 11.5 主动检测 / 全库巡检

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/conflicts/detect` | `conflict:resolve` | body `{"doc_id":N}`，对单篇重跑检测 |
| POST | `/conflicts/scan` | `system:scan` | body `{"kb_id":N,"only_unscanned":true}`，**全库巡检** |

> 日常检测只比对「上传者有权访问的文档」（防信息侧信道），因此
> 「受限文档 ↔ 公开文档」的矛盾**不会被自动发现**，需管理员定期跑 `scan` 兜底。

## 12. 检索与问答（实测通过）

```http
POST /api/v1/search
{
  "query": "弹性工作制 上班时间",
  "kb_ids": [3],
  "tag_filters": [],          // 可选，标签过滤（AND）
  "top_k": 5,                 // 默认 5
  "rerank": true,
  "include_conflict": false   // 是否召回"因冲突被隔离"的切片
}
```

实测响应（`took_ms=729`，真实向量召回 + 重排）：

```
total=3  recall=3
  rerank=0.5116 | 工作时间.docx | 第四条 公司实行弹性工作制：到岗时间 8:50-9:30 ...
  rerank=0.1814 | 工作时间.docx | 第三条 标准工作时间改为周一至周四 9:00-18:00 ...
  rerank=0.1645 | 工作时间.docx | 第五条 特殊岗位实行轮班制 ...
```

- 每条结果含 `content`（**已带【标题路径】前缀**）、`parent_content`（同章节父块上下文，
  供问答喂 LLM）、`fused_score` / `rerank_score` / `has_conflict` / `tags`。
- ⚠️ **权限条件由后端强制注入**，不接受调用方指定。
- `tag_filters` 若把结果全部过滤掉，会返回 `filter_notice`（含放宽建议）——**前端要展示**，
  否则用户会误以为「库里有却搜不到」。

问答（RAG）：`POST /api/v1/chat`，body 同构（`query` / `kb_ids` / `top_k` / `stream`），
返回 `answer` + `citations[]`。

---

## 13. 完整调用时序（上传一份带标签的文档）

```text
1) POST /auth/login                          → 拿 JWT（12h 有效）
2) POST /kb                                  → kb_id
3) POST /kb/{kb_id}/documents                → 202 {task_id}      （multipart）
4) GET  /tasks/{task_id}  轮询 1~2s/次        → done               （拿 result.doc_id）
                                             → conflict_pending   （去冲突页处理）
                                             → failed             （展示 error_msg）
5) GET  /kb/{kb_id}/documents                → 刷新列表
6) 预览：GET /documents/{id}/content (待补) 或 fetch+blob 方式
7) 冲突：GET /conflicts?status=pending → POST /conflicts/{id}/resolve
```

---

## 14. 高频坑清单（全部实际踩过）

| # | 坑 | 现象 | 正解 |
|---|---|---|---|
| 1 | **用错 Host 访问** | 首页正常但接口 502 | 只用 `http://47.121.183.81`；别用域名/127.0.0.1 |
| 2 | **把 202 当成功** | `doc_id`/`conflict_count` 恒为空/0 | 必须轮询 `/tasks/{id}`，结果在 `result` |
| 3 | **tags 传错格式** | `400 BAD_TAGS` | 传 **JSON 数组字符串** `"[\"行政\"]"` |
| 4 | **重复上传** | 任务 `failed`，提示 SHA256 相同 | 同库内内容相同的文件会被拒绝，先删旧的或改内容 |
| 5 | **Windows 命令行中文** | `400 error parsing the body` | Git Bash 中 `-d '...'` 的中文会变 GBK；用 `--data-binary @文件` |
| 6 | **直链预览** | 401 / 只会下载 | 两个取件接口都要 `Authorization`；预览用 `/documents/{id}/content` 且**必须走 Blob**，见第 7 节 |
| 7 | **storage_path 当 URL** | 404 | 它是**容器内路径**，不是可访问地址 |
| 8 | **切换向量模型不重建** | 检索**静默**返回空 | 必须 `POST /kb/{id}/reindex` |
| 9 | **忽略 warnings** | 以为删干净了，旧版其实复活了 | 展示 `OkResponse.warnings` |
| 10 | **对接凭据** | `admin/admin123456` 登录 401 | 管理员密码见 `.env.production` 的 `BOOTSTRAP_ADMIN_PASSWORD`；**对接请用专用账号** |
| 11 | **跨用户操作** | `403 NOT_OWNER` | 删/改/下载他人文档会被拒；管理员不受限 |
| 12 | **大文件** | `413` 或 `400 FILE_TOO_LARGE` | nginx `client_max_body_size` 要与 `MAX_UPLOAD_MB`（100）对齐 |
| 13 | **用旧的应用令牌** | `kb_live_xxx` → **401** | 应用令牌已于 2026-10-12 移除，改用**账号登录拿 JWT** |

---

## 15. 建议补充 / 待确认项（对接方需要拍板的）

| # | 建议 | 原因 | 优先级 |
|---|---|---|---|
| 1 | ~~新增 `GET /documents/{id}/content`（inline 预览）~~ | ✅ **已完成**（2026-10-12），见第 7 节 | — |
| 2 | 上传/冲突的**主动通知**（webhook / SSE） | 现在只能轮询；对接方无法及时感知 `failed` / `conflict_pending` | 🟠 中 |
| 3 | 列表与详情补 **`owner_name`（上传人姓名）** | 现在只有 `owner_id`，前端要显示"谁传的"得再查用户表 | 🟠 中 |
| 4 | 文档列表支持 **`owner_id` / 时间范围**过滤 | 现在只能按 `parse_status` / `has_conflict` / `tags` 过滤 | 🟡 低 |
| 5 | **补齐 HTTPS** | 当前是纯 HTTP，账号密码明文过公网，公网对接有泄露风险 | 🔴 高 |
| 6 | 预览支持**短时签名直链** | 若外部系统要 `<iframe>` 嵌我们的文件，需要免鉴权直链（要防泄漏） | 🟡 低 |

---

## 附录：接口总览（取自线上 `openapi.json`）

```text
认证   POST /auth/login  POST /auth/change-password  GET /auth/me
知识库 GET,POST /kb          GET,PATCH,DELETE /kb/{id}      GET /kb/{id}/stats
       POST /kb/{id}/documents   POST /kb/{id}/embedding-model   POST /kb/{id}/reindex
文档   GET,POST /kb/{id}/documents        GET,DELETE /documents/{id}
       GET /documents/{id}/chunks          GET  /documents/{id}/versions
       GET /documents/{id}/download        GET  /documents/{id}/content   ← 内联预览
       POST /documents/{id}/restore        POST /documents/{id}/reindex
       POST /documents/{id}/tags           DELETE /documents/{id}/tags/{name}
       PATCH /documents/{id}/visibility
任务   GET /tasks             GET /tasks/{id}
冲突   GET /conflicts  GET /conflicts/stats  POST /conflicts/detect
       POST /conflicts/scan  POST /conflicts/{id}/resolve
标签   GET,POST /tags   GET /tags/options   PATCH,DELETE /tags/{...}   POST /tags/rename
检索   POST /search           POST /chat
平台   GET /system/health  /system/info  /system/formats
       GET /models/config  /models/usage   POST /models/switch  /models/test
       GET /audit-logs  /audit-logs/stats  /audit-logs/actions
       GET,POST /users  PATCH /users/{id}   GET /roles  GET /permissions
```

> ⚠️ **`/api/v1/apps`（API 应用管理）已于 2026-10-12 随应用令牌一并移除，现返回 404。**

