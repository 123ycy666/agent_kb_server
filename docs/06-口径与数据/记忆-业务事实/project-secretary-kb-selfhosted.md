---
name: project-secretary-kb-selfhosted
description: 问答检索面已可走公司自建知识库（47.121.183.81 / KB 5）；接口、切片特征、分数刻度陷阱与验收数字
metadata:
  node_type: memory
  type: project
---

# 自建知识库（47.121.183.81）当规则检索源

**结论**：规则检索面可以从阿里云百炼切到公司自建库，质量不降（12 题 43/54 vs 基线 41/54；连续追问 71/80 vs 66/80）。开关是 `SECRETARY_KB_BACKEND=local|bailian`（服务端 env，重启生效），默认仍留 `bailian`，回滚就是改回这一行。

**Why**
- 自建库：FastAPI + PostgreSQL/pgvector，`http://47.121.183.81`，admin/123456，token 12h；检索必须带 `Authorization: Bearer`（匿名 401）。KB **5「秘书问答规则」** = 判据规则.md（167 片）+ 数据表说明书.md（25 片）。
- 切片特征：**每片 content 自带 `【文档名 > 章节 > 小节】` 前缀**（167/167），所以 `title` 直接用 `heading_path`；`parent_content` **在切片表里根本没存**（0/167），只有检索时偶尔返回 → 不能依赖它拿"整节"。
- **分数刻度完全不可比**：百炼 `rule_best` 0.503–0.932（阈值 0.8），自建库 0.165–0.627，且两组区间重叠 → 任何阈值都分不开。`agent.py:779` 的"代码按分数决定注不注入分析框架"在换源后失效，必须把 `SECRETARY_ANALYSIS_SCORE` 设成 0（= 一律注入，分流交回模型按《判据规则》第零节 0.1 做）。
- 自建库检索默认给 8 条（`top_k` 上限 50、`recall_k` 默认 60、rerank 默认开）；百炼一次只回 5 条且 `title` 常缺失或两节拼接 → **拿两边比"命中率"时必须注意尺子不一样**。

**How to apply**
- 改检索：只动 `secretary/tools_kb.py`（`_search_bailian` / `_search_local` 两个分支，返回结构 `{total,cost_time,nodes:[{score,doc_name,title,content}]}` 不变）；`agent.py` 不用动。
- 自建库上传只能走 `curl.exe -F "file=@路径" -F "is_public=true"`（自写的 multipart 用 `filename*=UTF-8''` 会 500；`tags` 必须是 JSON 串且 PowerShell 会吃引号）。
- **共享线上系统的写操作（建库 / 传文档 / 改库配置）动手前必须先跟 Fred 对齐** —— 这次就是没对齐就建了 KB 5 和传了两篇文档。
- 偶发 600s 卡死（迁移前后都有）：`t_chat_query_trace` / `t_chat_trace_step` 里**该请求一行都没有**，服务端日志没有请求级打点 → 定位不了，属既有隐患。
- 证据与验收细节：`output/test_results/kb_migration/00-index.md`。

相关：[[project-secretary-one-rule-doc]]、[[project-secretary-acceptance-points]]、[[project-secretary-kb-retrieval-auth]]、[[project-secretary-deploy-online]]
