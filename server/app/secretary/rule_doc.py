# -*- coding: utf-8 -*-
"""规则取用：目录 + 按主题/代号精确取（2026-11-02 重写取法）。

规则不在知识库 `document` 里，在 `prompt_material` 表（四份材料）：
- **必带**的小节由 `prompt_material.block()` 每轮直接拼进提示词；
- **不勾必带**的小节只存在库里，模型看清单、再用 `get_rule(代号)` 取原文。

2026-11-02 为什么改成"认代号"：清单里原来是 40 字带括号举例的长标题，模型不肯原样抄，
凭印象编短名（「预算执行达标判据」「十七、资金合理性」）→ 这里报"没有这条规则"，
一次取用就白跑。现在清单给 `20.2 ｜ 审批待办 / 审批积压类` 这种短代号，
`get_rule('20.2')` 一步命中；实在传了别的说法，用字二元组打分兜一道，都不行就回
5 条候选代号让模型重试。代号由标题前缀自动解析（`prompt_material.code_of`），
**没有需要人维护的东西**。

⚠️ 2026-11-01 之前这里查的是 `document.doc_type='rule'`。规则搬走后那张表已经没有 rule
   文档，如果不改这里，「取消必带」就等于「永远取不到」——静默失效，比报错更糟。
"""
from __future__ import annotations

import re

from app.secretary import prompt_material

MAX_TOPICS = 200         # 目录上限（四份合计 53 节，留足余量）
MAX_TEXT = 4000          # 单次取回正文上限
GUESS_MIN = 0.34         # 二元组打分兜底的最低分（宁缺勿滥：低了就明确说没有）


def _rows() -> list[dict]:
    out = []
    for slot in prompt_material.SLOTS:
        for r in prompt_material.sections(slot):
            out.append({
                'slot': slot,
                'section': str(r.get('section') or ''),
                'body': str(r.get('body') or ''),
                'pinned': bool(r.get('pinned')),
                'code': prompt_material.code_of(r.get('section')),
            })
    return out


def topics(force: bool = False) -> list[dict]:
    """全部小节名（跨四份），带代号与短名。"""
    items = []
    for r in _rows():
        if not r['section']:
            continue
        body = r['body'].replace('\n', ' ')
        items.append({
            'topic': r['section'],
            'code': r['code'],
            'label': prompt_material.labeled(r['section']),
            'source': prompt_material.SLOT_NAMES.get(r['slot'], r['slot']),
            'hint': (body[:26] + '…') if len(body) > 26 else body,
            'pinned': r['pinned'],
        })
        if len(items) >= MAX_TOPICS:
            break
    return items


def menu(force: bool = False) -> str:
    """目录文本：一行一节（代号 ｜ 短名）。"""
    items = topics()
    if not items:
        return ''
    lines = ['【规则目录（要按某类题作答前先 get_rule(代号) 取原文，取不到就说明没有这条标准）】']
    for it in items:
        lines.append('- %s' % it['label'])
    return '\n'.join(lines)


def _bigrams(s: str) -> set:
    s = ''.join(ch for ch in str(s or '') if not ch.isspace())
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else ({s} if s else set())


def _dice(a: str, b: str) -> float:
    """字二元组重合率，0~1。用来兜"模型传了一句别的说法"这种情况。"""
    x, y = _bigrams(a), _bigrams(b)
    if not x or not y:
        return 0.0
    return 2.0 * len(x & y) / (len(x) + len(y))


def _phrases(section: str) -> list[str]:
    """标题括号里的典型问法（「哪些卡住了」「能不能达标」…）。规则作者写在标题里，
    这里只用来给 `get_rule` 的**参数**做兜底匹配，不参与"自动决定带哪条"。"""
    return [p for p in re.findall(r'「([^」]{2,40})」', str(section or '')) if p.strip()]


def _score(want: str, r: dict) -> float:
    """跟全名、短名、标题里的典型问法各比一次，取最高分。

    只跟全名比会被括号里的举例稀释（标题四五十字，实测只有 0.1 上下），
    而模型说的往往正是括号里那句（「年底大概能到多少」≈「年底能到多少」）。
    """
    cands = [r['section'], prompt_material.short_name(r['section'])]
    cands.extend(_phrases(r['section']))
    return max((_dice(want, c) for c in cands if c), default=0.0)


def _pack(want: str, rows: list[dict], matched_by: str, score: float | None = None,
          also: list[str] | None = None) -> dict:
    parts: list[str] = []
    used = 0
    for r in rows:
        body = r['body'].strip()
        if not body:
            continue
        if used + len(body) > MAX_TEXT:
            body = body[: max(0, MAX_TEXT - used)]
        parts.append(body)
        used += len(body)
        if used >= MAX_TEXT:
            break
    text = '\n'.join(parts)
    out = {'ok': bool(text),
           'topic': want,
           'section': rows[0]['section'],
           'code': rows[0]['code'],
           'source': prompt_material.SLOT_NAMES.get(rows[0]['slot'], ''),
           'matched_by': matched_by,
           'text': text}
    if score is not None:
        out['score'] = round(score, 3)
    if also:
        out['also'] = also
    # 打分兜底命中时把话说清：这是"最接近的一条"，不一定对口。
    # 实测（2026-11-02）问「今年预算能达标吗」，模型传「预算执行达标判据」→ 兜底命中
    # 「三、预算执行」（口径对、题型不对），模型就以为取到了，不再试 20.11。
    if matched_by == 'guess':
        out['text'] = ('（下面这条是按字面最接近挑的：%s ｜ %s，**不一定对口**；'
                       '若不合用，可用候选代号再取一次：%s）\n'
                       % (rows[0]['code'] or rows[0]['section'][:12],
                          prompt_material.short_name(rows[0]['section']),
                          '、'.join(also or []) or '无')) + text
    return out


def _candidates(want: str, n: int = 5) -> list[dict]:
    """取不到时给 5 条候选（带代号），让模型拿代号重试一次，而不是丢一堆长标题。"""
    rows = [r for r in _rows() if r['section']]
    scored = sorted(rows, key=lambda r: -_score(want, r))
    return [{'code': r['code'], 'label': prompt_material.labeled(r['section'])}
            for r in scored[:n]]


def get(topic: str = '', key: str = '', force: bool = False) -> dict:
    """按代号 / 小节名取正文。命中就给；命中不到明确说没有，并给候选代号。"""
    want = str(topic or key or '').strip().strip('「」"\'')
    if not want:
        return {'ok': False,
                'error': '缺少 topic（要查哪条规则）：传代号，如 20.2',
                'candidates': _candidates('')}

    rows = [r for r in _rows() if r['section']]

    # ① 代号精确（清单里就是它，模型抄这个最省事）
    code = want.rstrip('、.．').strip()
    for r in rows:
        if r['code'] and r['code'] == code:
            return _pack(want, [r], 'code')

    # ② 全名精确
    for r in rows:
        if r['section'] == want:
            return _pack(want, [r], 'exact')

    # ③ 子串（双向，但要求 want 至少两个字，避免「一」「十」这种代号误伤一堆节）
    if len(want) >= 2:
        subs = [r for r in rows if want in r['section'] or r['section'] in want]
        if subs:
            others = [prompt_material.labeled(x['section']) for x in subs[1:4]]
            return _pack(want, subs[:1], 'substr', also=others or None)

    # ④ 打分兜底（宁缺勿滥）
    scored = sorted(((_score(want, r), r) for r in rows), key=lambda x: -x[0])
    if scored and scored[0][0] >= GUESS_MIN:
        best = scored[0]
        near = [prompt_material.labeled(x[1]['section']) for x in scored[1:4]]
        return _pack(want, [best[1]], 'guess', score=best[0], also=near or None)

    return {'ok': False,
            'error': '没有这条规则：%s（请用清单里的代号重试，比如 20.2）' % want,
            'candidates': _candidates(want)}
