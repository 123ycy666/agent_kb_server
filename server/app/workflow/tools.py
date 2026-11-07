"""工具：只做取数与落库，不做任何业务判断。

2026-11-07 定死：取数写结果（wf_item），发送读结果 + 找人 + 写每人版本（wf_notify）。
发送工种不给业务库 run_sql —— 一句话只从结果表来。
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.workflow import store
from app.workflow.models import RuleDoc, Run

MAX_ROWS = 300


def _fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {'type': 'function', 'function': {
        'name': name, 'description': desc,
        'parameters': {'type': 'object', 'properties': props, 'required': required}}}


_STR = {'type': 'string'}
_NUM = {'type': 'number'}

TOOLS_COMMON = [
    _fn('list_tables', '列出可查的表（只列白名单里登记过的）', {'keyword': _STR}, []),
    _fn('describe_table', '看一张表的字段与含义', {'table': _STR}, ['table']),
    _fn('run_sql', '执行只读查询，返回 columns/rows/row_count。一次最多回 %d 行' % MAX_ROWS,
        {'sql': _STR}, ['sql']),
]

TOOLS_DETECT = TOOLS_COMMON + [
    _fn('write_result',
        '写一条检查结果。每个被检查的对象都要写一次，命中与不命中都要写。'
        '归属所、归属县必填（发送按它们分层）；命中时框架自动建/更新结果',
        {'object_key': _STR, 'object_name': _STR, 'hit': {'type': 'boolean'},
         'unit_name': {'type': 'string', 'description': '归属单位名（发送按它分所级）'},
         'county_name': {'type': 'string', 'description': '上级单位名（发送按它分县级）'},
         'value': _NUM, 'value_unit': {'type': 'string', 'description': '值单位'},
         'threshold': _NUM, 'tier': {'type': 'string', 'description': 'high/mid/low'},
         'detail': {'type': 'object', 'description': '明细键值对，给人和前端看'},
         'evidence': {'type': 'object', 'description': '依据：表.字段、口径、数据截止日'},
         'reliable': {'type': 'boolean', 'description': '数据是否可靠'}},
        ['object_key', 'object_name', 'hit', 'unit_name', 'county_name', 'detail', 'evidence']),
    _fn('write_results',
        '批量写检查结果（一次 5~15 条更快）。items 里每个元素同 write_result 的参数；'
        '每个被检查的对象都要出现一次，命中与不命中都要写',
        {'items': {'type': 'array', 'items': {'type': 'object'}}}, ['items']),
    _fn('finish', '本轮结束：交检查对象数与一句话结论',
        {'checked_n': {'type': 'integer'}, 'data_as_of': _STR, 'note': _STR}, ['checked_n']),
]

TOOLS_SEND = TOOLS_COMMON[:2] + [
    _fn('find_user', '按单位名和岗位/角色关键字找可收件的人（返回登录名、姓名、部门、岗位、角色）',
        {'unit': {'type': 'string', 'description': '单位名关键字；留空表示不限'},
         'role': {'type': 'string', 'description': '岗位或角色关键字'},
         'limit': {'type': 'integer', 'description': '最多返回几个，默认 10'}},
        ['role']),
    _fn('read_items', '读结果（默认只读未关闭的）。doc_name 传本文档对应的取数文档名；'
        '结果多时用 offset 翻页（每次最多 200 条）',
        {'doc_name': _STR, 'status': _STR, 'alert_type': _STR, 'tier': _STR,
         'limit': {'type': 'integer', 'description': '每页多少条，默认 200'},
         'offset': {'type': 'integer', 'description': '从第几条开始，默认 0'},
         'with_detail': {'type': 'boolean', 'description': '要不要带明细，默认不带'}},
        ['doc_name']),
    _fn('send', '发一条给某人：message 是收件人看到的那一句话；alert_ids 是这句话覆盖的结果 id',
        {'item_ids': {'type': 'array', 'items': {'type': 'integer'},
                      'description': '这一句覆盖的结果 id，第一个当成最差那条'},
         'level': {'type': 'string', 'description': '层面，取值由发送文档第四节定'},
         'scope_name': {'type': 'string', 'description': '这句话说的是谁的范围'},
         'recipient_login': _STR, 'recipient_name': _STR, 'role_code': _STR,
         'message': {'type': 'string', 'description': '收件人看到的一句话，≤30 字'},
         'reason': _STR, 'channel': _STR},
        ['item_ids', 'level', 'scope_name', 'recipient_login', 'message', 'reason']),
    _fn('finish', '本轮结束：交发出的条数与一句话结论',
        {'sent_n': {'type': 'integer'}, 'note': _STR}, []),
]

SUBSETS = {'detect': TOOLS_DETECT, 'send': TOOLS_SEND}


def tools_for(work_type: str) -> list[dict]:
    return TOOLS_SEND if work_type == 'send' else TOOLS_DETECT


def _short(r: dict, n: int = MAX_ROWS) -> dict:
    rows = r.get('rows') or []
    out = {'columns': r.get('columns') or [], 'row_count': r.get('row_count', len(rows)),
           'rows': rows[:n], 'sql': r.get('sql') or ''}
    if r.get('error'):
        out['error'] = r['error']
    if r.get('hint'):
        out['hint'] = r['hint']
    if len(rows) > n:
        out['truncated'] = '只回了前 %d 行，请加聚合或 LIMIT' % n
    return out


def _msg_problem(db: Session, message: str) -> str:
    """一句话校验：规则全在运行参数里，代码不写死任何口径。"""
    if not message:
        return 'message 必填'
    p = store.params(db)

    def v(k: str) -> str:
        return str((p.get(k) or ('', ''))[0] or '').strip()

    maxc = v('msg_max_chars')
    if maxc.isdigit() and len(message) > int(maxc):
        return 'message 超 %s 字（现在 %d 字）' % (maxc, len(message))
    low = message.lower()
    for w in [x.strip() for x in v('msg_banned_words').split(',') if x.strip()]:
        if w.lower() in low:
            return 'message 不能出现 %s' % w
    maxsep = v('msg_max_separators')
    if maxsep.isdigit() and message.count('、') > int(maxsep):
        return 'message 顿号超过 %s 个' % maxsep
    maxnum = v('msg_max_numbers')
    if maxnum.isdigit():
        import re
        if len(re.findall(r'\d+(?:\.\d+)?', message)) > int(maxnum):
            return 'message 数字超过 %s 个' % maxnum
    return ''


def call(ctx: dict, name: str, args: dict) -> dict:
    db: Session = ctx['db']
    run: Run = ctx['run']
    doc: RuleDoc = ctx['doc']
    dry = bool(ctx.get('dry'))
    args = args or {}

    if name == 'list_tables':
        from app.secretary import tools_db
        return _short(tools_db.list_tables(str(args.get('keyword') or '')))

    if name == 'describe_table':
        from app.secretary import tools_db
        return _short(tools_db.describe_table(str(args.get('table') or '')))

    if name == 'run_sql':
        from app.secretary import tools_db
        r = tools_db.run_sql(str(args.get('sql') or ''))
        store.log(db, run, 'sql', {'sql': (r.get('sql') or '')[:4000],
                                   'rows': r.get('row_count'),
                                   'error': (r.get('error') or '')[:500]})
        return _short(r)

    if name == 'find_user':
        from app.secretary import tools_db
        unit = str(args.get('unit') or '').strip()
        role = str(args.get('role') or '').strip()
        if not unit and not role:
            return {'ok': False, 'error': 'unit 和 role 至少给一个'}
        base = store.param(db, 'user_query_sql', '').strip()
        if not base:
            return {'ok': False, 'error': '没配账号查询：运行参数里要填 user_query_sql'}
        lim = max(1, min(50, int(args.get('limit') or 10)))
        conds: list = []
        params: list = []
        for key, value in (('user_filter_unit', unit), ('user_filter_role', role)):
            if not value:
                continue
            frag = store.param(db, key, '').strip()
            if not frag:
                return {'ok': False, 'error': '没配 %s' % key}
            conds.append('(' + frag + ')')
            params.extend(['%' + value + '%'] * frag.count('%s'))
        sql = base.replace('{where}', ' AND '.join(conds) or '1=1').replace('{limit}', str(lim))
        r = tools_db._exec(sql, params, limit_rows=lim)
        rows = [dict(x) for x in (r.get('rows') or [])]
        store.log(db, run, 'tool', {'tool': 'find_user', 'unit': unit, 'role': role, 'found': len(rows)})
        if not rows:
            return {'count': 0, 'users': [],
                    'hint': '没找到人：单位名或岗位关键字可能不对，换关键字再试；确实没有就不发这一层'}
        return {'count': len(rows), 'users': rows}

    if name == 'read_items':
        rows = store.read_items(db, doc_name=str(args.get('doc_name') or '') or None,
                                doc_id=args.get('doc_id') or None,
                                status=str(args.get('status') or 'open'),
                                alert_type=args.get('alert_type') or None,
                                tier=args.get('tier') or None,
                                limit=int(args.get('limit') or 200),
                                offset=int(args.get('offset') or 0),
                                with_detail=bool(args.get('with_detail')))
        ctx['reads'] = ctx.get('reads', 0) + 1
        ctx['counts']['checked_n'] = max(ctx['counts'].get('checked_n', 0), len(rows))
        return {'count': len(rows), 'offset': int(args.get('offset') or 0), 'items': rows}

    if name == 'send':
        ids = args.get('item_ids') or []
        if not isinstance(ids, list) or not ids:
            return {'ok': False, 'error': 'item_ids 必须是非空数组'}
        level = str(args.get('level') or '')
        login = str(args.get('recipient_login') or '').strip()
        reason = str(args.get('reason') or '').strip()
        message = str(args.get('message') or '').strip()
        if not level:
            return {'ok': False, 'error': 'level 必填，取值见发送文档第四节'}
        if not login or not reason:
            return {'ok': False, 'error': 'recipient_login 与 reason 必填'}
        bad = _msg_problem(db, message)
        if bad:
            return {'ok': False, 'error': bad}
        if store.recent_sent(db, login, level, doc.alert_type or ''):
            return {'ok': True, 'skipped': '该收件人该层面在 no_repeat_days 内已发过'}
        if dry:
            ctx['counts']['would_send'] = ctx['counts'].get('would_send', 0) + 1
            return {'ok': True, 'dry': True}
        r = store.add_notify(db, run, doc, level=level,
                             scope_name=str(args.get('scope_name') or ''), login=login,
                             name=str(args.get('recipient_name') or ''),
                             role=str(args.get('role_code') or ''), message=message,
                             alert_ids=ids, reason=reason, channel=str(args.get('channel') or ''))
        ctx['counts']['sent_n'] = ctx['counts'].get('sent_n', 0) + (1 if r.get('ok') else 0)
        return r

    if name == 'write_results':
        items = args.get('items') or []
        if not isinstance(items, list) or not items:
            return {'ok': False, 'error': 'items 必须是非空数组'}
        ok = fail = 0
        errs = []
        for it in items[:30]:
            r = call(ctx, 'write_result', it if isinstance(it, dict) else {})
            if r.get('ok'):
                ok += 1
            else:
                fail += 1
                if len(errs) < 3:
                    errs.append(r.get('error'))
        return {'ok': fail == 0, 'written': ok, 'failed': fail, 'errors': errs}

    if name == 'write_result':
        key = str(args.get('object_key') or '').strip() or str(args.get('object_name') or '').strip()
        if not key:
            return {'ok': False, 'error': 'object_key 必填'}
        need = ('detail', 'evidence')
        lack = [k for k in need if not isinstance(args.get(k), dict) or not args.get(k)]
        if lack:
            return {'ok': False, 'error': '缺 %s：明细和依据必须给' % '、'.join(lack)}
        if not str(args.get('unit_name') or '').strip() or not str(args.get('county_name') or '').strip():
            return {'ok': False, 'error': 'unit_name / county_name 必填：发送要按归属分层'}
        if dry:
            ctx['keys'].add(key)
            ctx['counts']['checked_n'] = ctx['counts'].get('checked_n', 0) + 1
            ctx['counts']['hit_n'] = ctx['counts'].get('hit_n', 0) + (1 if args.get('hit') else 0)
            return {'ok': True, 'dry': True}
        r = store.write_item(
            db, run, doc, object_key=key, object_name=str(args.get('object_name') or ''),
            hit=bool(args.get('hit')), unit_name=str(args.get('unit_name') or ''),
            county_name=str(args.get('county_name') or ''), value=_num(args.get('value')),
            value_unit=str(args.get('value_unit') or ''), threshold=_num(args.get('threshold')),
            tier=(str(args.get('tier'))[:8] if args.get('tier') else None),
            detail=args.get('detail') or {}, evidence=args.get('evidence') or {},
            reliable=args.get('reliable') is not False)
        ctx['keys'].add(key)
        if r.get('ok'):
            ctx['counts']['hit_n'] = ctx['counts'].get('hit_n', 0) + (1 if args.get('hit') else 0)
            if r.get('state') == 'new':
                ctx['counts']['new_n'] = ctx['counts'].get('new_n', 0) + 1
        return r

    if name == 'finish':
        ctx['finished'] = True
        if args.get('checked_n') is not None:
            ctx['counts']['checked_n'] = int(args.get('checked_n') or 0)
        if args.get('data_as_of'):
            ctx['data_as_of'] = str(args.get('data_as_of'))
        return {'ok': True, 'note': str(args.get('note') or '')[:500]}

    return {'ok': False, 'error': '未知工具：%s' % name}


def _num(v):
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False, default=str)
