# -*- coding: utf-8 -*-
"""主流程图的静态结构 + 每个节点挂的材料（2026-11-01）。

页面的树、节点、材料按钮全部由这里生成，**不在前端写死**——改结构只改这一处。

材料三种来源，页面上靠 `editable` / `source` 区分：
    code:*    代码常量（只读：红线 / 优先级 / 身份 / 时间地点规则）
    table:prompt_material  四份材料（★ 2026-11-03 主分支空壳化后整体关闭，见 prompt_material.SLOTS）
    table:complete_rule    补全表（★ 2026-11-03 已删除）
    词表/词典  只读展示（业务词表仍可页面覆盖）

★ 2026-11-03（Fred）：链路里删掉的 6 份材料 —— 补全表、查数口径、判定标准、
  作答规则、汇报口吻、自检清单。原文备份在
  `docs/08-历史归档/2026-11-03-删除的提示词规则/`，恢复方式见那份 README。
"""
from __future__ import annotations

NODES = [
    {
        'id': 'p1', 'no': 1, 'name': '提问', 'desc': '用户原话',
        'materials': [
            {'key': 'base.identity', 'name': '身份', 'editable': True},
            {'key': 'base.redlines', 'name': '红线', 'editable': False, 'always': True},
            {'key': 'base.priority', 'name': '优先级', 'editable': False, 'always': True},
        ],
    },
    {
        'id': 'p2', 'no': 2, 'name': '要不要动这句话', 'desc': '是不是业务问题',
        'materials': [
            {'key': 'modes.keywords', 'name': '业务词表', 'editable': True},
        ],
    },
    {
        'id': 'p3', 'no': 3, 'name': '改错字', 'desc': '打错的名改成库里的标准名',
        'materials': [
            {'key': 'nc.typo', 'name': '纠错词典', 'editable': False},
        ],
    },
    {
        'id': 'p4', 'no': 4, 'name': '补全', 'desc': '只补缺的问法（补全表 2026-11-03 已删）',
        'materials': [],
    },
    {
        'id': 'p5', 'no': 5, 'name': '定时间地点', 'desc': '上个月 → 2026年10月',
        'materials': [
            {'key': 'time.scope', 'name': '时间地点规则', 'editable': False},
        ],
    },
    {
        'id': 'p6', 'no': 6, 'name': '判题型', 'desc': '查数 / 分析 / 写作 / 出文件',
        'materials': [
            {'key': 'modes.keywords', 'name': '题型词表', 'editable': True},
            {'key': 'modes.text', 'name': '模式做法', 'editable': True},
        ],
        'branches': ['B 查数', 'C 分析', 'D 写材料', 'E 出文件', 'A 写作（不查库）'],
    },
    {
        'id': 'p7', 'no': 7, 'name': '去查', 'desc': '要数→业务库 / 不知字段→字典表 / 问制度→资料库',
        'materials': [
            {'key': 'semantic.tables', 'name': '数据表清单', 'editable': False},
            {'key': 'semantic.columns', 'name': '字段说明', 'editable': False},
            {'key': 'semantic.codes', 'name': '字典表', 'editable': False},
        ],
    },
    {
        'id': 'p8', 'no': 8, 'name': '数够了吗', 'desc': '口径对齐、能下结论；不够就回去再查',
        'materials': [],
    },
    {
        'id': 'p9', 'no': 9, 'name': '要不要判', 'desc': '查数不判；分析要判（判定标准 2026-11-03 已删）',
        'materials': [],
    },
    {
        'id': 'p10', 'no': 10, 'name': '作答', 'desc': '先结论，再支撑数（作答规则/汇报口吻 2026-11-03 已删）',
        'materials': [],
    },
    {
        'id': 'p11', 'no': 11, 'name': '自检', 'desc': '交卷前过一遍（自检清单 2026-11-03 已删）',
        'materials': [],
    },
    {
        'id': 'p12', 'no': 12, 'name': '交付', 'desc': '回答给员工，同时留档',
        'materials': [],
    },
]


def _code_text(key: str) -> str:
    if key == 'semantic.columns':
        from app.secretary import semantic
        try:
            return ('【字段说明】（每张表有哪些字段、中文名、说明、示例值）\n'
                    '这份**不进提示词**：模型用 describe_table(表名) 按需取一张表的字段。\n\n'
                    + semantic.table_menu())
        except Exception as exc:                   # noqa: BLE001
            return '【字段说明】读取失败：%s' % str(exc)[:120]
    if key == 'semantic.tables':
        from app.secretary import semantic
        try:
            return ('【可用数据表（只能查这些，字典外的表一律不可用）】\n'
                    '字段明细请用 describe_table(表名) 现取，不要凭印象写字段。\n'
                    + semantic.table_list_menu())
        except Exception as exc:                   # noqa: BLE001
            return '【数据表清单】读取失败：%s' % str(exc)[:120]
    if key == 'semantic.codes':
        from app.secretary import semantic
        try:
            return semantic.codes_menu() or '【业务码表】字典为空'
        except Exception as exc:                   # noqa: BLE001
            return '【业务码表】读取失败：%s' % str(exc)[:120]
    if key == 'base.identity':
        from app.secretary import prompt_text, prompts_data
        return prompt_text.get('identity', prompts_data.IDENTITY)
    if key == 'base.redlines':
        from app.secretary import prompt_stack
        return prompt_stack.L0_REDLINES
    if key == 'base.priority':
        from app.secretary import agent
        return agent.RULES_PRIORITY
    if key == 'modes.text':
        import json as _json
        from app.secretary import prompt_text, prompts_data
        merged = prompt_text.mode_texts(prompts_data.MODE_TEXTS)
        return _json.dumps(merged, ensure_ascii=False, indent=2)
    if key == 'modes.keywords':
        import json as _json
        from app.secretary import modes
        return _json.dumps(modes.load_rules(), ensure_ascii=False, indent=2)
    if key == 'time.scope':
        return ('【时间地点补全规则】（代码实现，只读展示）\n'
                '· 相对时间词换成具体期间：上个月 / 今年 / 9 月 → 具体年月\n'
                '· 本轮没提时间 → 沿用上一轮；上一轮也没有 → 取**上一个月**（11月问 → 补10月）\n'
                '· 地点：从问题里的所 / 县 / 台区名取；没有 → 全市\n'
                '· 表在不同表里写法不同（「广水市供电公司」vs「广水」）→ 统一按名称清单换')
    return ''


def _typo_text() -> str:
    """纠错词典概览（只读）：总条数 + 前十行样例。"""
    try:
        from app.secretary import pgdb
        conn = pgdb.connect(autocommit=True)
        try:
            cur = conn.cursor()
            try:
                cur.execute('SELECT count(*) AS n FROM t_nc_typo WHERE deleted_flag = 0')
                row = cur.fetchone()
                total = int((row or {}).get('n') or 0)
                cur.execute('SELECT wrong, right_word, reason FROM t_nc_typo '
                            'WHERE deleted_flag = 0 ORDER BY id LIMIT 10')
                rows = cur.fetchall() or []
            finally:
                cur.close()
        finally:
            try:
                conn.close()
            except Exception:                      # noqa: BLE001
                pass
        lines = ['【纠错词典】（只读，维护在词典页）', '共 %d 条。前 10 条样例：' % total]
        for r in rows:
            lines.append('· %s → %s（%s）' % (r.get('wrong'), r.get('right_word'),
                                             r.get('reason') or ''))
        return '\n'.join(lines)
    except Exception as exc:                       # noqa: BLE001
        return '【纠错词典】读取失败（不影响问答）：%s' % str(exc)[:120]


def _table_text(key: str) -> tuple[str, bool]:
    """返回 (正文, 是否存在)。"""
    if key.startswith('mat.'):
        from app.secretary import prompt_material
        slot = key.split('.', 1)[1]
        return prompt_material.text(slot), slot in prompt_material.SLOTS
    return '', False


def material_body(key: str) -> str:
    """一份材料的正文（页面右侧展示用）。"""
    body, ok = _table_text(key)
    if ok:
        return body
    if key == 'nc.typo':
        return _typo_text()
    return _code_text(key)


# 每份材料「什么时候进提示词」——页面直接显示，不用猜
WHEN = {
    'base.identity': '每条消息都在',
    'base.redlines': '每条消息都在',
    'base.priority': '每条消息都在',
    'modes.text': '判完题型后',
    'semantic.tables': '查数时（data / doc 模式）',
    'semantic.columns': '不进提示词：describe_table 按需取',
    'semantic.codes': '查数时（data / doc 模式）',
    'nc.typo': '预处理：代码读，不进提示词',
    'time.scope': '预处理：代码读，不进提示词',
    'modes.keywords': '判题型：代码读，不进提示词',
}


# 这几份正文存在 prompt_text 表里（页面可改，没有覆盖就用代码常量）
TEXT_KEY = {
    'base.identity': 'identity',
    'modes.text': 'modes',
    'modes.keywords': 'keywords',
}


def tree() -> list[dict]:
    """节点树 + 每份材料的字数/时机，直接给前端渲染。"""
    out = []
    for node in NODES:
        mats = []
        for m in node.get('materials') or []:
            body = material_body(m['key'])
            mats.append({
                'key': m['key'],
                'name': m['name'],
                'editable': bool(m.get('editable')),
                'always': bool(m.get('always')),
                'when': WHEN.get(m['key'], ''),
                'textKey': TEXT_KEY.get(m['key'], ''),
                'chars': len(body or ''),
                'body': body or '',
            })
        item = dict(node)
        item['materials'] = mats
        out.append(item)
    return out
