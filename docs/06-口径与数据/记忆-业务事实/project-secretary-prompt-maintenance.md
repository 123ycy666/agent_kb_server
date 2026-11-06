---
name: project-secretary-prompt-maintenance
description: 秘书服务的提示词怎么维护：正文在线上 ai_data.ai_prompt，代码只维护「注入哪几道 key」的名单
metadata:
  node_type: memory
  type: project
---

**结论**：提示词正文全部在线上 `ai_data.ai_prompt`（取 `IS_new=1 AND deleted_flag=0`），`config.PROMPTS` 只决定注入哪几道 key。当前五道：`answer_agent` / `business_rules` / `sql_plan_rules` / `work_order_rules` / `caliber_rules`（2026-10-19 把代码里的 CALIBER 搬进来）。

**码值也不写死**：工单状态、缺陷类型、审批节点等码表从业务库 `dlj_data.t_dict` + `t_dict_data` 动态读（`semantic.codes_menu()`，白名单 `semantic._DICT_CODES`），提示词里不再有码表；`answer_agent` 只留身份与回答边界。

**Why**：Fred 要求「本地不预设任何规则、规则从库里拿」，所以一条取数口径（如「缺陷工单默认排除草稿 work_status=0」）的正确落法是**新增一道 key**，不是写进代码；也**不要追加到 business_rules** —— 那行表头写着「业务规则（所有Agent通用）」，改了会外溢到他们别的 agent。

**How to apply**
1. 加规则：按 `ai_prompt` 列（prompt_key / prompt_name / prompt_content / remark / deleted_flag / version / IS_new，后两列 NOT NULL）插一行 → `config.PROMPTS` 加 key → 本地重启 + `tools/deploy_update.py`（新 key 必须重启才注入）。
2. **改内容不用重启**：`semantic.prompts()` 有 60 秒 TTL，改库最多 60 秒自动生效。
3. 提示词层是「高概率」不是「必然」：同一条草稿排除规则实测 **9/11 次生效**（同一问法重跑 5 次才 5/5）；要 100% 只能落到代码做 SQL 兜底，那就违背「不预设规则」，所以先接受这个概率。
4. 连接账号是 `root@%`，`ai_data` 可写（`tools_db` 的只读闸只挡自己的 run_sql 工具），但只用来新增提示词行；`dlj_data` 业务表仍只读。

[[project-secretary-deploy-online]]
