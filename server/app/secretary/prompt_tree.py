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

import re

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
        'id': 'p2', 'no': 2, 'name': '要不要走预处理', 'desc': '走 = 纠错 + 补全（补问法 / 查补全表）；不走直接去干活',
        'materials': [
            {'key': 'base.intent', 'name': '① 要不要走预处理', 'editable': True},
        ],
    },
    {
        'id': 'p3', 'no': 3, 'name': '纠错', 'desc': '错别字 / 单位 / 站名改成库里的标准名',
        'materials': [
            # ★ 2026-11-03（Fred 现场要求）：纠错词典要能实时改 —— 点进去就是增删改查
            {'key': 'nc.typo', 'name': '② 纠错词典', 'editable': True},
        ],
    },
    {
        'id': 'p4', 'no': 4, 'name': '补问法 + 查补全表', 'desc': '补默认问法；拿原问题和补后问题去表里找命中（只查不改）',
        'materials': [
            {'key': 'mat.complete', 'name': '③ 补全表（时间地点也在这里配）', 'editable': True},
        ],
    },
    {
        'id': 'p5', 'no': 5, 'name': '问题确定', 'desc': '把前面几步揉成四行定稿：要什么 / 时间 / 范围 / 其它要求',
        'materials': [
            {'key': 'base.confirm', 'name': '④ 问题确定', 'editable': True},
        ],
    },
    {
        'id': 'p6', 'no': 6, 'name': '干活（主循环）', 'desc': '拿定稿自己决定查字段 / 查知识库 / 查数 / 写文档；信息不够就反问用户',
        'materials': [
            {'key': 'mat.fetch', 'name': '⑤ 查数口径', 'editable': True},
            {'key': 'semantic.tables', 'name': '数据表清单', 'editable': False},
            {'key': 'semantic.columns', 'name': '字段说明', 'editable': False},
            {'key': 'semantic.codes', 'name': '字典表', 'editable': False},
        ],
    },
    {
        'id': 'p7', 'no': 7, 'name': '回答', 'desc': '拿查回来的信息，按回答规则写成回答',
        'materials': [
            {'key': 'mat.answer', 'name': '⑥ 回答规则', 'editable': True},
        ],
    },
]

# ★ 2026-11-03（Fred）：第 5 步「算时间地点」整块删除 —— 代码不再换算月份/年份，也不补默认。
#   要补什么由 Fred 在「补全表」（第 4 步）里自己配，命中后交给第 5 步「问题确定」判断怎么用。



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
    'nc.typo': '预处理：代码读；页面可增删改，60 秒内生效',
    'mat.complete': '预处理：代码查表，不进提示词；命中只作参考',
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
    """节点树 + 每份材料的字数/时机/节数，直接给前端渲染。"""
    from app.secretary import prompt_material
    out = []
    for node in NODES:
        mats = []
        for m in node.get('materials') or []:
            body = material_body(m['key'])
            slot = m['key'].split('.', 1)[1] if m['key'].startswith('mat.') else ''
            cnt = prompt_material.counts(slot) if slot in prompt_material.SLOTS else {}
            mats.append({
                'key': m['key'],
                'name': m['name'],
                'editable': bool(m.get('editable')),
                'always': bool(m.get('always')),
                'when': WHEN.get(m['key'], ''),
                'textKey': TEXT_KEY.get(m['key'], ''),
                'chars': len(body or ''),
                'body': body or '',
                # 2026-11-05：页面要显示「N 节 / 必带 M 节 / 已删 K 节」
                'slot': slot,
                'sections': cnt.get('sections', 0),
                'pinned': cnt.get('pinned', 0),
                'deleted': cnt.get('deleted', 0),
            })
        item = dict(node)
        item['materials'] = mats
        out.append(item)
    return out


def _esc(body: str) -> str:
    """正文里以 # 开头的行前面加反斜杠 —— 否则导出后会被当成新的节/块（2026-11-05）。"""
    return '\n'.join(('\\' + ln) if ln.startswith('#') else ln
                     for ln in str(body or '').split('\n'))


def _cell(v) -> str:
    """表格单元格：换行和竖线要转义，否则一行被截断、数据丢失。"""
    return (str(v or '').replace('\\', '\\\\').replace('\n', '\\n').replace('|', '\\|'))


def _uncell(v: str) -> str:
    return (str(v or '').replace('\\n', '\n').replace('\\|', '|')
            .replace('\\\\', '\\'))


def export_markdown(slot: str = '', only_group: bool = False) -> str:
    """把提示词导成 markdown（2026-11-05）。

    slot 为空 = 全部材料 + 全部文本键；给 slot 就只导那一份。
    必带节标「（必带）」，按需取标「（按需取）」—— 导出去给人看时，一眼知道哪些会真进提示词。
    """
    from app.secretary import prompt_material, prompt_text
    slots = [slot] if slot in prompt_material.SLOTS else list(prompt_material.SLOTS)
    out = []
    for s in slots:
        rows = prompt_material.sections(s)
        if not rows:
            continue
        out.append('# %s' % prompt_material.SLOT_NAMES.get(s, s))
        out.append('')
        for r in rows:
            mark = '（必带）' if r.get('pinned') else '（按需取）'
            out.append('## %s %s' % (str(r.get('section') or '').strip(), mark))
            out.append('')
            out.append(_esc(str(r.get('body') or '').strip()))
            out.append('')
    if not only_group:
        for it in prompt_text.all_texts():
            body = str(it.get('body') or '')
            if not body.strip():
                continue
            out.append('# %s（%s）' % (it.get('name') or it.get('key'), it.get('key')))
            out.append('')
            out.append(_esc(body.strip()))
            out.append('')
        from app.secretary import complete_rules
        rows = complete_rules.rows()
        if rows:
            out.append('# 补全表')
            out.append('')
            out.append('| 类型 | 匹配 | 补成 | 优先级 |')
            out.append('|---|---|---|---|')
            for r in rows:
                kind = '归一' if str(r.get('kind')) == 'norm' else '补缺'
                out.append('| %s | %s | %s | %s |' % (
                    kind, _cell(r.get('match_text')), _cell(r.get('fill_text')),
                    r.get('priority') or 0))
            out.append('')
    return ('\n'.join(out).strip() + '\n') if out else ''


def import_markdown(text: str, user: str = 'import') -> dict:
    """把 markdown 导回库（2026-11-05）。格式与 `export_markdown` 完全对称：

        # 查数口径 / 判定标准 / 作答规则 / 自检清单   → 材料，`## 标题 （必带）」分节
        # 身份（identity）                        → 文本键，整块正文
        # 补全表                                  → markdown 表格，类型/匹配/补成/优先级

    只覆盖文件里出现的块，没出现的块一行不动。返回每块写了什么。
    """
    from app.secretary import complete_rules, prompt_material, prompt_text
    lines = str(text or '').replace('\r\n', '\n').split('\n')
    blocks = []
    cur = None
    for ln in lines:
        if ln.startswith('# '):
            cur = {'title': ln[2:].strip(), 'lines': []}
            blocks.append(cur)
        elif cur is not None:
            cur['lines'].append(ln)
    if not blocks:
        return {'error': '没找到以 "#" 开头的块，格式见导出的文件'}
    slot_by_name = {v: k for k, v in prompt_material.SLOT_NAMES.items()}
    res = {'materials': {}, 'texts': [], 'complete': 0, 'skipped': []}

    def _norm_title(t: str) -> str:
        """去掉开头的「⑤」「3.」这类序号，材料标题才对得上。"""
        return re.sub(r'^[①②③④⑤⑥⑦⑧⑨⑩\s\d.、]+', '', str(t or '')).strip()


    def _un(ln: str) -> str:
        return ln[1:] if ln.startswith('\\#') else ln

    for b in blocks:
        title, body_lines = b['title'], b['lines']
        key = ''
        if title.endswith('）') and '（' in title:
            key = title[title.rfind('（') + 1:-1].strip()
        if key and key in prompt_text.TEXT_KEYS:
            body = '\n'.join(_un(x) for x in body_lines).strip()
            if not body:
                res['skipped'].append(title + '（空）')
                continue
            got = prompt_text.save(key, body, user=user)
            if got.get('ok'):
                res['texts'].append(key)
            else:
                res['skipped'].append('%s：%s' % (title, got.get('error')))
            continue
        if title in slot_by_name or _norm_title(title) in slot_by_name:
            slot_code = slot_by_name.get(title) or slot_by_name[_norm_title(title)]
            rows, sec = [], None
            for ln in body_lines:
                if ln.startswith('## '):
                    head = ln[3:].strip()
                    pinned = '（按需取）' not in head
                    sec = {'section': head.replace('（必带）', '').replace('（按需取）', '').strip(),
                           'body': '', 'pinned': pinned}
                    rows.append(sec)
                elif sec is not None:
                    sec['body'] += _un(ln) + '\n'
            rows = [{'section': r['section'], 'body': r['body'].strip(),
                     'sort_no': i, 'pinned': r['pinned']} for i, r in enumerate(rows)]
            rows = [r for r in rows if r['body']]
            if not rows:
                res['skipped'].append(title + '（空）')
                continue
            # 同名的节原地更新（id 不变），不然每导一次就多出一堆软删行（2026-11-05）。
            have = {str(r.get('section') or '').strip(): r.get('id')
                    for r in prompt_material.sections(slot_code)}
            for r in rows:
                r['id'] = have.get(r['section'])
            got = prompt_material.replace_slot(slot_code, rows, user=user)
            if got.get('ok'):
                res['materials'][slot_code] = len(rows)
            else:
                res['skipped'].append('%s：%s' % (title, got.get('error')))
            continue
        if title.startswith('补全表'):
            items = []
            for ln in body_lines:
                s = ln.strip()
                if not s.startswith('|'):
                    continue
                cells = [c.strip() for c in
                         s.strip('|').replace('\\|', '\u0001').split('|')]
                cells = [c.replace('\u0001', '\\|') for c in cells]
                if len(cells) < 3 or '匹配' in cells[1]:
                    continue
                if set(cells[0]) <= set('-: '):
                    continue
                pri = 0
                try:
                    pri = int(cells[3]) if len(cells) > 3 else 0
                except ValueError:
                    pri = 0
                items.append({'kind': 'norm' if cells[0] in ('归一', 'norm') else 'fill',
                              'match_text': _uncell(cells[1]), 'fill_text': _uncell(cells[2]),
                              'priority': pri or (len(items) + 1) * 10})
            if not items:
                res['skipped'].append('补全表（空）')
                continue
            # 同一句话的条目原地更新，不每导一次就多出三条软删行（2026-11-05）。
            have = {str(r.get('match_text') or '').strip(): r.get('id')
                    for r in complete_rules.rows()}
            for it in items:
                it['id'] = have.get(it['match_text'].strip())
            got = complete_rules.save(items)
            if got.get('ok'):
                res['complete'] = len(items)
            else:
                res['skipped'].append('补全表：%s' % got.get('error'))
            continue
        res['skipped'].append(title + '（认不出是什么块）')
    return {'ok': True, **res}
