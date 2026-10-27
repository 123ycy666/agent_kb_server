<!-- 来源：ai_data.ai_prompt（prompt_key=business_rules，id=39，更新于 2026-09-22 17:44:26）。由 server/scripts/export_prompts.py 导出，人工复核后使用。 -->

========== 业务规则（所有Agent通用） ==========

【数据库规范】
- 除 t_work_order_audit_task 外，其余业务表都有 deleted_flag，查询时必须加；t_power_work_order 是 varchar(2)，写 deleted_flag = '0'，其余为 tinyint，写 deleted_flag = 0
- 金额单位均为元，需要在回答中转换为万元（除以10000）
- 百分比例（线损率、执行率等）保留2位小数

【供电所层级关系】
- 供电公司(县级t_power_supply_company) > 供电所(t_power_company) > 台区(t_power_area)
- 供电所关联：t_power_company_assessment.power_station_id = t_power_company.id
- 工单关联供电所：通过 power_company_name 字段

【指标判定标准】
- 线损率：< 3% 优秀，3%-5% 正常，> 5% 偏高需关注
- 预算执行率(reality_total/budget_total)：< 80% 偏低，80%-110% 正常，> 110% 超预算
- 投入产出比(economic_benefit/reality_total)：< 1 需关注，>= 2 优秀
- 故障停电次数同比增长 > 20% 为异常

【排名与分析基准】
- 同比（YoY）：与去年同期对比
- 供电所排名优先按经济效益(economic_benefit)降序
- 对比分析时默认使用全市平均值作为基准

【时间处理】
- 未指定年份时默认使用当前年份
- 年份筛选**按表取不同字段**（见下面【年份字段对照】），不要一律套用 create_time

【年份字段对照】（按表结构确定）
- 工单表与中台推送明细表：没有年份列，用 create_time 范围过滤
- 预算类（t_power_company_budget_allocation / t_power_company_budget_result）与评分明细（t_power_station_score_detail）：用 year 列
- AI 指标表（t_power_ai_metric_snapshot / t_power_ai_metric_comparison）：用 period_type + period_key（如 yearToDate + 202609）
- 档案表（t_power_area / t_power_company / t_power_line / t_power_supply_company）：没有年份概念，年份只作展示、不作过滤条件
