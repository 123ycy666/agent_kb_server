---
name: project-secretary-one-rule-doc
description: 问答不带 skill_id；全系统只维护 skills/判据规则.md 一篇规则；知识库里只放这一篇
metadata:
  node_type: memory
  type: project
---

**结论**
- `/api/ask` **没有也不该有 `skill_id`**。对话就是对话、技能接口就是技能接口，两条路不为对方做适配。
- 全系统只维护 `skills/判据规则.md` **一篇**规则（判断标准 + 比较 + 问法归一 + 回答方向）。《分析规则》《问法归一》已并入并删除。
- **知识库里只放这一篇规则**，技能文档不进库。

**Why**
- 「调用方传 skill_id」是上一轮臆造出来的，前端从来没传过。
- 技能文档进知识库会跟规则文档抢检索名额：实测问「是哪些项目」，技能文档拿 0.972、规则文档只有 0.785，模型就照技能文档的模板答，而不是照规则答 —— 这就是"预检索判据会挑错标准"的根因。
- 检索只认这一篇（`agent.rule_slices` 按 doc_name 过滤），挑错标准从根上不可能再发生。

**How to apply**
- 改规则只改 `skills/判据规则.md` 一处，然后跑 `tools/deploy_update.py`（技能路径直接读）+ `tools/kb_sync.py`（问答检索）；kb_sync 会把本地已删的从库里一并清掉。
- 别再往知识库加技能文档，也别再给 `/api/ask` 加 `skill_id` 这类入参。
- 顺带修的 bug：`name_fix` 把常用词「资金」同音改写成「紫金台区」，把整句意思弄没（模型只能回「未找到相关信息」）；已在 `name_fix.NO_PARTIAL_EXTRA` 补进禁入词。实测 40 个常用业务词里只有它被误改。

相关：[[project-secretary-kb-single-index-trap]] [[project-secretary-prompt-maintenance]]
