# -*- coding: utf-8 -*-
"""空壳化验收（2026-11-03）—— 全部断言，跑完打印 PASS/FAIL 汇总。

不连任何库：把 `pgdb.connect` 打成抛错，代码里所有"读失败回退默认值"的路径都会走默认值，
这就是空壳部署后的真实形态（库里没有覆盖、没有规则材料）。

跑法：py -X utf8 _scratch/verify_shell.py

**只对 main（空壳）有意义**：`online` 分支把规则入口打开了（SLOTS 非空），
在那条分支上跑到这里会直接说"这不是空壳分支"然后退出 0 —— 不是失败。
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))

# ── 空壳分支守卫：online 分支上不跑这套断言 ──────────────────────────
from app.secretary import prompt_material as _pm     # noqa: E402

if _pm.SLOTS:
    print('跳过：当前分支 prompt_material.SLOTS = %s，不是空壳分支（main）。' % (_pm.SLOTS,))
    raise SystemExit(0)

# ── 先把 DB 打掉：空壳环境就是"库读不到 → 全走代码默认值" ──────────────
from app.secretary import pgdb                       # noqa: E402

def _no_db(*a, **k):
    raise RuntimeError('验收脚本：不连库')

pgdb.connect = _no_db                                # type: ignore[assignment]

from app.secretary import (                          # noqa: E402
    agent_v2, modes, preprocess, prompt_material, prompt_stack, prompt_tree, time_scope,
)

FAILS: list[str] = []
CASES = 0


def chk(name: str, ok: bool, detail='') -> None:
    global CASES
    CASES += 1
    if ok:
        print('PASS  %s%s' % (name, ('  ← ' + str(detail)) if detail else ''))
    else:
        print('FAIL  %s%s' % (name, ('  ← ' + str(detail)) if detail else ''))
        FAILS.append(name)


# ── ① 链路：6 份材料都不在节点上 ────────────────────────────────────
tree = prompt_tree.tree()
keys = [m['key'] for n in tree for m in n['materials']]
GONE = ('mat.complete', 'mat.fetch', 'mat.judge', 'mat.answer', 'base.format', 'mat.check')
for k in GONE:
    chk('链路里没有 %s' % k, k not in keys, '现有 %d 份材料' % len(keys))
chk('节点数仍是 12（只删材料不删步骤）', len(tree) == 12, len(tree))
chk('节点 4 补全没有材料', not [m for n in tree if n['id'] == 'p4' for m in n['materials']])

# ── ② 空壳总开关：四份材料整体关闭 ──────────────────────────────────
chk('prompt_material.SLOTS 为空', prompt_material.SLOTS == (), prompt_material.SLOTS)
chk('block() 不再拼任何材料', prompt_material.block(('fetch', 'judge')) == '')
chk('材料概览为空', prompt_material.summary() == [], prompt_material.summary())

# ── ③ 时间地点：默认补上一个月 ─────────────────────────────────────
time_scope._latest_ym = lambda: '202610'
time_scope._units = lambda: []
time_scope.tools_db._exec = lambda *a, **k: {'rows': []}
r = time_scope.resolve('情况怎么样', today=datetime.date(2026, 11, 5))
chk('11月没提时间 → 2026年10月', (r['time_text'], r['period_key']) == ('2026年10月', '202610'),
    (r['time_text'], r['period_key']))
chk('默认期间的说明写清楚了', '上一个月' in r['note'], r['note'])
r1 = time_scope.resolve('情况怎么样', today=datetime.date(2027, 1, 9))
chk('1月没提时间 → 上一年12月', (r1['time_text'], r1['period_key']) == ('2026年12月', '202612'),
    (r1['time_text'], r1['period_key']))
r2 = time_scope.resolve('上个月线损', today=datetime.date(2026, 11, 5))
chk('明说上个月仍然算上个月', r2['period_key'] == '202610' and not r2['is_default'], r2['period_key'])

# ── ④ 业务词表语义：命中直通词才算"不是业务问题" ─────────────────────
GATE = [
    ('两水', True, '只有所名、一个业务词都没有 → 业务问题'),
    ('两水供电所情况', True, '业务问题'),
    ('上个月线损多少', True, '业务问题'),
    ('全市情况怎么样', True, '业务问题'),
    ('现在还有多少条缺陷工单没闭环', True, '业务问题'),
    ('帮我写个通知', False, '写作 → 直通'),
    ('做个PPT汇报', False, '出文件 → 直通'),
    ('你好', False, '寒暄 → 直通'),
    ('什么叫线损', False, '问概念 → 直通'),
]
for q, want, why in GATE:
    got = preprocess.gate(q)
    chk('gate(%s) = %s' % (q, want), got is want, '%s（实得 %s）' % (why, got))

chk('modes 不再返回 business_topics',
    'business_topics' not in modes.load_rules(),
    sorted(modes.load_rules().keys()))
chk('judge(台区线损情况) = data', modes.judge('台区线损情况')['mode'] == 'data')

# ── ⑤ 补全：只补默认问法，不再有表命中 ──────────────────────────────
comp = preprocess.complete('两水供电所情况')
chk('补全：只有对象没问法 → 补「怎么样」', comp['text'] == '两水供电所情况怎么样', comp['text'])
chk('补全：hits 恒空（补全表已删）', comp['hits'] == [], comp['hits'])
chk('补全：说清了问什么就不动', preprocess.complete('上个月线损多少')['changed'] is False)

# ── ⑥ 装配：system 里没有 S4 / 规则材料 ─────────────────────────────
sysmsg = agent_v2.system('data', lambda today=None: '【旧装配】', today='2026-11-05')
ids = [x['id'] for x in prompt_stack.build(mode='data')['layers']]
# 注：身份（L1）里本来就有「汇报口吻，不寒暄」这句风格要求，它属于身份、保留；
#     这里查的是 **S4 那一段** 与 **规则材料段** 有没有被整段注入。
for bad in ('S4 作答格式', '规则材料', '【汇报口吻与格式】', '交卷前对一遍'):
    chk('system 不含「%s」' % bad, bad not in sysmsg)
chk('装配层里没有 S4 / S6', ('S4' not in ids) and ('S6' not in ids), ids)

# ── ⑦ 路由：补全表接口已下线 ────────────────────────────────────────
from app.api.v1 import prompts as prompts_api        # noqa: E402
paths = sorted({getattr(r, 'path', '') for r in prompts_api.router.routes})
chk('没有 /complete 路由', not any('/complete' in p for p in paths), paths)

# ── ⑧ 文件层面：complete_rules 模块已删除、备份齐全 ─────────────────
chk('complete_rules.py 已删除',
    not (ROOT / 'server/app/secretary/complete_rules.py').exists())
bak = ROOT / 'docs/08-历史归档/2026-11-03-删除的提示词规则'
want_files = ['README.md', '判据规则-原文.md', '汇报口吻-自检清单-作答规则.md',
              '补全表.md', '业务相关问题词表.md', '时间地点规则-改前.md']
for f in want_files:
    chk('备份存在 %s' % f, (bak / f).is_file(), (bak / f).stat().st_size if (bak / f).is_file() else '-')

print('\n%d 项：%d 通过，%d 失败' % (CASES, CASES - len(FAILS), len(FAILS)))
if FAILS:
    print('失败项：')
    for f in FAILS:
        print('  - ' + f)
    raise SystemExit(1)
