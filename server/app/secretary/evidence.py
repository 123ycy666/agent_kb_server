# -*- coding: utf-8 -*-
"""证据包：从本轮 trace 由**代码**生成给正文用的权威证据（不经模型转述）。

为什么要有（2026-11-10）
------------------------
收尾原来是"让模型把查询结果写成一份〔口径〕=〔数值〕的清单，再只拿这份清单写正文"。
实测（`output/test_results/2026-11-10-六项指标根因复现/`）：22,116 字的工具结果被扔掉，
只给 6,585 字的二手转述 —— 单位、统计期间、同比基期、好坏方向全在转述时丢了。

外部实证（arXiv 2610.26836）：把工具输出移出上下文会让模型"算对了但答错了"，
机制叫 evidence substitution；有效修法是**内联权威输出并指明哪份是权威**，
而"在提示词里要求正确抄写"无效。

所以这里只做一件事：把 trace 里的真结果摊平给正文那次调用。**数值一律由代码填。**
"""
from __future__ import annotations

import re

_NUM = re.compile(r'^-?\d+(?:\.\d+)?$')
_PERIOD = re.compile(r'20\d{4}')
_UNIT = re.compile(r'单位[:：]?\s*([^\s，,。；;（()]+)')
_DIR = re.compile(r'(越大越好|越小越好|越高越好|越低越好|正指标|逆指标|宜大|宜小)')
_YOY = re.compile(r'^(.*)_yoy_rate$')
_TBL = re.compile(r'\b(?:from|join)\s+([A-Za-z0-9_]+)', re.I)


def _meta(table, col):
    """(中文名, 单位, 方向) —— 全从字典/覆盖层取；取不到就空，宁可空着也不让模型猜。

    方向（越大好/越小好）没有任何结构化字段，只能从字段说明里读：
    字典 `ai_column_metadata.business_desc` 或页面「数据表」覆盖层里写「越大越好 / 越小越好」即可，
    代码不改。写了就带上，没写就空 —— 不写死任何指标的好恶。
    """
    try:
        from app.secretary import semantic
        c = (semantic.table_cols(table) or {}).get(col) or {}
        desc = str(c.get('business_desc') or '')
        m = _UNIT.search(desc)
        d = _DIR.search(desc)
        return (str(c.get('column_cn_name') or ''),
                (m.group(1).strip('（）()') if m else ''),
                (d.group(1) if d else ''))
    except Exception:                              # noqa: BLE001
        return '', '', ''


def _fmt(v):
    try:
        return ('%.4f' % float(v)).rstrip('0').rstrip('.')
    except (TypeError, ValueError):
        return str(v)


def _one_result(res):
    """一条 SQL 结果 → 一段证据。"""
    sql = str(res.get('sql') or '')
    tables = sorted(set(_TBL.findall(sql)))
    tbl = tables[0] if tables else ''
    periods = sorted(set(_PERIOD.findall(sql)))
    head = '【%s】期间 %s' % ('、'.join(tables) or '?', '/'.join(periods) or '未带')
    if res.get('error'):
        return '%s\n  报错：%s' % (head, str(res['error'])[:200])
    rows = res.get('rows') or []
    if not rows:
        return ('%s\n  查到 0 行。判定：%s%s' % (
            head, res.get('判定') or 'EMPTY_OK',
            ('（' + res['判定说明'] + '）') if res.get('判定说明') else ''))
    cols = list(rows[0].keys())
    nums = [c for c in cols
            if [r.get(c) for r in rows if r.get(c) is not None]
            and all(_NUM.match(str(r.get(c))) for r in rows if r.get(c) is not None)]
    all_zero = bool(nums) and all(float(r.get(c) or 0) == 0 for c in nums for r in rows)
    out = ['%s 共 %d 行%s' % (head, len(rows),
                              '。**数值全为 0/NULL，属占位行，不能当结论**' if all_zero else '')]
    if len(rows) == 1:
        r0 = rows[0]
        for c in nums:
            cn, u, dr = _meta(tbl, c)
            out.append('  %s%s = %s%s%s' % (cn or c, ('(%s)' % c) if cn else '', _fmt(r0[c]), u,
                                            ('（%s）' % dr) if dr else ''))
    else:
        # 多行：按期间倒序挑最近的 12 行（问"多少/最新"时，答案通常就在最近几行；
        # 原来按 SQL 返回顺序截前 12 行，实测把 202611 那行截掉了，模型只能答"需另查"）
        prow = next((c for c in cols
                     if all(_PERIOD.match(str(r.get(c))) for r in rows if r.get(c) is not None)
                     and any(r.get(c) for r in rows)), None)
        ordered = sorted(rows, key=lambda r: str(r.get(prow)), reverse=True) if prow else rows
        for r in ordered[:12]:
            label = ' '.join(str(r[c]) for c in cols if c not in nums)[:40] or '(行)'
            kv = '，'.join('%s=%s' % (c, _fmt(r[c])) for c in nums if r.get(c) is not None)
            out.append('  %s → %s' % (label, kv))
        if len(rows) > 12:
            out.append('  …其余 %d 行略（上面已按期间倒序给了最近 12 行；完整明细在工具结果里）'
                       % (len(rows) - 12))
    sm = res.get('summary') or {}
    if sm:
        out.append('  库内已算：' + '；'.join(
            '%s 合计%s / 最大%s / 最小%s' % (c, _fmt(d.get('sum')), _fmt(d.get('max')), _fmt(d.get('min')))
            for c, d in list(sm.items())[:6]))
    yoys = [(c, rows[0].get(c)) for c in cols if _YOY.match(c) and rows[0].get(c) is not None]
    if yoys:
        out.append('  同比（正=上升 / 负=下降）：' + '，'.join(
            '%s=%s%%' % (k[:-9], _fmt(v)) for k, v in yoys[:8]))
    return '\n'.join(out)


def text(trace):
    """证据包正文；本轮没有查询时返回空串。"""
    parts = []
    for t in (trace or []):
        if not isinstance(t, dict) or t.get('kind') != 'tool' or t.get('tool') != 'run_sql':
            continue
        res = t.get('result') or {}
        if not isinstance(res, dict):
            continue
        for one in (res.get('results') if res.get('multi') else [res]) or []:
            if isinstance(one, dict):
                parts.append(_one_result(one))
    if not parts:
        return ''
    return ('【权威证据】（下面每个数都是本轮数据库查询的原始结果，系统直接摊出，未经转述。\n'
            '规则：① 正文只用这里的数；② 需要两次结果相减/相除做差值或占比时，'
            '这里没现成的就写「需另查」，不要自己算；'
            '③ 查到 0 行或占位行时如实写"该期间无数据/未就绪"，不要写成"没有数据"。\n\n'
            + '\n\n'.join(parts) + '\n）')
