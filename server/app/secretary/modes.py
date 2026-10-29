# -*- coding: utf-8 -*-
"""判模式：一句话该走哪条路（general / data / doc / office）。

设计原则（2026-10-21）
--------------------
- **规则优先**，不打模型：每次提问多一次调用在内网是实打实的延迟。
- 判不出来落 `general`（自由作答），**不是报错**，也不是硬塞进查数。
- 关键词表放在站点包 `_platform/mode_rules.yml`（E4），代码里不写业务词。
- 模式只决定"加载哪些提示词、给哪些工具"，不决定"能不能做"。

自检：py -X utf8 server/tests/agent/test_prompt_stack.py
"""
from __future__ import annotations

from pathlib import Path

import yaml

# 任务型入口（前端下拉）→ 模式。这是产品结构，不是业务规则。
TASK_MODES = {'digest': 'doc', 'proofread': 'doc', 'table_audit': 'doc',
              'deck': 'doc', 'doc_write': 'doc', 'ocr': 'doc', 'batch': 'doc'}

_FALLBACK_RULES = {
    'produce': ['做', '写', '生成', '出一份', '出个', '整理成', '转成', '导出', '起草', '拟一份', '汇总成'],
    'objects_file': ['ppt', '幻灯片', '演示', '汇报材料', '文档', '公文', '简报', '请示',
                     '表格', '台账', '清单', '模板', '方案'],
    'objects_text': ['总结', '作文', '文案', '讲稿', '邮件', '通知', '说明', '心得体会',
                     '材料', '报告', '稿子'],
    'data': ['多少', '多大', '几个', '几笔', '几条', '占比', '比例', '排名', '前几', '同比', '环比',
             '哪几个', '哪些', '统计', '合计', '总额', '总数', '平均', '执行率', '完成率', '差多少'],
    # 业务主题词：写材料时命中它必须走 data（否则模型会凭空编业务情况 —— 实测过）
    'business_topics': ['配网', '台区', '线路', '线损', '工单', '缺陷', '预算', '项目',
                        '审批', '指标', '抢修', '停电', '售电', '供电所', '执行率', '进度'],
    'kb': ['规定', '流程', '制度', '办法', '标准', '怎么规定', '谁审批', '依据'],
    # 问概念/定义：这类问题查库也查不出来（库里没有"什么叫线损"），不要因为出现业务词
    # 就把它送进查数模式（2026-10-28 判模式回归时发现）
    'explain': ['解释', '是什么', '什么意思', '什么叫', '含义', '区别', '原理', '为什么'],
    'chat': ['你好', '您好', 'hi', 'hello', '在吗', '谢谢', '你是谁', '你能干什么'],
}


def rules_path() -> Path:
    return Path(__file__).resolve().parents[3] / 'config' / 'sites' / '_platform' / 'mode_rules.yml'


def load_rules() -> dict:
    p = rules_path()
    if not p.is_file():
        return dict(_FALLBACK_RULES)
    try:
        data = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
        out = dict(_FALLBACK_RULES)
        out.update({k: v for k, v in data.items() if isinstance(v, (list, dict))})
        return out
    except Exception:                              # noqa: BLE001
        return dict(_FALLBACK_RULES)


def judge(question: str, *, has_files: bool = False, task: str | None = None,
          rules: dict | None = None) -> dict:
    """返回 {'mode', 'reasons'}。mode ∈ general/data/doc/office。"""
    r = rules or load_rules()
    q = (question or '').strip()
    low = q.lower()

    if task:
        return {'mode': TASK_MODES.get(task, 'doc'), 'reasons': ['显式选了任务 %s' % task]}

    produce = any(w in q for w in r.get('produce', ()))
    has_data_word = any(w in q for w in r.get('data', ()))
    has_biz_topic = any(w in q for w in r.get('business_topics', ()))

    # 1) 要文件/交付物 → office（技能包干活）
    if produce and any(w in low for w in r.get('objects_file', ())):
        return {'mode': 'office', 'reasons': ['要文件产物（动词 + PPT/文档/表格类词）']}

    # 2) 要写材料 + 涉业务/数据 → data（先查再写；不查就会编业务情况，实测过）
    if produce and (has_data_word or has_biz_topic):
        return {'mode': 'data', 'reasons': ['要写材料且涉业务 → 先查再写']}

    # 3) 要写文字、不涉数据 → general（纯写作：总结/作文/通知/讲稿/邮件）
    if produce and any(w in q for w in r.get('objects_text', ())):
        return {'mode': 'general', 'reasons': ['纯文字写作 → 直接写，不查库']}

    if has_data_word:
        return {'mode': 'data', 'reasons': ['含问数词']}

    # 问概念/定义（"什么叫线损""某某是什么意思"）→ 直答：库里没有定义，查了也是空
    if any(w in q for w in r.get('explain', ())):
        return {'mode': 'general', 'reasons': ['问概念/定义 → 直答，不查库']}

    # 4) 只提业务对象、没有问数词（"某所整体情况""某县的情况怎么样"）→ 也要查库。
    #    以前这类落到 general，而 general 模式**不给工具**，模型只能凭提示词编，
    #    结果把"没查"写成"库里没有记录"（2026-10-28 线上：问整体情况一律答未查到）。
    if has_biz_topic:
        return {'mode': 'data', 'reasons': ['含业务对象（没问数词也要查）→ 先查再答']}

    if any(w in q for w in r.get('kb', ())):
        return {'mode': 'data', 'reasons': ['含制度/流程词（知识库检索在 data 模式）']}

    if has_files:
        return {'mode': 'data', 'reasons': ['带文件、没要产物 → 带文件问答']}

    if low.startswith(tuple(r.get('chat', ()))):
        return {'mode': 'general', 'reasons': ['寒暄 → 直答']}

    return {'mode': 'general', 'reasons': ['没命中规则 → 通用模式（自由作答）']}
