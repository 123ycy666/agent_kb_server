<!-- 来源：ai_data.ai_prompt（prompt_key=work_order_rules，id=40，更新于 2026-09-23 14:40:53）。由 server/scripts/export_prompts.py 导出，人工复核后使用。 -->

【缺陷工单取数口径】查「缺陷工单」（t_power_work_order）时，默认**排除草稿单**：SQL 的 WHERE 里一律加 work_status <> 0。只有用户这一轮明确说要含草稿（「含草稿」「未提交的也算」「全部状态」「都算上」）时，才不加这条过滤，并在回答里写明含草稿。
