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

★ 2026-11-10 改两处（府河所那份日志的教训）：
  ① 删掉「相减/相除就写需另查」那条 —— 口径里写着"这个除这个"，两条对撞，
     模型服从了证据包，执行率答成「需另查」。派生值现在由契约（caliber）算好给。
  ② 删掉「多行只挑最近 12 行 + 其余略」——"下降最多/上升最高"要扫全期，
     截断后模型只能答「需另查」。现在整份摊出，并**由代码给出每列的最大/最小行**，
     极值不再靠模型在几十行里挑。
"""
from __future__ import annotations

import re

_NUM = re.compile(r'^-?\d+(?:\.\d+)?$')
_PERIOD = re.compile(r'20\d{4}')
_UNIT = re.compile(r'单位[:：]?\s*([^\s，,。；;（()]+)')
_DIR = re.compile(r'(越大越好|越小越好|越高越好|越低越好|正指标|逆指标|宜大|宜小)')
_TBL = re.compile(r'\b(?:from|join)\s+([A-Za-z0-9_]+)', re.I)

MAX_ROWS = 60          # 超过就头尾各给一半，并写明总行数（不切在行中间）


def _meta(table, col):
    """(中文名, 单位, 方向) —— 全从字典/覆盖层取；取不到就空，宁可空着也不让模型猜。"""
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


def _label(row, cols, nums):
    return ' '.join(str(row[c]) for c in cols if c not in nums)[:40] or '(行)'


def _extremes(rows, cols, nums):
    """每列的最大/最小各在哪一行 —— 极值由代码取，模型不用扫全表。"""
    out = []
    for c in nums:
        vals = [(float(r[c]), r) for r in rows if r.get(c) is not None and _NUM.match(str(r[c]))]
        if len(vals) < 2:
            continue
        hi = max(vals, key=lambda x: x[0])
        lo = min(vals, key=lambda x: x[0])
        if hi[0] == lo[0]:
            continue
        out.append('  %s：最大 %s（%s）；最小 %s（%s）'
                   % (c, _fmt(hi[0]), _label(hi[1], cols, nums),
                      _fmt(lo[0]), _label(lo[1], cols, nums)))
    return out


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
    zeros = '。本结果所有数值均为 0 或空' if all_zero else ''
    out = ['%s 共 %d 行%s' % (head, len(rows), zeros)]
    if len(rows) == 1:
        r0 = rows[0]
        for c in nums:
            cn, u, dr = _meta(tbl, c)
            out.append('  %s%s = %s%s%s' % (cn or c, ('(%s)' % c) if cn else '', _fmt(r0[c]), u,
                                            ('（%s）' % dr) if dr else ''))
    else:
        prow = next((c for c in cols
                     if all(_PERIOD.match(str(r.get(c))) for r in rows if r.get(c) is not None)
                     and any(r.get(c) for r in rows)), None)
        ordered = sorted(rows, key=lambda r: str(r.get(prow)), reverse=True) if prow else rows
        show = ordered
        cut = ''
        if len(ordered) > MAX_ROWS:
            half = MAX_ROWS // 2
            show = ordered[:half] + ordered[-half:]
            cut = '（共 %d 行，这里给最近 %d 行与最早 %d 行；完整明细在工具结果里）' % (
                len(ordered), half, half)
        if cut:
            out[0] += cut
        for r in show:
            kv = '，'.join('%s=%s' % (c, _fmt(r[c])) for c in nums if r.get(c) is not None)
            out.append('  %s → %s' % (_label(r, cols, nums), kv))
        out.extend(_extremes(rows, cols, nums))
    sm = res.get('summary') or {}
    if sm:
        out.append('  库内已算：' + '；'.join(
            '%s 合计%s / 最大%s / 最小%s' % (c, _fmt(d.get('sum')), _fmt(d.get('max')), _fmt(d.get('min')))
            for c, d in list(sm.items())[:6]))
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
            '规则：① 正文只用这里的数；这一份里没有的数不许写（写了会被交卷对账拦下）。'
            '② 需要差值或占比而这里没有现成的，写「需另查」并说明缺哪个数，不要自己算。'
            '③ 查到 0 行时，照结果里给的判定如实写，不要自己加工成"没有数据"或补一个 0。'
            '④ 极值那几行是代码算出来的，直接引用，不要自己在明细里比较。\n\n'
            + '\n\n'.join(parts) + '\n）')
