"""工具：只做取数与落库，不做任何业务判断。"""
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
        '对象、当前值、阈值、档位、明细、依据必须齐；hit=true 时框架会自动建/更新事项',
        {'object_key': _STR, 'object_name': _STR, 'value': _NUM, 'threshold': _NUM,
         'op': {'type': 'string', 'description': '> >= < <= = 之一'},
         'tier': {'type': 'string', 'description': 'high/mid/low'},
         'hit': {'type': 'boolean'}, 'scope_type': _STR, 'scope_key': _STR,
         'detail': {'type': 'object', 'description': '明细键值对，给人和对话看'},
         'evidence': {'type': 'object', 'description': '依据：表.字段、口径、数据截止日'},
         'reliable': {'type': 'boolean', 'description': '数据是否可靠'},
         'title': _STR, 'body': _STR},
        ['object_key', 'object_name', 'hit', 'detail', 'evidence']),
    _fn('write_results',
        '批量写检查结果（一次 5~15 条更快）。items 里每个元素同 write_result 的参数；'
        '每个被检查的对象都要出现一次，命中与不命中都要写',
        {'items': {'type': 'array', 'items': {'type': 'object'}}}, ['items']),
    _fn('finish', '本轮结束：交检查对象数与一句话结论',
        {'checked_n': {'type': 'integer'}, 'data_as_of': _STR, 'note': _STR}, ['checked_n']),
]

TOOLS_SEND = TOOLS_COMMON + [
    _fn('find_user', '按单位名和岗位/角色关键字找可收件的人（返回登录名、姓名、部门、岗位、角色）',
        {'unit': {'type': 'string', 'description': '单位名关键字，如 两水；留空表示不限单位'},
         'role': {'type': 'string', 'description': '岗位或角色关键字，如 所长、负责人、财务；留空不限'},
         'limit': {'type': 'integer', 'description': '最多返回几个，默认 10'}},
        []),
    _fn('read_items', '读事项（默认只读未关闭的）',
        {'status': _STR, 'alert_type': _STR, 'tier': _STR,
         'limit': {'type': 'integer'}}, []),
    _fn('send', '发一条给某人：必须写清依据（为什么发给他）',
        {'item_id': {'type': 'integer'}, 'recipient_login': _STR, 'recipient_name': _STR,
         'reason': _STR, 'channel': _STR}, ['item_id', 'recipient_login', 'reason']),
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
            return {'ok': False,
                    'error': 'unit 和 role 至少给一个。unit 传事项对象里的单位名（如 厉山供电所），'
                             'role 传岗位关键字（如 所长、负责人、财务）'}
        lim = max(1, min(50, int(args.get('limit') or 10)))
        where = ['e.deleted_flag=0', 'e.disabled_flag=0']
        params: list = []
        if unit:
            where.append('d.department_name LIKE %s')
            params.append('%' + unit + '%')
        if role:
            where.append('(p.position_name LIKE %s OR r.role_name LIKE %s)')
            params.extend(['%' + role + '%', '%' + role + '%'])
        sql = ('SELECT e.login_name AS login_name, e.actual_name AS actual_name, '
               'd.department_name AS dept, p.position_name AS position, '
               'GROUP_CONCAT(DISTINCT r.role_name) AS roles '
               'FROM t_employee e '
               'LEFT JOIN t_department d ON d.department_id = e.department_id '
               'LEFT JOIN t_position p ON p.position_id = e.position_id '
               'LEFT JOIN t_role_employee re ON re.employee_id = e.employee_id '
               'LEFT JOIN t_role r ON r.role_id = re.role_id '
               'WHERE ' + ' AND '.join(where) + ' '
               'GROUP BY e.employee_id, e.login_name, e.actual_name, d.department_name, p.position_name '
               'ORDER BY e.login_name LIMIT %s')
        params.append(lim)
        r = tools_db._exec(sql, params, limit_rows=lim)
        rows = [dict(x) for x in (r.get('rows') or [])]
        store.log(db, run, 'tool', {'tool': 'find_user', 'unit': unit, 'role': role, 'found': len(rows)})
        if not rows:
            return {'count': 0, 'users': [],
                    'hint': '没找到人：单位名或岗位关键字可能不对，换关键字再试；确实没有就按规则不发'}
        return {'count': len(rows), 'users': rows}

    if name == 'read_items':
        rows = store.read_items(db, status=str(args.get('status') or 'open'),
                                alert_type=args.get('alert_type') or None,
                                tier=args.get('tier') or None,
                                limit=int(args.get('limit') or 200))
        ctx['reads'] = ctx.get('reads', 0) + 1
        ctx['counts']['checked_n'] = max(ctx['counts'].get('checked_n', 0), len(rows))
        return {'count': len(rows), 'items': rows[:200]}

    if name == 'send':
        item_id = int(args.get('item_id') or 0)
        login = str(args.get('recipient_login') or '').strip()
        reason = str(args.get('reason') or '').strip()
        if not item_id or not login or not reason:
            return {'ok': False, 'error': 'item_id / recipient_login / reason 都必填'}
        if store.recent_sent(db, item_id, login):
            return {'ok': True, 'skipped': '已在该静默期内发过'}
        if dry:
            ctx['counts']['would_send'] = ctx['counts'].get('would_send', 0) + 1
            return {'ok': True, 'dry': True}
        r = store.add_notify(db, run, item_id, login, str(args.get('recipient_name') or ''),
                             reason, str(args.get('channel') or ''))
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
        if dry:
            ctx['keys'].add(key)
            ctx['counts']['checked_n'] = ctx['counts'].get('checked_n', 0) + 1
            ctx['counts']['hit_n'] = ctx['counts'].get('hit_n', 0) + (1 if args.get('hit') else 0)
            return {'ok': True, 'dry': True}
        r = store.write_result(
            db, run, doc, object_key=key, object_name=str(args.get('object_name') or ''),
            scope_type=args.get('scope_type') or None, scope_key=args.get('scope_key') or None,
            value=_num(args.get('value')), threshold=_num(args.get('threshold')),
            op=(str(args.get('op'))[:8] if args.get('op') else None),
            tier=(str(args.get('tier'))[:8] if args.get('tier') else None),
            hit=bool(args.get('hit')), detail=args.get('detail') or {},
            evidence=args.get('evidence') or {},
            reliable=args.get('reliable') is not False,
            title=str(args.get('title') or ''), body=args.get('body'))
        ctx['keys'].add(key)
        if r.get('ok') and not r.get('dry'):
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
        if args.get('sent_n') is not None:
            pass
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
