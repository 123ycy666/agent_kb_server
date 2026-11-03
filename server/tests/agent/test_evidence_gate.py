# -*- coding: utf-8 -*-
"""核实闸验收（2026-11-02，**不需要数据库、不需要模型**）。

钉死的事情
----------
1. 答案侧断言识别：数字+单位、以及「没有/缺失/查无」这类否定断言都要认出来。
2. 三动作矩阵：pass / nudge / block 在 (used_tool, nudged, mode, 有无断言) 全组合下的取值。
3. 内网三条真实 trace 的形态走一遍状态机，必须能靠「提醒 + 补工具」自己救回来。
4. 判模式兜底改 data 之后，那些"说法千变万化"的问句都进查数；写作/寒暄/概念不变。

运行：py -X utf8 server/tests/agent/test_evidence_gate.py
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

# 判模式会去读数据库里的词表覆盖（prompt_text.keywords）。本测试不许连库：
# 用一个空词表替身顶上，读到的就是代码默认值。
_stub = types.ModuleType('app.secretary.prompt_text')
_stub.keywords = lambda: {}
sys.modules['app.secretary.prompt_text'] = _stub

from app.secretary import evidence_gate as eg          # noqa: E402
from app.secretary import modes                        # noqa: E402

_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = '') -> None:
    _checks.append((label, bool(cond), extra))


def main() -> int:
    print('核实闸验收（DB 无关）')

    # ── 1 答案侧断言：该认出来的
    hit = [
        ('内网第 1 条原文（数字+万元）', '全市共分配预算2536.73万元，已审批金额2680.16万元'),
        ('执行率百分比', '执行率105.64%，已超分配预算143.43万元'),
        ('全角百分号', '线损率2.65％'),
        ('六项指标那串数字', '售电量59.34亿千瓦时，平均停电时长2.86小时/户，故障跳闸962次'),
        ('0 条也是断言', '按这个口径查到 0 条'),
        ('0条记录', '共0条记录'),
        ('内网第 2/3 条原文（数据缺失）', '数据缺失，无法作答'),
        ('快照表无记录', '按系统全年口径在快照表中均无记录'),
        ('没有记录', '按这个月和这个所，没有记录'),
        ('查无', '查无此数据'),
        ('暂无', '暂无异常'),
        ('不存在', '本项目不存在逾期'),
        ('未采集', '该字段未采集'),
        ('长答案中间夹一个数', '情况平稳。' * 300 + '完成率98.6%。' + '其余正常。' * 300),
        ('英文单位', 'revenue 59.34 kWh'),
        ('倍数', '约为去年的 3.5 倍'),
        ('项/笔/张', '新增项目 12 项，报销 7 笔，附件 3 张'),
        ('中文百分比', '增长了百分之三'),
        ('千分位金额', '合计 1,234.5 万元'),
        ('markdown 表格里的数', '| 线损率 | 2.65% |'),
    ]
    for label, a in hit:
        chk('认出断言：' + label, bool(eg.evidence_reason(a)), repr(a[:30]))

    # ── 2 答案侧断言：不该认出来的（纯写作/问候/没断言）
    miss = [
        ('空答案', ''),
        ('纯空白', '   \n\t '),
        ('只有标点', '。！？…——'),
        ('问候', '你好，我是这里的助手，可以帮你查数据或写材料。'),
        ('写通知', '关于做好迎峰度夏工作的通知：请各班组按月报送进展，抓好落实。'),
        ('解释概念', '线损率是供电量与售电量之差占供电量的比例。'),
        ('纯数字无单位', '2025、2026、0715'),
        ('表名但没结论', '我准备查 t_budget 这张表。'),
    ]
    for label, a in miss:
        why = eg.evidence_reason(a)
        chk('不认断言：' + label, why == '', why)

    # ── 3 三动作矩阵（笛卡尔积 16 组 + 查过工具 2 组）
    #    ★ 2026-11-03（Fred）改：**取消 block**。上一轮查到的数这轮直接引用被判
    #      「没核实」然后锁死，是拿查数工具的安全阀套智能体。现在只提醒一次，
    #      提醒过就放行；同会话历史证据命中时直接 pass。
    has = '完成率98.6%'
    no = '好的，我明白了。'
    for mode in ('data', 'general', 'doc', 'office'):
        for an, an_tag in ((has, '有断言'), (no, '无断言')):
            for nudged in (False, True):
                if not nudged:
                    if an_tag == '有断言':
                        exp = eg.ACT_NUDGE          # 有断言：任何模式都先给一次机会
                    else:
                        exp = eg.ACT_NUDGE if mode == 'data' else eg.ACT_PASS
                elif an_tag == '有断言':
                    # 2026-11-03：提醒过就不再拦（原来 data 模式是 block）
                    exp = eg.ACT_PASS
                else:
                    exp = eg.ACT_PASS
                got, why = eg.decide(used_tool=False, nudged=nudged,
                                     answer=has if an_tag == '有断言' else no, mode=mode)
                chk('动作 %s/%s/nudged=%s → %s' % (mode, an_tag, nudged, exp), got == exp,
                    '%s（%s）' % (got, why))
    for mode in ('data', 'general'):
        got, _ = eg.decide(used_tool=True, nudged=False, answer=has, mode=mode)
        chk('查过工具就一定放行（%s）' % mode, got == eg.ACT_PASS, got)
    chk('未被拦时 nudge 的理由写进理由字段',
        '没有工具结果支撑' in eg.decide(used_tool=False, nudged=False, answer=has)[1])
    chk('非查数模式：提醒过就不拦（写材料能出得去）',
        eg.decide(used_tool=False, nudged=True, answer=has, mode='general')[0] == eg.ACT_PASS)
    chk('查数模式：提醒过也不再拦死（2026-11-03 取消 block）',
        eg.decide(used_tool=False, nudged=True, answer=has, mode='data')[0] == eg.ACT_PASS)
    chk('同一句永远不再返回 block',
        all(eg.decide(used_tool=False, nudged=True, answer=has, mode=m)[0] != eg.ACT_BLOCK
            for m in ('data', 'general', 'doc', 'office')))
    # 历史证据：上一轮查过的数，这一轮直接引用要放行（不提醒、不拦）
    chk('历史查过 → 直接放行',
        eg.decide(used_tool=False, nudged=False, answer=has, mode='data',
                  history_ok=True)[0] == eg.ACT_PASS
        and '同会话此前查过' in eg.decide(used_tool=False, nudged=False, answer=has,
                                          mode='data', history_ok=True)[1])
    chk('claims() 能取出断言片段', eg.claims('完成率98.6%，共12条') == ['98.6%', '12条'],
        eg.claims('完成率98.6%，共12条'))

    # ── 4 内网三条真实 trace 走状态机：必须能自己救回来
    real = [
        ('第 1 条 全市（模型自己写了数字）',
         '2025年全市预算分配与执行情况：全市共分配预算2536.73万元，执行率105.64%'),
        ('第 2 条 2025年全市情况（答数据缺失）',
         '2025年全市治理成效指标与预算执行情况数据缺失，无法作答。'),
        ('第 3 条 7月整体情况（答数据缺失）',
         '2026年7月全市治理成效指标与预算执行情况数据缺失，无法作答。'),
    ]
    for label, first_answer in real:
        # 第 1 轮：没工具就写答案
        a1, why1 = eg.decide(used_tool=False, nudged=False, answer=first_answer, mode='data')
        # 第 2 轮：提醒后（工具已补上）真去查了
        a2, _ = eg.decide(used_tool=True, nudged=True, answer='', mode='data')
        seq = [a1, a2]
        chk('状态机：%s → [%s]' % (label, '→'.join(seq)),
            seq == [eg.ACT_NUDGE, eg.ACT_PASS], '→'.join(seq) + ' / ' + why1)

    # 旧闸门会怎么判：同一句话，旧代码只看「数字+单位」
    old_claim = re.compile(r'\d+(?:\.\d+)?\s*(?:万|亿|%|％|件|个|条|户|千瓦时|千瓦|kWh|元|次|小时|倍)')
    chk('旧闸门：数字那条会被要求核实', bool(old_claim.search(real[0][1])))
    chk('旧闸门：数据缺失那条被当通用问答放行（这就是 bug）',
        not old_claim.search(real[1][1]) and not old_claim.search(real[2][1]))
    chk('新闸门：数据缺失那条必须被要求核实', bool(eg.evidence_reason(real[1][1])))

    # ── 5 判模式：兜底改成 data，说法再变也进查数
    must_data = ['全市', '全市情况怎么样', '2025年全市情况怎么样', '2026年7月整体情况',
                 '全市整体情况', '全市啥情况', '说说全市', '今年开得怎么样',
                 '两水供电所情况怎么样', '请假流程是谁审批的', '两水所上个月的工单有多少单',
                 '我今天有点累']
    for q in must_data:
        got = modes.judge(q)['mode']
        chk('判模式「%s」→ data' % q, got == 'data', got)
    keep = [('你好', 'general'), ('谢谢', 'general'), ('在吗', 'general'),
            ('什么叫线损', 'general'), ('线损率是什么意思', 'general'),
            ('帮我把这份材料写成汇报', 'general'),
            ('做个PPT汇报', 'office'), ('帮我写个通知', 'general')]
    for q, exp in keep:
        got = modes.judge(q)['mode']
        chk('判模式「%s」→ %s' % (q, exp), got == exp, got)

    # ── 6 接线：agent.py 真的用上了闸门，且不再拿问题侧词表当闸
    src = (SERVER / 'app' / 'secretary' / 'agent.py').read_text(encoding='utf-8')
    chk('agent.py 调用 evidence_gate.decide', 'evidence_gate.decide(' in src)
    chk('agent.py 不再有用 _biz_force 放行', '_biz_force' not in src)
    chk('agent.py 提醒时补工具（_tool_unlocked）', '_tool_unlocked' in src)
    chk('agent_v2 有 tool_context', 'def tool_context' in (
        SERVER / 'app' / 'secretary' / 'agent_v2.py').read_text(encoding='utf-8'))
    chk('modes.py 兜底注释写明理由', '按查数处理' in (
        SERVER / 'app' / 'secretary' / 'modes.py').read_text(encoding='utf-8'))

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(('  [OK]   ' if ok else '  [FAIL] ') + label
              + (('   ← ' + extra) if (not ok and extra) else ''))
    print('\n%d 项检查，%d 项失败' % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
