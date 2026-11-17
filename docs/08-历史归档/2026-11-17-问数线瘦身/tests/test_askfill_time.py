# -*- coding: utf-8 -*-
"""补问法上页面 + 第 5 步传提问时间 —— 验收（2026-11-11，Fred）。

守两条线：
  ① 补问法的词表是页面 `askfill` 框里边的 JSON：能改词、也能 `"on": false` 把这一步关掉；
     关掉之后问题一个字不加，补全表照**原话**查。
  ② 第 5 步「问题确定」拿得到【提问时间】（接口 today / business_date）；
     原话没说时间时补上一个自然月，靠的是页面 `confirm` 框里的第 4 条。

跑法：python -X utf8 server/tests/agent/test_askfill_time.py
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = '') -> None:
    _checks.append((label, bool(cond), extra))


def main() -> int:
    from app.secretary import confirm, preprocess, prompt_text, prompt_tree

    print('补问法上页面 + 提问时间传进第 5 步 —— 验收')

    real_get = prompt_text.get
    seen_hits: list[str] = []

    def fake_hits(text):
        seen_hits.append(str(text))
        return [{'kind': 'fill', 'match': '情况', 'fill': '①全市 ②县公司 ③供电所'}]

    preprocess.hits = fake_hits

    def with_cfg(cfg: str):
        prompt_text.get = lambda k, d='': cfg if k == 'askfill' else real_get(k, d)

    # ── ① 默认规则（页面没改）─────────────────────────────────
    with_cfg('')
    for q, want in (('两水', '两水情况怎么样'),
                    ('两水供电所情况', '两水供电所情况怎么样'),
                    ('两水供电所有多少户', '两水供电所有多少户'),
                    ('', '')):
        got = preprocess.complete(q)
        chk('默认补问法：%r → %r' % (q, want), got['text'] == want, repr(got))
    chk('默认：说清了就不算改过', preprocess.complete('两水供电所有多少户')['changed'] is False)

    # ── ② 页面把这一步关掉 ───────────────────────────────────
    with_cfg('{"on": false}')
    seen_hits.clear()
    got = preprocess.complete('两水')
    chk('关掉：问题一个字不加', got['text'] == '两水', repr(got))
    chk('关掉：changed 是 False', got['changed'] is False, repr(got))
    chk('关掉：补全表照原话查', seen_hits == ['两水'], repr(seen_hits))
    chk('关掉：命中照样返回', len(got['hits']) == 1, repr(got['hits']))
    with_cfg('{"on": "false"}')
    chk('关掉：手写 "false" 字符串也认', preprocess.complete('两水')['text'] == '两水')

    # ── ③ 页面改词表 ─────────────────────────────────────────
    with_cfg(json.dumps({'default': '怎么个说法'}, ensure_ascii=False))
    chk('改默认问法：两水 → 两水怎么个说法',
        preprocess.complete('两水')['text'] == '两水怎么个说法')
    with_cfg(json.dumps({'asked': ['户数']}, ensure_ascii=False))
    chk('改「说清了」词表：两水户数不加字',
        preprocess.complete('两水户数')['text'] == '两水户数')
    with_cfg(json.dumps({'topic': ['运行'], 'how': ['怎么样']}, ensure_ascii=False))
    chk('改「只补怎么样」词表：两水运行 → 两水运行怎么样',
        preprocess.complete('两水运行')['text'] == '两水运行怎么样')
    with_cfg('{坏 json')
    chk('JSON 写坏：退回默认规则', preprocess.complete('两水')['text'] == '两水情况怎么样')

    # ── ④ 第 5 步拿得到提问时间 ──────────────────────────────
    prompt_text.get = real_get
    block = confirm.raw_block('两水', {}, {}, None, ask_time='2026-11-11')
    chk('资料里有【提问时间】', '【提问时间】2026-11-11' in block, block[:60])
    chk('资料里原话还在', '【本轮原话】两水' in block, block[:120])
    chk('没传时间时明写「（没传）」',
        '【提问时间】（没传）' in confirm.raw_block('两水', {}, {}, None), '')

    got_user: list[str] = []

    def fake_ask(system, user):
        got_user.append(str(user))
        return '2026年10月 两水 情况怎么样'

    r = confirm.run('两水', {}, {}, None, ask=fake_ask, ask_time='2026-11-11')
    chk('第 5 步真的调了模型', r['used'] is True, repr(r))
    chk('模型收到的是带提问时间的资料',
        '【提问时间】2026-11-11' in (got_user[0] if got_user else ''))
    chk('定稿就是模型给的', r['text'].startswith('2026年10月'), r['text'])

    src = io.open(SERVER / 'app' / 'secretary' / 'agent.py', encoding='utf-8').read()
    chk('agent 把提问时间交给第 5 步', 'ask_time=_now' in src)
    chk('agent 的 confirm 留痕带提问时间', "'ask_time': _now" in src)

    # ── ⑤ 页面接线 ───────────────────────────────────────────
    chk('页面有 askfill 这个键', 'askfill' in prompt_text.TEXT_KEYS, sorted(prompt_text.TEXT_KEYS))
    chk('askfill 按 JSON 校验', 'askfill' in prompt_text._JSON_KEYS)
    nodes = prompt_tree.tree()
    names = [str(n.get('name') or '') for n in nodes]
    chk('节点树 8 步（接手 + 七步）', len(nodes) == 8, names)
    chk('纠错后面紧跟补问法',
        len(names) > 3 and names[2] == '纠错' and names[3] == '补问法', names)
    by_no = {n.get('no'): n for n in nodes}
    p4 = [m['key'] for m in (by_no.get(4) or {}).get('materials') or []]
    p5 = [m['key'] for m in (by_no.get(5) or {}).get('materials') or []]
    chk('补问法是自己一步，只挂它自己的规则', p4 == ['base.askfill'], p4)
    chk('补全表单独一步', p5 == ['mat.complete'], p5)
    mats = {m['key']: m for n in nodes for m in n['materials']}
    chk('补问法可编辑、走文本键',
        mats.get('base.askfill', {}).get('editable') is True
        and mats.get('base.askfill', {}).get('textKey') == 'askfill',
        mats.get('base.askfill'))
    chk('编号顺延到 ⑦ 回答规则',
        str(mats.get('mat.answer', {}).get('name', '')).startswith('⑦'),
        mats.get('mat.answer', {}).get('name'))

    # ── ⑥ 库里的规则真的改了（连不上库就跳过）──────────────────
    rule = (prompt_text.get('confirm', '') or '').strip()
    if rule:
        chk('库里的问题确定提到【提问时间】', '【提问时间】' in rule)
        chk('库里的问题确定按上一个自然月补', '上一个自然月' in rule)
    else:
        print('  [SKIP] 读不到库里 confirm 提示词（这条跳过）')

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(('  [OK]   ' if ok else '  [FAIL] ') + label
              + (('   ← ' + extra) if (not ok and extra) else ''))
    print('\n%d 项检查，%d 项失败' % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
