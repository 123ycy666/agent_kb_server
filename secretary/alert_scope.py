# -*- coding: utf-8 -*-
"""账号 → 角色 + 数据范围（预警鉴权的唯一入口）。

**为什么要单独一个文件、不用 run_sql**：
  run_sql 有业务字典白名单，是「取业务数」的闸；而这里是**鉴权数据**（这个人能看哪些所），
  且要拿「所 id 列表」。所以直连 tools_db._exec（同样是只读连接、同样只允许 SELECT）。

**失败一律关闸**：查不到 / 已停用 / 没角色 / 映射不到任何所 → ok=False。
**绝不默认全市** —— 这是这类接口最容易翻车的地方。

四条实测结论写在这里，改之前先看：
  1. 权限落在 `t_employee.department_id`，范围 = 该部门 + 全部子部门 → t_power_company。
  2. **只能按 department_id 对，不能按名字对**：部门表写「新城供电站」、档案写「新城供电所」。
  3. `department_id=1`（随州市供电所）→ 全市所有所。
  4. 例外：广水的 3 个县级账号（xgscwb003 等）部门挂在**关庙供电所**下，按部门只能看 1 个所；
     县级角色（cto/yxb/xgscwb）在部门映射不到所/只映射到本所时上溯父部门。
     反证：`gdzczy`（录入人员，挂两水所）不该走这个例外 —— 所以例外只对县级角色生效。
"""
import alert_rules
import tools_db

CITY_DEPT_ID = 1        # 随州市供电所：市级口径的锚点


def _rows(sql, args=None, limit=500):
    r = tools_db._exec(sql, args, limit_rows=limit)
    return [{(k or '').lower(): v for k, v in row.items()} for row in (r.get('rows') or [])]


def _dept(dept_id):
    rows = _rows('SELECT department_id, department_name, parent_id FROM t_department '
                 'WHERE department_id=%s', [dept_id], limit=1)
    return rows[0] if rows else {}


def _dept_tree_ids(dept_id):
    """该部门 + 其全部下级部门 id（两层树，做成循环以防以后变三层）。"""
    out = [int(dept_id)]
    rows = _rows('SELECT department_id, parent_id FROM t_department')
    changed = True
    while changed:
        changed = False
        for r in rows:
            d, p = r.get('department_id'), r.get('parent_id')
            if d is None or p is None:
                continue
            if int(p) in out and int(d) not in out:
                out.append(int(d))
                changed = True
    return out


def _stations_by_dept(dept_ids):
    if not dept_ids:
        return []
    marks = ','.join(['%s'] * len(dept_ids))
    return _rows('SELECT id, name, department_id FROM t_power_company '
                 'WHERE deleted_flag=0 AND department_id IN (' + marks + ') ORDER BY id',
                 list(dept_ids))


def _scope_of(dept_id, dept, stations):
    """范围名 + 类型。全市的锚点是「部门 = 市公司」，不是「所数够多」。"""
    if int(dept_id) == CITY_DEPT_ID:
        return {'scope': '全市', 'scope_type': 'city'}
    if len(stations) == 1:
        return {'scope': stations[0].get('name') or '', 'scope_type': 'station'}
    return {'scope': (dept.get('department_name') or ''), 'scope_type': 'county'}


def _is_org_level(dept_id, dept):
    """这个部门本身是不是「一级口径」——市公司（1）或县公司（父 = 1）。

    **例外规则必须在这里止步**，否则曾都/随县这些县公司账号会一路上溯到市公司，
    把 47 个所全算进自己范围（实测踩过：xgscwb 的 n 从 7 变成 47）。
    """
    if int(dept_id) == CITY_DEPT_ID:
        return True
    return dept.get('parent_id') is not None and int(dept.get('parent_id')) == CITY_DEPT_ID



def _emp(account):
    rows = _rows('SELECT employee_id, login_name, actual_name, department_id, '
                 'disabled_flag, deleted_flag, administrator_flag '
                 'FROM t_employee WHERE login_name=%s ORDER BY deleted_flag LIMIT 1',
                 [account], limit=1)
    return rows[0] if rows else {}


def _roles(employee_id):
    return _rows('SELECT r.role_code, r.role_name FROM t_role_employee re '
                 'JOIN t_role r ON r.role_id=re.role_id WHERE re.employee_id=%s',
                 [employee_id])


def _fail(account, error, name='', role_code=''):
    return {'ok': False, 'account': account, 'error': error, 'name': name,
            'role_code': role_code, 'station_ids': [], 'station_names': []}


def resolve(account):
    """解析登录账号 → {角色, 范围, 所 id 列表}。失败返回 ok=False + error。"""
    acct = (account or '').strip()
    if not acct:
        return _fail(acct, 'empty_account')

    e = _emp(acct)
    if not e:
        return _fail(acct, 'no_such_account')
    if int(e.get('deleted_flag') or 0) == 1 or int(e.get('disabled_flag') or 0) == 1:
        return _fail(acct, 'account_disabled', e.get('actual_name') or '')

    roles = _roles(e['employee_id'])
    codes = [str(r['role_code']) for r in roles if r.get('role_code')]
    if not codes:
        return _fail(acct, 'no_role', e.get('actual_name') or '')
    role_code = next((c for c in alert_rules.ROLE_PRIORITY if c in codes), codes[0])

    dept_id = int(e['department_id'])
    dept = _dept(dept_id)
    stations = _stations_by_dept(_dept_tree_ids(dept_id))

    # 例外：县级角色挂在**所级**部门 → 上溯父部门取真实范围（见文件头第 4 条）。
    # 已经在县公司/市公司层级的账号不走这一步（否则会一路上溯到全市）。
    if role_code in ('cto', 'yxb', 'xgscwb') and not _is_org_level(dept_id, dept):
        parent_id = dept.get('parent_id')
        up = None
        if parent_id is not None and int(parent_id) not in (0, dept_id):
            up = _stations_by_dept(_dept_tree_ids(parent_id))
        if up and len(up) > len(stations):
            dept = _dept(int(parent_id))
            dept_id = int(parent_id)
            stations = up

    if not stations:
        return _fail(acct, 'no_station', e.get('actual_name') or '', role_code)

    sc = _scope_of(dept_id, dept, stations)
    node = ''
    for n, c in alert_rules.LEDGER_ROLE_BY_NODE.items():
        if c == role_code:
            node = n
            break

    return {
        'ok': True, 'account': acct,
        'employee_id': int(e['employee_id']),
        'name': e.get('actual_name') or '',
        'role_code': role_code,
        'role_name': alert_rules.ROLE_NAMES.get(role_code, role_code),
        'dept_id': dept_id, 'dept_name': dept.get('department_name') or '',
        'parent_dept_id': dept.get('parent_id'),
        'scope': sc['scope'], 'scope_type': sc['scope_type'],
        'station_ids': [int(s['id']) for s in stations],
        'station_names': [s.get('name') or '' for s in stations],
        'ledger_role': role_code, 'ledger_node': node,
    }
