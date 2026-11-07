# -*- coding: utf-8 -*-
"""工具结果交付层：超长的工具结果落盘 + 交给模型一个能再取的骨架。

为什么（2026-11-08 两次线上事故）
--------------------------------
① 「全市」那题：查数轮 max_tokens=600，正文截在半句、run_sql 一个没发出去。
   额度那半已收口到 config.LLM_MAX_TOKENS。
② 「预算情况」那题：5 条 SQL 共 15,203 字真实数据，旧 _trim 只认识
   {columns, rows, summary} 一种形状，把数组查询外层 results 当"最占地方的明细列表"
   整块砍成 0 条，模型拿到 results: []，把"没给"当成了"没有"，答「暂无数据」。

病根不是阈值：裁剪按形状写分支、用字符数猜信息量、砍完不告诉模型剩下在哪。
这里的规矩：
  · 不砍内容，只换形式：数组 → 「前 k 项 + total + ref + next_offset」；
  · 被换掉的数组单独落盘，read_result(ref, offset) 能逐页取回；
  · 落盘失败就显式 truncated，绝不返回看起来像空数据的形状；
  · 递归处理任意 JSON，不按返回形状写分支（写分支就是这次事故的病因）。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from app.secretary import config

_SMALL = 400                    # ≤ 这个字数算"小字段"（summary / columns / row_count），一律保留
_REF_RE = re.compile(r'r[0-9a-f]{12}\Z')
_KEEP_FILES = 300               # 单个 session 目录留多少个分片，超了删最旧
_MIN_PER_ITEM = 200             # 给每个元素少于此数就不逐个抠了，整条数组落盘


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _root(session_id=None) -> Path:
    d = Path(config.TOOL_RESULT_DIR) / re.sub(r'[^\w-]', '_', str(session_id or 'anon'))[:64]
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sweep(d: Path) -> None:
    files = sorted(d.glob('r*.json'), key=lambda p: p.stat().st_mtime)
    for old in files[:-_KEEP_FILES]:
        try:
            old.unlink()
            old.with_name(old.stem + '.meta.json').unlink()
        except Exception:                          # noqa: BLE001 - 清旧分片失败不影响本次
            pass


def _spill(session_id, name, arr) -> str:
    """整条数组落盘，返回 ref；写不成返回 ''（调用方改走 truncated）。"""
    ref = 'r' + uuid.uuid4().hex[:12]
    try:
        d = _root(session_id)
        (d / (ref + '.json')).write_text(_dump(arr), encoding='utf-8')
        (d / (ref + '.meta.json')).write_text(
            _dump({'name': name, 'total': len(arr), 'created': int(time.time())}),
            encoding='utf-8')
        _sweep(d)
        return ref
    except Exception as exc:                       # noqa: BLE001 - 落盘失败降级，不许崩
        print('[tools_result] 落盘失败（按截断处理）：%s: %s'
              % (type(exc).__name__, str(exc)[:120]), flush=True)
        return ''


def _hard_cut(obj, budget):
    """兜底：连骨架都放不下时，返回一个显式"省略了"的形状。"""
    if isinstance(obj, str):
        return obj[:max(0, budget - 20)] + '…（已截断）'
    return {'truncated': True,
            'note': '结果过大，已整体省略；需要数字请在 SQL 里先聚合（SUM / COUNT / ORDER BY … LIMIT 1）再查'}


def _fit(obj, budget, session_id, name):
    """把一个值裁到 budget 字以内：小字段原样、大值递归、数组换骨架 + 落盘。"""
    if len(_dump(obj)) <= budget:
        return obj
    if isinstance(obj, dict):
        out, heavy = {}, []
        for k, v in obj.items():
            if len(_dump(v)) <= _SMALL:
                out[k] = v
            else:
                heavy.append((k, v))
        left = budget - len(_dump(out)) - 12 * len(heavy)
        for i, (k, v) in enumerate(heavy):
            share = max(120, left // (len(heavy) - i))
            out[k] = _fit(v, share, session_id, '%s.%s' % (name, k))
            left -= len(_dump(out[k]))
        return out if len(_dump(out)) <= budget else _hard_cut(out, budget)
    if isinstance(obj, list):
        if not obj:
            return obj
        per = (budget - 120) // len(obj)
        if per >= _MIN_PER_ITEM:
            fitted = [_fit(x, per - 20, session_id, '%s[%d]' % (name, i))
                      for i, x in enumerate(obj)]
            return fitted if len(_dump(fitted)) <= budget else _hard_cut(fitted, budget)
        ref = _spill(session_id, name, obj)
        head = {'total': len(obj), 'shown': 0, 'items': [], 'next_offset': 0}
        if ref:
            head['ref'] = ref
        room = budget - len(_dump(head)) - 170      # 留够一句完整 note 的位置
        k = 0
        if room > 80:
            lo, hi = 0, len(obj)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if len(_dump(obj[:mid])) <= room:
                    lo = mid
                else:
                    hi = mid - 1
            k = lo
        head['shown'] = k
        head['items'] = obj[:k]
        head['next_offset'] = k if k < len(obj) else None
        notes = []
        if ref:
            notes = ['共 %d 条，这里给前 %d 条。合计 / 均值 / 最大最小取本结果的 summary；'
                     '其余明细用 read_result("%s", offset=%d) 取，不要拿看到的几行自己相加。'
                     % (len(obj), k, ref, k),
                     '共 %d 条，其余用 read_result("%s", offset=%d) 取。' % (len(obj), ref, k)]
        else:
            head['truncated'] = True
            notes = ['共 %d 条，落盘失败，只给前 %d 条，其余取不到' % (len(obj), k), '']
        for note in notes:
            head['note'] = note
            if len(_dump(head)) <= budget:
                return head
        head.pop('note', None)
        return head if len(_dump(head)) <= budget else _hard_cut(head, budget)
    if isinstance(obj, str):
        return obj[:max(0, budget - 20)] + '…（已截断）'
    return obj


def deliver(name, res, session_id=None, cap=None) -> str:
    """工具结果 → 给模型的 JSON 文本。没超就原样返回，不强加任何字段。"""
    cap = int(cap or config.TOOL_RESULT_CHARS or 12000)
    text = _dump(res)
    if len(text) <= cap:
        return text
    out = _fit(res, cap - 80, session_id, name)
    if len(_dump(out)) > cap:
        out = _hard_cut(out, cap)
    if isinstance(out, dict) and len(_dump(out)) <= cap - 40:
        out['_original_chars'] = len(text)
    return _dump(out)


def read_result(ref, offset=0, limit=20) -> dict:
    """取回 deliver 落盘的那条数组的一段。next_offset=None 表示读完。"""
    ref = str(ref or '').strip()
    if not _REF_RE.match(ref):
        return {'error': 'ref 不合法：只接受工具结果里返回的那个 ref'}
    hits = list(Path(config.TOOL_RESULT_DIR).glob('*/%s.json' % ref))
    if not hits:
        return {'error': '取不到了：ref 不存在或分片已过期，请重新查询'}
    try:
        arr = list(json.loads(hits[0].read_text(encoding='utf-8')))
    except Exception as exc:                       # noqa: BLE001 - 分片坏了当"取不到"
        return {'error': '分片读不出来：%s: %s' % (type(exc).__name__, str(exc)[:120])}
    off = max(0, int(offset or 0))
    lim = max(1, min(int(limit or 20), 200))
    cap = int(getattr(config, 'READ_RESULT_CHARS', 6000) or 6000)
    rows, used = [], 0
    for x in arr[off:off + lim]:
        s = len(_dump(x))
        if rows and used + s > cap:
            break
        rows.append(x)
        used += s
    if not rows and off < len(arr):                # 单条就超字数上限：给一条，否则永久卡在同一页
        rows = [arr[off]]
    end = off + len(rows)
    nxt = end if end < len(arr) else None
    return {'ref': ref, 'total': len(arr), 'offset': off, 'rows': rows, 'next_offset': nxt,
            'note': ('接着取 offset=%d（还剩 %d 条）' % (nxt, len(arr) - end))
            if nxt is not None else
            '已到末尾：共 %d 条，本次给的是第 %d-%d 条' % (len(arr), off + 1, end)}
