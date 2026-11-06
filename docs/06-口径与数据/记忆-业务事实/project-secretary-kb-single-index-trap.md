---
name: project-secretary-kb-single-index-trap
description: 4.5 的单库模式（SECRETARY_KB_SINGLE）会用 .env 的 DEFAULT_INDEX_ID 锁住知识库管理面，必须与检索索引 KB_IDS 对齐
metadata:
  node_type: memory
  type: project
---

**结论**：4.5 起服务处于**单库模式**（`SECRETARY_KB_SINGLE=1`，代码默认开）—— 知识库**管理面**（`/kb` 页、`GET/POST/DELETE /api/kb/**`）只暴露 `.env` 里的 `DEFAULT_INDEX_ID`。
**它必须与问答检索用的 `config.KB_IDS` 是同一个索引**，否则：管理面管 A 库、问答检索 B 库，往控制台传的规则文档**永远不被检索到**。

**2026-09-23 真实踩坑**：`.env` 的 `DEFAULT_INDEX_ID=igjhr8giyb`（另一个库），而检索用的是 `r57xtq9ypm`（16 篇规则文档都在）。
表现：`GET /api/kb/indices/r57xtq9ypm/documents` 返回 **0 篇 + `"index_id":"igjhr8giyb"` + `"locked":true`** —— 看着像"知识库被删了"，其实一篇没少，只是列的不是那个库。
修法：**把 `.env` 的 `DEFAULT_INDEX_ID` 改成检索索引**（`r57xtq9ypm`），重启即恢复；本地与线上都要改（两边 `.env` 同源）。

**How to apply**
1. 怀疑"知识库空了"时，先分清两边：`tools_kb.kb_search()` 走**检索面**（`config.KB_IDS`，实测正常）；`/api/kb/**` 走**管理面**（单库模式锁定 `DEFAULT_INDEX_ID`）。
2. 判据：响应里的 `index_id` 字段如果不是你要的那个库，就是 `DEFAULT_INDEX_ID` 没对齐。
3. 上传规则文档用 `tools/kb_sync.py`（直连百炼 SDK，不受单库模式限制），或先对齐 `DEFAULT_INDEX_ID` 再用管理面。
4. `tools/deploy_update.py` **保留服务器 .env**，所以这个改动会一直在；但换服务器/重建 `.env` 时会回到 `.env.example` 的默认值，需重新对齐。

[[project-secretary-kb-retrieval-auth]] [[project-secretary-deploy-online]]
