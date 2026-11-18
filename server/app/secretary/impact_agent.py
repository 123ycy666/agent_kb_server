# -*- coding: utf-8 -*-
"""干活线（智能体）影响面：一轮真正用到的东西，按执行顺序列，每项可看原文。

旧 `impact_office.py` 描述的是已下线的 loop 骨架，这一份对应 `server/app/agent/`。
"""
from __future__ import annotations

import json


def _text(text, size='', note=''):
    return {'kind': 'text', 'size': size, 'text': text, 'note': note}


def _table(columns, rows, size=''):
    return {'kind': 'table', 'size': size or '%d 行' % len(rows),
            'columns': columns, 'rows': rows}


# ── 探针 ──────────────────────────────────────────────────────────────

def _p_route_rule():
    from app.secretary import route_rule
    try:
        r = route_rule.rules()
    except Exception as exc:                       # noqa: BLE001
        return _text('读不到判据：%s' % exc)
    return _text(json.dumps(r, ensure_ascii=False, indent=2)[:12000], size='JSON')


def _p_route_code():
    import inspect
    from app.secretary import router
    src = inspect.getsource(router.route)
    return _text(src[:8000], size='%d 行' % len(src.splitlines()))


def _p_identity():
    from app.agent import runtime
    t = runtime.identity()
    return _text(t, size='%d 字' % len(t))


def _p_rules():
    from app.agent import runtime
    return _text('\n'.join('%d. %s' % (i, r) for i, r in enumerate(runtime.RULES, 1)),
                 size='%d 条' % len(runtime.RULES))


def _p_catalog(user_id):
    from app.agent import skills
    txt = skills.catalog(user_id, 200) or '（空）'
    return _text(txt, size='%d 行' % len([x for x in txt.splitlines() if x.strip()]))


def _p_specs():
    from app.agent import db
    rows = db.query('select name, version_no, status, product from kb.spec '
                    'order by name limit 100')
    return _table(['name', 'version_no', 'status', 'product'], rows)


def _p_skill_body():
    return _text('技能正文不常驻。每轮只把目录（名字 · 什么时候用 · 产物）放进指令；'
                 '模型要正文时调 read_skill(name)，由 spec.load 取 kb.spec.body。\n\n'
                 '《查数口径》《作答规则》两篇不在说明书表里，来自提示词页的 prompt_material，'
                 '和问数线共用同一份文本。', size='按需')


def _p_tools():
    from app.agent.tools import TOOLS
    lines = ['- %s：%s' % (t.name, (t.description or '').replace('\n', ' ')[:120])
             for t in TOOLS]
    return _text('\n'.join(lines), size='%d 只' % len(TOOLS))


def _p_tool_schema():
    from app.agent.tools import TOOLS
    out = []
    for t in TOOLS:
        try:
            schema = t.params_json_schema
        except Exception:                          # noqa: BLE001
            schema = {}
        out.append(json.dumps({'name': t.name, 'description': t.description,
                               'parameters': schema}, ensure_ascii=False, indent=2))
    return _text('\n\n'.join(out)[:30000], size='%d 只' % len(TOOLS))


def _p_sessions(user_id):
    from app.agent import db
    n = db.one('select count(*) n from qa.agent_session where user_id=%s', (user_id,))['n']
    rows = db.query('select id, title, updated_at from qa.agent_session where user_id=%s '
                    'order by updated_at desc limit 5', (user_id,))
    return _table(['id', 'title', 'updated_at'], rows, size='%d 个会话' % n)


def _p_compact():
    from app.agent import config
    return _text('上下文预算 %d token；到 %.0f%% 触发压缩；保留最近 %d 项。\n'
                 '超了先清旧工具结果，再把最老一段压成摘要（原文只标 archived，不删）。'
                 % (config.CONTEXT_BUDGET, config.COMPACT_AT * 100, config.KEEP_RECENT),
                 size='%d 项' % config.KEEP_RECENT)


def _p_memory(user_id):
    from app.agent import db, memory
    n = db.one('select count(*) n from qa.agent_memory where user_id=%s', (user_id,))['n']
    about = memory.about(user_id, 5)
    return _text('关于我（只放偏好，最多 5 行）：\n%s\n\n记忆总条数：%d'
                 % (about or '（空）', n), size='%d 条' % n)


def _p_runtime():
    from app.agent import config
    base, key, model = config.llm()
    return _text('模型：%s\n端点：%s\n步数上限：%d\n单次工具结果上限：%d 字\n技能目录上限：%d 行'
                 % (model, base, config.MAX_TURNS, config.TOOL_CAP, config.CATALOG_MAX),
                 size='')


def _p_artifacts(user_id):
    from pathlib import Path
    root = Path('data/agent_out')
    if not root.exists():
        return _text('还没有产物目录', size='0')
    dirs = sorted([p for p in root.iterdir() if p.is_dir()],
                  key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    lines = ['%s（%d 个文件）' % (p.name, len(list(p.rglob('*')))) for p in dirs]
    return _text('\n'.join(lines) or '空', size='%d 个会话目录' % len(dirs))


# ── 链路（顺序 = 一轮的实际顺序）──────────────────────────────────────

def _chain(user_id):
    return [
        {'key': 'route', 'name': '① 入口（判成干活）', 'items': [
            {'key': 'a_route_rule', 'name': '分流判据（cfg.route_rule）',
             'where': 'cfg.route_rule', 'editable': False, 'takes_effect': '60 秒',
             'note': '产物词表 + 动词 + 指代；判错整条线走错。', 'editor': '',
             'probe': _p_route_rule},
            {'key': 'a_route_code', 'name': '判成干活的三种情形',
             'where': 'server/app/secretary/router.py', 'editable': False,
             'takes_effect': '改代码',
             'note': '要一份文件 / 本轮带了文件 / 这句话在指会话里的文件。',
             'editor': '', 'probe': _p_route_code},
        ]},
        {'key': 'sys', 'name': '② 每轮指令', 'items': [
            {'key': 'a_identity', 'name': '身份',
             'where': '提示词页「身份」', 'editable': True, 'takes_effect': '60 秒',
             'note': '它是谁、服务谁；改这里不用改代码。', 'editor': 'inject:identity',
             'probe': _p_identity},
            {'key': 'a_rules', 'name': '作业规则（11 条）',
             'where': 'server/app/agent/runtime.py RULES', 'editable': False,
             'takes_effect': '改代码',
             'note': '工具优先、数字出处、先出文件再标待确认、别反复查。',
             'editor': '', 'probe': _p_rules},
        ]},
        {'key': 'skill', 'name': '③ 技能', 'items': [
            {'key': 'a_catalog', 'name': '技能目录',
             'where': 'kb.spec.menu_text + 两篇虚拟技能', 'editable': False,
             'takes_effect': '下一轮',
             'note': '一行一篇：名字 · 什么时候用 · 产物；模型按目录决定读哪篇。',
             'editor': '', 'probe': lambda: _p_catalog(user_id)},
            {'key': 'a_specs', 'name': '全部说明书',
             'where': 'kb.spec', 'editable': False, 'takes_effect': '下一轮',
             'note': '版本、状态、产物；上传发布走 /specs 页。', 'editor': '',
             'probe': _p_specs},
            {'key': 'a_skill_body', 'name': '正文怎么取',
             'where': 'read_skill → spec.load', 'editable': False,
             'takes_effect': '按需', 'note': '正文不常驻，用到才读。', 'editor': '',
             'probe': _p_skill_body},
        ]},
        {'key': 'tool', 'name': '④ 工具', 'items': [
            {'key': 'a_tools', 'name': '9 只工具',
             'where': 'server/app/agent/tools.py', 'editable': False,
             'takes_effect': '改代码', 'note': '一只一个动作，错误写怎么改。', 'editor': '',
             'probe': _p_tools},
            {'key': 'a_schema', 'name': '工具 schema 原文',
             'where': 'function_tool 生成', 'editable': False,
             'takes_effect': '改代码', 'note': '参数、类型、必填。', 'editor': '',
             'probe': _p_tool_schema},
        ]},
        {'key': 'mem', 'name': '⑤ 会话与记忆', 'items': [
            {'key': 'a_sessions', 'name': '会话',
             'where': 'qa.agent_session / qa.agent_item', 'editable': False,
             'takes_effect': '每轮',
             'note': '用户、助手、工具调用、工具结果、摘要都存；可续跑。', 'editor': '',
             'probe': lambda: _p_sessions(user_id)},
            {'key': 'a_compact', 'name': '压缩',
             'where': '/runtime 可改', 'editable': False, 'takes_effect': '5 秒',
             'note': '预算、阈值、保留项；超了先清旧工具结果再压摘要。', 'editor': '',
             'probe': _p_compact},
            {'key': 'a_memory', 'name': '记忆',
             'where': 'qa.agent_memory', 'editable': False, 'takes_effect': '每轮',
             'note': '「关于我」只放偏好 top5；其它靠 recall 按需取。', 'editor': '',
             'probe': lambda: _p_memory(user_id)},
        ]},
        {'key': 'run', 'name': '⑥ 运行与产物', 'items': [
            {'key': 'a_runtime', 'name': '运行参数',
             'where': '/runtime 页', 'editable': False, 'takes_effect': '5 秒',
             'note': '模型、端点、步数、结果上限、目录上限。', 'editor': '',
             'probe': _p_runtime},
            {'key': 'a_artifacts', 'name': '产物目录',
             'where': 'data/agent_out', 'editable': False, 'takes_effect': '每轮',
             'note': '按会话分目录；正式文件登记在 doc_files。', 'editor': '',
             'probe': lambda: _p_artifacts(user_id)},
        ]},
    ]


def _index(user_id):
    return {it['key']: (seg, it) for seg in _chain(user_id) for it in seg['items']}


def _run(it):
    try:
        return it['probe']()
    except Exception as exc:                       # noqa: BLE001
        return {'kind': 'text', 'size': '读不到',
                'text': '（现在没有内容）\n\n读取失败：%s: %s'
                        % (type(exc).__name__, str(exc)[:300])}


def _meta(seg, it, p):
    return {'key': it['key'], 'seg': seg['name'], 'name': it['name'],
            'where': it['where'], 'size': p.get('size') or '',
            'editable': bool(it['editable']), 'takes_effect': it['takes_effect'],
            'note': it['note'], 'editor': it['editor'], 'kind': p.get('kind') or 'text'}


def segments(user_id=0):
    out = []
    for seg in _chain(int(user_id or 0)):
        out.append({'key': seg['key'], 'name': seg['name'],
                    'items': [_meta(seg, it, _run(it)) for it in seg['items']]})
    return out


def body(key, user_id=0):
    idx = _index(int(user_id or 0)).get(key)
    if not idx:
        return {'error': '未知的项：%s' % key}
    seg, it = idx
    p = _run(it)
    out = dict(_meta(seg, it, p))
    out.update({'kind': p.get('kind') or 'text', 'text': p.get('text') or '',
                'columns': p.get('columns') or [], 'rows': p.get('rows') or [],
                'hint': p.get('note') or ''})
    return out
