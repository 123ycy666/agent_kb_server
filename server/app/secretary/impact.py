# -*- coding: utf-8 -*-
"""问数线业务规则：一次回答的输入只有这 8 项，全部可改（页面就地编辑）。

代码里不再有业务规则；这里只负责把每项"现在的原文"读出来给页面看。
`segments()` 给列表，`body(key)` 给某一项原文。
"""
from __future__ import annotations

import inspect
import json

EMPTY = '（现在没有内容）'


def _conn():
    import psycopg
    from psycopg.rows import dict_row

    from app.secretary import pgdb
    return psycopg.connect(pgdb.DSN, autocommit=True, row_factory=dict_row)


def _fetch(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchall() if cur.description else []


def _try_fetch(cur, *sqls):
    last = None
    for sql in sqls:
        try:
            return _fetch(cur, sql)
        except Exception as exc:                     # noqa: BLE001
            last = exc
    raise last if last else RuntimeError('没有可用的 SQL')


def _src(mod, name):
    fn = getattr(mod, name, None)
    if fn is None:
        return '（代码里找不到 %s）' % name
    try:
        return inspect.getsource(fn)
    except Exception as exc:                         # noqa: BLE001
        return '（读不到源码：%s）' % str(exc)[:120]


def _text(text, size=None, note=''):
    body = str(text or '')
    out = {'kind': 'text', 'text': body, 'size': size or ('%d 字' % len(body))}
    if note:
        out['note'] = note
    return out


def _rows(rows, size=None, note=''):
    list_rows, cols = [], []
    for r in rows:
        one = {}
        for k, v in (r or {}).items():
            if k not in cols:
                cols.append(k)
            if v is None:
                one[k] = ''
            elif isinstance(v, (dict, list)):
                one[k] = json.dumps(v, ensure_ascii=False, default=str)
            else:
                one[k] = str(v)
        list_rows.append(one)
    out = {'kind': 'rows', 'columns': cols, 'rows': list_rows,
           'size': size or ('%d 条' % len(list_rows))}
    if note:
        out['note'] = note
    return out


def _empty(why='（这一项是空的）'):
    return {'kind': 'text', 'text': EMPTY + '\n' + why, 'size': '0 字'}


# ── 探针 ────────────────────────────────────────────────────────────
def _p_route(cur):
    rows = _fetch(cur, 'select k, body, version_no from cfg.route_rule order by k')
    if not rows:
        return _empty('cfg.route_rule 里没有行，入口只能用代码兜底词表。')
    return _text('\n\n'.join('## %s（v%s）\n%s' % (r['k'], r.get('version_no'),
                                                  json.dumps(r['body'], ensure_ascii=False, indent=2)
                                                  if isinstance(r['body'], (dict, list)) else str(r['body']))
                             for r in rows))


def _p_lex(cur):
    from app.secretary import lex_import
    total = 0
    rows = []
    for key in lex_import.ORDER:
        spec = lex_import.SPECS[key]
        flag = 'enabled' if spec.get('upsert') else 'deleted_flag = 0'
        try:
            n = _fetch(cur, 'select count(*) as n from %s where %s' % (spec['table'], flag))
            c = int((n[0] or {}).get('n') or 0)
        except Exception:                            # noqa: BLE001
            c = 0
        total += c
        rows.append({'词典': key, '表': spec['table'], '条数': c, '说明': spec['name']})
    return _rows(rows, size='%d 条' % total,
                 note='四类可逐行改，五类可批量导入（CSV / XLSX）；拼音码导入时由系统现算。'
                      '改完 60 秒内重载，也可以点「立即生效」。')


def _p_askfill(cur):
    from app.secretary import prompt_text
    raw = prompt_text.get('askfill', '')
    if not raw.strip():
        return _empty('补问法为空 = 不补问。')
    try:
        return _text(json.dumps(json.loads(raw), ensure_ascii=False, indent=2))
    except Exception:                                # noqa: BLE001
        return _text(raw, note='这段不是合法 JSON，代码会按空处理（= 不补问）。')


def _p_complete(cur):
    from app.secretary import complete_rules
    rows = [dict(r) for r in (complete_rules.rows() or [])]
    if not rows:
        return _empty('补全表 0 条。')
    return _rows([{'id': r.get('id'), 'kind': r.get('kind'), 'match_text': r.get('match_text'),
                   'fill_text': r.get('fill_text'), 'priority': r.get('priority'),
                   'enabled': r.get('enabled')} for r in rows],
                 size='%d 条' % len(rows), note='命中后作为参考资料交给问题确定，不替用户改问题。')


def _p_confirm(cur):
    from app.secretary import prompt_text
    body = prompt_text.get('confirm', '')
    if not body.strip():
        return _empty('问题确定框是空的 = 不归整，纠错后的原话直接往下传。')
    return _text(body)


def _p_identity(cur):
    from app.secretary import prompt_text, prompts_data
    body = prompt_text.get('identity', '')
    if body.strip():
        return _text(body, note='页面上覆盖过的身份（cfg.prompt_text.identity）。')
    return _text(prompts_data.IDENTITY, note='没有覆盖 → 用代码里的默认身份。')


def _p_material(slot):
    def probe(cur):
        from app.secretary import prompt_material as pm
        rows = pm.sections(slot)
        if not rows:
            return _empty('cfg.prompt_material 的 slot=%s 没有启用的节 —— 模型收不到。' % slot)
        chars = sum(len(str(r.get('body') or '')) for r in rows)
        body = '\n\n'.join('## %s（%s）\n%s' % (r.get('section'),
                                               '必带' if r.get('pinned') else '按需',
                                               str(r.get('body') or '').strip()) for r in rows)
        return _text(body, size='%d 字 / %d 节' % (chars, len(rows)),
                     note='这里是全部小节；注入只取「必带」的节。')
    return probe


CHAIN = [
    {'key': 'route', 'name': '① 入口分流', 'items': [
        {'key': 'route_rule', 'name': '入口判据（产物词表 / 动词 / 指代文件）',
         'where': 'cfg.route_rule', 'editable': True, 'takes_effect': '下一轮',
         'note': '判成问数还是干活；判错整条线走错。', 'editor': 'route_rule', 'probe': _p_route},
    ]},
    {'key': 'fix', 'name': '② 名称与错字纠错', 'items': [
        {'key': 'lexicon', 'name': '七张纠错词典',
         'where': 't_nc_* / cfg.t_nc_word', 'editable': True, 'takes_effect': '60 秒',
         'note': '名称库、拼音表、错别字、标准词、单位规则；可批量导入，导完可立即生效。',
         'editor': 'lexicon', 'probe': _p_lex},
    ]},
    {'key': 'askfill', 'name': '③ 补问法', 'items': [
        {'key': 'askfill', 'name': '补问词与话术',
         'where': 'cfg.prompt_text.askfill（JSON）', 'editable': True, 'takes_effect': '下一轮',
         'note': '缺时间/对象时补一句；"on": false 整段关掉。', 'editor': 'askfill',
         'probe': _p_askfill},
    ]},
    {'key': 'complete', 'name': '④ 补全表', 'items': [
        {'key': 'complete_rule', 'name': '补全条目',
         'where': 'cfg.complete_rule', 'editable': True, 'takes_effect': '下一轮',
         'note': '命中后作为参考资料给问题确定。', 'editor': 'complete', 'probe': _p_complete},
    ]},
    {'key': 'confirm', 'name': '⑤ 问题确定', 'items': [
        {'key': 'confirm', 'name': '问题确定规则',
         'where': 'cfg.prompt_text.confirm', 'editable': True, 'takes_effect': '下一轮',
         'note': '把口语问句揉成一句可查的问题；空 = 原话往下传。', 'editor': 'text:confirm',
         'probe': _p_confirm},
    ]},
    {'key': 'identity', 'name': '⑥ 装配 system：身份', 'items': [
        {'key': 'identity', 'name': '身份（整个智能体就这一份）',
         'where': 'cfg.prompt_text.identity', 'editable': True, 'takes_effect': '下一轮',
         'note': '只说你是谁、服务谁；纪律写在作答规则里。', 'editor': 'text:identity',
         'probe': _p_identity},
    ]},
    {'key': 'fetch', 'name': '⑦ 查数口径', 'items': [
        {'key': 'material_fetch', 'name': '表字段 + 指标算法 + 必需过滤',
         'where': 'cfg.prompt_material（slot=fetch）', 'editable': True, 'takes_effect': '下一轮',
         'note': '模型写 SQL 的唯一依据。表、字段、指标怎么算都写在这里。',
         'editor': 'material:fetch', 'probe': _p_material('fetch')},
    ]},
    {'key': 'answer', 'name': '⑧ 作答规则', 'items': [
        {'key': 'material_answer', 'name': '怎么写答案',
         'where': 'cfg.prompt_material（slot=answer）', 'editable': True, 'takes_effect': '下一轮',
         'note': '口吻、格式、数字出处、查不到怎么说。', 'editor': 'material:answer',
         'probe': _p_material('answer')},
    ]},
]


def _index():
    return {it['key']: (seg, it) for seg in CHAIN for it in seg['items']}


def _run(seg, it, cur):
    try:
        return it['probe'](cur)
    except Exception as exc:                          # noqa: BLE001 - 取不到也要能读
        return {'kind': 'text', 'size': '读不到',
                'text': '%s\n\n读取失败：%s: %s' % (EMPTY, type(exc).__name__, str(exc)[:300])}


def _meta(seg, it, p):
    return {'key': it['key'], 'seg': seg['name'], 'name': it['name'],
            'where': it['where'], 'size': p.get('size') or '',
            'editable': bool(it['editable']), 'takes_effect': it['takes_effect'],
            'note': it['note'], 'editor': it['editor'], 'kind': p.get('kind') or 'text'}


def segments():
    out = []
    try:
        conn = _conn()
    except Exception as exc:                          # noqa: BLE001
        return [{'key': 'error', 'name': '读不到配置库',
                 'items': [{'key': 'error', 'seg': '读不到配置库', 'name': '数据库连不上',
                            'where': 'PostgreSQL', 'size': '-', 'editable': True,
                            'takes_effect': '-', 'note': str(exc)[:200], 'editor': '',
                            'kind': 'text'}]}]
    try:
        cur = conn.cursor()
        try:
            for seg in CHAIN:
                out.append({'key': seg['key'], 'name': seg['name'],
                            'items': [_meta(seg, it, _run(seg, it, cur)) for it in seg['items']]})
        finally:
            cur.close()
    finally:
        conn.close()
    return out


def body(key):
    idx = _index().get(key)
    if not idx:
        return {'error': '未知的项：%s' % key}
    seg, it = idx
    conn = _conn()
    try:
        cur = conn.cursor()
        try:
            p = _run(seg, it, cur)
        finally:
            cur.close()
    finally:
        conn.close()
    out = dict(_meta(seg, it, p))
    out.update({'kind': p.get('kind') or 'text', 'text': p.get('text') or '',
                'columns': p.get('columns') or [], 'rows': p.get('rows') or [],
                'hint': p.get('note') or ''})
    return out
