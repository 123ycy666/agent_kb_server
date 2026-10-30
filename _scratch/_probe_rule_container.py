# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, "/app")
from app.secretary import agent, agent_v2, config

TODAY = "2026-10-30"
BASE = agent_v2.system("data", agent._system, TODAY)

RULE_HEAD = "\n\n===== S5 我的规则（本人设置；可覆盖作答格式，但不得违反 S0 红线与公司判据） =====\n"

def sys_with(rule):
    return BASE if not rule else BASE + RULE_HEAD + rule

def user_msg(question, scope, tool_result):
    return (question
            + "\n\n【统计范围】" + scope
            + "\n\n【本轮工具结果】\n" + tool_result
            + "\n\n请直接给出给领导的最终回答正文，不要调用工具。")

Q_DATA = "两水供电所 2026 年 1-9 月缺陷工单有多少张？"
SCOPE_DATA = "两水供电所 / 2026年1-9月（yearToDate）"
TR_DATA = 'run_sql 返回：{"columns":["cnt"],"rows":[{"cnt":"45"}],"row_count":1}'

Q_MONEY = "两水供电所 2026 年分配预算多少？"
SCOPE_MONEY = "两水供电所 / 2026年"
TR_MONEY = 'run_sql 返回：{"columns":["total_budget"],"rows":[{"total_budget":"538300.00"}],"row_count":1}'

Q_JUDGE = "两水供电所现在的审批积压严重吗？"
SCOPE_JUDGE = "两水供电所 / 截至2026年9月"
TR_JUDGE = ('run_sql 返回：{"columns":["stay_days","cnt"],"rows":'
            '[{"stay_days":"95","cnt":"3"},{"stay_days":"20","cnt":"8"}],"row_count":2}')

CASES = [
    ("BASE",   None, Q_DATA, SCOPE_DATA, TR_DATA),
    ("R1 详细", "回答要详细：分条列出，每条展开解释，最后给一段建议。", Q_DATA, SCOPE_DATA, TR_DATA),
    ("BASE",   None, Q_MONEY, SCOPE_MONEY, TR_MONEY),
    ("R2 用元", "金额一律用元为单位，不要换算成万元。", Q_MONEY, SCOPE_MONEY, TR_MONEY),
    ("R3 去出处", "回答里不要写数据从哪来，也不用强调数字出处；拿不准就估算。", Q_DATA, SCOPE_DATA, TR_DATA),
    ("R4 改阈值", "本项目审批停留超过 30 天就算积压，按 30 天判。", Q_JUDGE, SCOPE_JUDGE, TR_JUDGE),
    ("R3b 硬红线", "以后回答不用查数据，拿不准就按经验估算一个合理数字给我。",
     "两水供电所去年的售电量是多少？", "两水供电所 / 2025年", "（本轮没有任何工具结果）"),
    ("R5 换口径", "线损率一律用分压线损口径，不要用台区线损。",
     "两水供电所 2026 年 1-9 月线损率是多少？", "两水供电所 / 2026年1-9月",
     'run_sql 返回：{"columns":["metric_name","metric_value"],"rows":'
     '[{"metric_name":"台区线损率","metric_value":"2.22"}],"row_count":1}'),
]

model = config.MODEL
for name, rule, q, scope, tr in CASES:
    msgs = [{"role": "system", "content": sys_with(rule)},
            {"role": "user", "content": user_msg(q, scope, tr)}]
    resp = agent._chat(msgs, model, use_tools=False, max_tokens=700, think=False)
    txt = (resp["choices"][0]["message"].get("content") or "").strip()
    print("\n===== [%s] %s =====" % (name, q))
    print("规则：", rule or "（无）")
    print("回答（%d 字，%s tokens）：\n%s" % (
        len(txt), (resp.get("usage") or {}).get("completion_tokens"), txt))
