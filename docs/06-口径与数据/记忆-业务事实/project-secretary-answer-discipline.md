---
name: project-secretary-answer-discipline
description: 回答纪律只在 agent.COMMON 一处；判据规则只放检索面；通用问题不许强制查库
metadata:
  node_type: memory
  type: project
---

**结论（2026-10-19 定）**
- **回答怎么写只住一处**：`server/app/secretary/agent.py` 的 `COMMON`（+`CHECK`）。分层：问数 / 问情况判断 / 问原因 / 问办法 / 问预测，**只答被问的那一层**，答完就停，默认不分条。
- **`skills/判据规则.md` 只放检索面**：口径、字段、阈值、问法归一、比较基准、各对象的标准项（仅在问题笼统时用）。**不放回答结构、长度、自查清单、回答骨架**。
- **`ai_prompt.answer_agent` 只放身份与边界**（线上库 `ai_data`，新增行 IS_new=1，旧行置 0）：小智是谁、什么情况不查库、非业务直接答。
- 非业务问题（问候/常识/写作）：答案无业务数字、**问句**无业务词 → 直接放行，不提醒、不加"未调用工具"标注。判据用问句，不用答案（自我介绍会提到业务词，用答案会误拦）。

**Why**
- 改前三套规范方向相反：库里 answer_agent 说"没要求分析就只做数据呈现"，代码里 ANALYSIS_FRAME 与 15 条自检要求"必答项逐项写、必须给建议和成效"，判据规则 §20 标题写着"缺一不可"。冲突下模型只能写全，于是冗余、答非所问。
- 分流判据曾经是"检索到规则就算分析题"（规则只有一篇、业务问题几乎必命中），等于分析模板每题都注入。

**How to apply**
- 要改回答风格：只改 `COMMON`/`CHECK`，别去判据规则里加；改完重启服务。
- 要改口径/阈值：只改判据规则；改完 `tools/deploy_update.py` + `tools/kb_sync.py` 同步到知识库。
- 别再恢复"必答项逐项写 / 必须给建议和成效 / 不设字数上限"这类条款；那正是这次删掉的东西。
- 验收：问数 1–3 行、问情况先给结果、问原因无对策、方案题有动作、闲聊不查库。

相关：[[project-secretary-prompt-maintenance]] [[project-secretary-one-rule-doc]]

## 2026-11-03 追加（会过时的一点）

上面「回答纪律只在 agent.COMMON 一处」在 **main（空壳）** 上已经不成立了：COMMON / CHECK /
`prompts_data.ANSWER_DATA` 按 Fred 要求删除，S4 整段不再注入。要恢复见
`docs/08-历史归档/2026-11-03-删除的提示词规则/汇报口吻-自检清单-作答规则.md`，
分支约定见 `project-secretary-shell-branch`。
