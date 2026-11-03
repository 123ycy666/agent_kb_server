# -*- coding: utf-8 -*-
"""主流程图的静态结构 + 每个节点挂的材料（2026-11-01）。

页面的树、节点、材料按钮全部由这里生成，**不在前端写死**——改结构只改这一处。

材料三种来源，页面上靠 `editable` / `source` 区分：
    code:*    代码常量（只读，最多 S0/S1 与身份这类）
    table:prompt_material  五份材料（四份可改）
    table:complete_rule    补全表（可改）
    词表/词典  只读展示
"""
from __future__ import annotations

NODES = [
    {
        'id': 'p1', 'no': 1, 'name': '接手这句话', 'desc': '用户原话 + 最近几轮问答',
        'materials': [
            {'key': 'base.identity', 'name': '身份', 'editable': True},
            {'key': 'base.redlines', 'name': '红线', 'editable': True},
            {'key': 'base.priority', 'name': '优先级', 'editable': True},
        ],
    },
    {
        'id': 'p2', 'no': 2, 'name': '要不要走预处理', 'desc': '走 = 纠错 + 补全 + 补时间地点；不走直接去干活',
        'materials': [
            {'key': 'base.intent', 'name': '① 要不要走预处理', 'editable': True},
        ],
    },
    {
        'id': 'p3', 'no': 3, 'name': '纠错', 'desc': '错别字 / 单位 / 站名改成库里的标准名',
        'materials': [
            {'key': 'nc.typo', 'name': '② 纠错词典', 'editable': False},
        ],
    },
    {
        'id': 'p4', 'no': 4, 'name': '补问法 + 查补全表', 'desc': '补默认问法；拿原问题和补后问题去表里找命中（只查不改）',
        'materials': [
            {'key': 'mat.complete', 'name': '④ 补全表', 'editable': True},
        ],
    },
    {
        'id': 'p5', 'no': 5, 'name': '算时间地点', 'desc': '上个月 → 2026年10月；没说就默认上一个月 / 全市',
        'materials': [
            {'key': 'time.scope', 'name': '⑤ 时间地点规则', 'editable': False},
        ],
    },
    {
        'id': 'p6', 'no': 6, 'name': '问题确定', 'desc': '把前面几步揉成四行定稿：要什么 / 时间 / 范围 / 其它要求',
        'materials': [
            {'key': 'base.confirm', 'name': '⑥ 问题确定', 'editable': True},
        ],
    },
    {
        'id': 'p7', 'no': 7, 'name': '干活（主循环）', 'desc': '拿定稿自己决定查字段 / 查知识库 / 查数 / 写文档；信息不够就反问用户',
        'materials': [
            {'key': 'mat.fetch', 'name': '⑦ 查数口径', 'editable': True},
            {'key': 'semantic.tables', 'name': '数据表清单', 'editable': False},
            {'key': 'semantic.columns', 'name': '字段说明', 'editable': False},
            {'key': 'semantic.codes', 'name': '字典表', 'editable': False},
        ],
    },
    {
        'id': 'p8', 'no': 8, 'name': '回答', 'desc': '拿查回来的信息，按回答规则写成回答',
        'materials': [
            {'key': 'mat.answer', 'name': '⑧ 回答规则', 'editable': True},
        ],
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
        from app.secretary import prompt_text
        return prompt_text.get('redlines', '')
    if key == 'base.priority':
        from app.secretary import prompt_text
        return prompt_text.get('priority', '')
    if key == 'base.intent':
        from app.secretary import prompt_text
        return prompt_text.get('intent', '')
    if key == 'base.confirm':
        from app.secretary import prompt_text
        return prompt_text.get('confirm', '')
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
        return ('【时间地点补全规则】（代码实现，只读展示；改说法在页面的时间地点规则里）\n'
                '· 相对时间词换成具体期间：上个月 / 今年 / 9 月 → 具体年月\n'
                '· 本轮没提时间 → **默认上一个月**（2026-11-03 改；不再沿用上一轮，\n'
                '  是否沿用由第 6 步「问题确定」判断）\n'
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
    if key == 'mat.complete':
        from app.secretary import complete_rules
        return complete_rules.as_text(), True
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
    'base.redlines': '填了才注入（每条消息）',
    'base.priority': '填了才注入（每条消息）',
    'base.intent': '第 1 步：每次调用一次模型判断',
    'base.confirm': '第 6 步：每次调用一次模型归整',
    'modes.text': '每条消息都在（基础流程那段）',
    'mat.fetch': '第 7 步干活循环里',
    'mat.answer': '第 8 步写回答时',
    'semantic.tables': '干活循环里（工具目录）',
    'semantic.columns': '不进提示词：describe_table 按需取',
    'semantic.codes': '干活循环里（业务码表）',
    'nc.typo': '预处理：代码读，不进提示词',
    'mat.complete': '预处理：代码读，不进提示词',
    'time.scope': '预处理：代码算，不进提示词',
    'modes.keywords': '已不参与链路（词表匹配已删）',
}


# 这几份正文存在 prompt_text 表里（页面可改，没有覆盖就用代码常量）
TEXT_KEY = {
    'base.identity': 'identity',
    'base.redlines': 'redlines',
    'base.priority': 'priority',
    'base.intent': 'intent',
    'base.confirm': 'confirm',
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
