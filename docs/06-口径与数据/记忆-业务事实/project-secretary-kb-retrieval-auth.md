---
name: project-secretary-kb-retrieval-auth
description: 百炼知识库两套面：检索面（问答用）只授权了 r57xtq9ypm；口径库 uxht00z9ey 检索会 401
metadata:
  node_type: memory
  type: project
---

**结论**：`config.KB_AGENT_ID = aid-72fa8c…` 这个检索服务**只授权了 `r57xtq9ypm`（ai大脑通用语义知识库）**。拿 `uxht00z9ey`（项目管理秘书口径库）去 `tools_kb.kb_search` 会直接 `HTTP 401 Unauthorized`（2026-09-22 实测）。所以口径库那 3 篇文档问答链路根本检索不到 —— 这是**授权问题，不是代码问题**（换检索知识库要先在百炼控制台给检索服务加库）。

**How to apply**
- 要让问答检索到某篇文档，**必须传进 `r57xtq9ypm`**（或先给检索服务授权别的库，再改 `SECRETARY_KB_IDS`）。
- 分析类问题的判断标准文档《分析规则》就放在 `r57xtq9ypm`，检索后按 `doc_name == '分析规则'` 过滤（`agent.ANALYSIS_DOC`），避免把「补全规则」当成判断标准。
- 上传/删除走**管理面**：线上 `POST|DELETE /api/kb/indices/{index_id}/documents`，或本地 `kb_bailian.BailianKnowledgeBase(settings).upload_document(...)`（凭据在 .env：ALIBABA_CLOUD_*、WORKSPACE_ID）。上传后索引异步构建，实测小文件几秒就能检索到。
- 修改线上 `r57xtq9ypm` 是**动生产知识库**：加文档可逆（有删除接口），删之前先看 `GET .../documents`。

[[project-secretary-deploy-online]]
