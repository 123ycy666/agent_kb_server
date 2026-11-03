# -*- coding: utf-8 -*-
"""八步流水线 + 空壳 验收（2026-11-03 定稿）。

跑法：`py -X utf8 _scratch/verify_shell.py`

不连任何库：把 `pgdb.connect` 打成抛错，所有"读失败回退默认值"的路径都走默认值 ——
这就是空壳部署后的真实形态（库里没有覆盖、没有规则材料、补全表空）。

覆盖：
    ① 页面四个可编辑框存在、默认空（intent / confirm / redlines / priority）
    ② 第 1 步：框空 / 非"不走" → 走；只有"不走"才不走
    ③ 第 3 步：只说对象的补默认问法
    ④ 第 4 步：补全表命中只返回条目，不改问题
    ⑤ 第 6 步：参考资料齐全（补前补后 / 命中条目 / 最近几轮），四行能解析
    ⑥ 节点树 = 八步，材料键对得上，没有断链（不再引已删常量）
    ⑦ 装配：空内容 → S0/S1/S4/S6 一段都不进 system
    ⑧ 模式做法：只留 general（基础流程），业务三段清空
    ⑨ 守卫全关：步数 30、数字闸关、时间预算不限、不再调 evidence_gate
    ⑩ 四份材料槽还在（机制留、内容空）
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))

FAIL = []
OK = []


def chk(name, cond, detail=''):
    (OK if cond else FAIL).append(name)
    print(('  ok   ' if cond else '  FAIL ') + name + (('  ← ' + str(detail)) if (detail and not cond) else ''))


def main():
    # 不连库：任何读表都抛，走代码默认值
    from app.secretary import pgdb
    pgdb.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('no db'))

    from app.secretary import (preprocess, confirm, prompt_text, prompt_tree,
                               prompt_stack, prompt_material, prompts_data,
                               config, complete_rules)

    print('[1] 页面四个可编辑框')
    keys = set(prompt_text.TEXT_KEYS)
    for k in ('intent', 'confirm', 'redlines', 'priority'):
        chk('TEXT_KEYS 有 %s' % k, k in keys, sorted(keys))
    rows = {r['key']: r for r in prompt_text.all_texts()}
    for k in ('intent', 'confirm', 'redlines', 'priority'):
        chk('%s 默认空' % k, (rows.get(k) or {}).get('default') == '', rows.get(k))

    print('[2] 第 1 步 要不要走预处理')
    chk('框空 → 走（全走）', preprocess.gate('两水情况') is True)
    # 下面几项要"框里有内容"才有意义：库连不上时框恒为空，这里临时给两段规则。
    _real_get = prompt_text.get
    prompt_text.get = lambda k, d='': ({'intent': '只要不是闲聊就回"走"',
                                        'confirm': '按资料归整成四行'}.get(k)
                                       or _real_get(k, d))
    chk('回"走" → 走', preprocess.gate('两水情况', ask=lambda s, u: '走') is True)
    chk('回"不走" → 不走', preprocess.gate('你好', ask=lambda s, u: '不走') is False)
    chk('回废话 → 走（只认"不走"）', preprocess.gate('你好', ask=lambda s, u: '？') is True)
    chk('调用抛错 → 走', preprocess.gate('你好', ask=lambda s, u: 1 / 0) is True)
    chk('空白 → 不走', preprocess.gate('   ') is False)

    print('[3] 第 3 步 补问法')
    c = preprocess.complete('两水供电所')
    chk('只说对象 → 补默认问法', c['changed'] and c['text'] == '两水供电所情况怎么样', c)
    c2 = preprocess.complete('两水供电所情况')
    chk('带"情况" → 只补"怎么样"', c2['text'] == '两水供电所情况怎么样', c2)
    c3 = preprocess.complete('上个月新装了多少户')
    chk('已经问清 → 一个字不加', c3['changed'] is False and c3['text'] == '上个月新装了多少户', c3)

    print('[4] 第 4 步 补全表只查不改')
    chk('表空 → 0 命中', preprocess.hits('两水情况') == [])
    chk('补全不改问题', preprocess.complete('两水情况')['text'].startswith('两水情况'))
    chk('complete_rules 有 读表/保存/预览',
        all(hasattr(complete_rules, x) for x in ('rows', 'save', 'as_text')))

    print('[5] 第 6 步 问题确定')
    scope = {'time': {'time_text': '2026年10月', 'is_default': True},
             'place': '全市', 'place_default': True}
    fixed = {'text': '两水供电所', 'used': True, 'hint': ''}
    comp = {'text': '两水供电所情况怎么样', 'changed': True, 'hits': []}
    r = confirm.run('两水情况', fixed, comp, scope, [], ask=None)
    chk('框空 / 无调用 → 不归整', r['used'] is False and r['text'] == '')
    blk = r['block']
    for frag in ('原话', '更正后', '补问法后', '补全表命中的条目', '时间', '范围', '最近几轮'):
        chk('参考资料有「%s」' % frag, frag in blk)
    chk('补前写明"这次没说"', '（这次没说）' in blk, blk[:200])
    one = confirm.run('两水情况', fixed, comp, scope, [], ask=lambda s, u: 'x')
    chk('有框 + 有调用 → 归整', one['used'] is True)
    prompt_text.get = _real_get
    ln = confirm.lines('要什么：看两水装表\n时间：2026年10月\n范围：两水供电所\n其它要求：简略')
    chk('四行能解析', ln['要什么'] == '看两水装表' and ln['其它要求'] == '简略', ln)

    print('[6] 节点树 = 八步')
    nodes = prompt_tree.tree()
    chk('八个节点', len(nodes) == 8, len(nodes))
    mats = [m['key'] for n in nodes for m in n['materials']]
    for k in ('base.intent', 'base.confirm', 'mat.complete', 'mat.fetch', 'mat.answer'):
        chk('材料里有 %s' % k, k in mats, mats)
    chk('不再有 base.format（汇报口吻已删）', 'base.format' not in mats)
    for m in [m for n in nodes for m in n['materials']]:
        chk('材料 %s 能取到正文' % m['key'], isinstance(m['body'], str))

    print('[7] 装配：空内容不注入')
    b = prompt_stack.build(mode='general', identity='', modes={'general': ''},
                           priority='', redlines='', answer_format=None, rules_menu=None)
    ids = [x['id'] for x in b['layers']]
    chk('S0/S1/S4/S6 都不出现', not ({'S0', 'S1', 'S4', 'S6'} & set(ids)), ids)

    print('[8] 模式做法只留基础流程')
    mt = prompts_data.MODE_TEXTS
    chk('data/doc/office 清空',
        all(not (mt.get(k) or '').strip() for k in ('data', 'doc', 'office')),
        {k: len(mt.get(k) or '') for k in mt})
    chk('general 有基础流程', len((mt.get('general') or '').strip()) > 30)
    chk('身份里不再指"L0 红线"', 'L0 红线' not in (prompts_data.IDENTITY or ''))

    print('[9] 守卫全关')
    chk('步数上限 30', config.MAX_STEPS == 30, config.MAX_STEPS)
    chk('数字闸默认关', config.NUMBER_GUARD_ON is False)
    chk('时间预算不限', config.QUESTION_BUDGET_S == 0, config.QUESTION_BUDGET_S)
    src = open(os.path.join(ROOT, 'server/app/secretary/agent.py'),
               encoding='utf-8').read()
    chk('主循环不再调 evidence_gate', 'evidence_gate.decide' not in src)
    chk('不再往用户消息里注入【统计范围】',
        '【统计范围】（已按今天日期算好' not in src)
    chk('不再用词表判模式', 'judge_mode(' not in src)
    chk('有第 1 步 / 第 6 步调用', '_pre.gate(' in src and 'confirm.run(' in src)

    print('[10] 四份材料槽还在')
    chk('SLOTS 四份', prompt_material.SLOTS == ('fetch', 'judge', 'answer', 'check'),
        prompt_material.SLOTS)

    print('\n成功 %d 项，失败 %d 项' % (len(OK), len(FAIL)))
    for f in FAIL:
        print('  FAIL: ' + f)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
