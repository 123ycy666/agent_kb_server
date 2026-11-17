# -*- coding: utf-8 -*-
"""问数线影响面：一次回答被哪 16 类输入影响，按决策链十段给出。

页面「提示词 → 问数线」直接渲染 `segments()`。
每项带 `endpoint`（点开看原文）与 `editor`（能就地改的写法：`material:fetch` / `text:intent`）。
这一层只读，不改配置。
"""
from __future__ import annotations

from psycopg.rows import dict_row

from app.secretary import pgdb


def _q(sql, params=None):
    import psycopg
    with psycopg.connect(pgdb.DSN, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall() if cur.description else []


def _one(sql, default=0):
    try:
        rows = _q(sql)
        return int(list(rows[0].values())[0] or 0) if rows else default
    except Exception:                              # noqa: BLE001 - 取不到按 0，页面标读不到
        return -1


def _text_chars(k):
    rows = _q("select length(coalesce(body,'')) as n from cfg.prompt_text where k=%s", (k,))
    return int(rows[0]['n']) if rows else 0


def _material_chars(*slots):
    try:
        rows = _q("select coalesce(sum(length(body)),0) as n, count(*) as c "
                  "from cfg.prompt_material where enabled and slot = any(%s)", (list(slots),))
        return int(rows[0]['n'] or 0), int(rows[0]['c'] or 0)
    except Exception:                              # noqa: BLE001
        return 0, 0


def _tool_count():
    try:
        from app.secretary import agent
        return len(agent.TOOLS)
    except Exception:                              # noqa: BLE001
        return 0


def segments() -> list[dict]:
    fetch_chars, fetch_secs = _material_chars('fetch', 'judge')
    answer_chars, answer_secs = _material_chars('answer')
    nc_total = sum(v for k, v in (
        ('t_nc_catalog', _one("select count(*) from cfg.t_nc_catalog")),
        ('t_nc_pinyin', _one("select count(*) from cfg.t_nc_pinyin")),
        ('t_nc_term', _one("select count(*) from cfg.t_nc_term")),
        ('t_nc_typo', _one("select count(*) from cfg.t_nc_typo")),
        ('t_nc_wordlist', _one("select count(*) from cfg.t_nc_wordlist")),
        ('t_nc_ambiguous', _one("select count(*) from cfg.t_nc_ambiguous")),
    ) if v > 0)
    route_rows = _one("select count(*) from cfg.route_rule")

    return [
        {'key': 'route', 'name': '① 入口分流', 'items': [
            {'name': '入口分流判据（五张词表）', 'where': 'cfg.route_rule',
             'size': '%d 组' % route_rows, 'editable': False, 'takes_effect': '下一轮',
             'endpoint': '/inject/route-rule', 'editor': '',
             'note': '判成问数 / 干活 / 闲聊；判错后面全错'},
        ]},
        {'key': 'fix', 'name': '② 纠错', 'items': [
            {'name': '六张纠错词典', 'where': 'cfg.t_nc_*', 'size': '共 %d 条' % nc_total,
             'editable': False, 'takes_effect': '60 秒', 'endpoint': '/prompts/lexicon',
             'editor': '', 'note': '名称纠错靠它；改词典 60 秒内重载'},
        ]},
        {'key': 'pre', 'name': '③ 预处理与补问', 'items': [
            {'name': '要不要走预处理', 'where': 'cfg.prompt_text.intent',
             'size': '%d 字' % _text_chars('intent'), 'editable': True, 'takes_effect': '下一轮',
             'endpoint': '/prompts/texts', 'editor': 'text:intent',
             'note': '第 2 步一次模型判断'},
            {'name': '补问法', 'where': 'cfg.prompt_text.askfill',
             'size': '%d 字' % _text_chars('askfill'), 'editable': True, 'takes_effect': '下一轮',
             'endpoint': '/prompts/texts', 'editor': 'askfill',
             'note': '缺时间/地点/对象时追问；可整段关掉'},
        ]},
        {'key': 'fill', 'name': '④ 补全表', 'items': [
            {'name': '补全表（时间地点也在这）', 'where': 'cfg.complete_rule',
             'size': '%d 条' % _one("select count(*) from cfg.complete_rule"),
             'editable': False, 'takes_effect': '下一轮', 'endpoint': '/prompts/complete',
             'editor': '', 'note': '预处理代码查表，不进提示词；命中只作参考'},
        ]},
        {'key': 'confirm', 'name': '⑤ 问题确定', 'items': [
            {'name': '问题确定规则', 'where': 'cfg.prompt_text.confirm',
             'size': '%d 字' % _text_chars('confirm'), 'editable': True, 'takes_effect': '下一轮',
             'endpoint': '/prompts/texts', 'editor': 'text:confirm',
             'note': '把口语问句归整成可查的问题'},
        ]},
        {'key': 'caliber_text', 'name': '⑥ 查数口径', 'items': [
            {'name': '表字段说明（fetch / judge）', 'where': 'cfg.prompt_material',
             'size': '%d 字 / %d 节' % (fetch_chars, fetch_secs), 'editable': True,
             'takes_effect': '下一轮', 'endpoint': '/prompts/material/fetch',
             'editor': 'material:fetch',
             'note': '模型写 SQL 唯一的表与字段来源'},
        ]},
        {'key': 'semantic', 'name': '⑦ 表与字段字典', 'items': [
            {'name': '业务字典（表名/字段中文名）', 'where': 'MySQL ai_table_metadata',
             'size': '只读·库', 'editable': False, 'takes_effect': '下一轮',
             'endpoint': '/prompts/meta', 'editor': '', 'note': '表名翻中文，翻错就报数字不报中文'},
            {'name': '字典覆盖层', 'where': 'cfg.data_meta_override',
             'size': '%d 条' % _one("select count(*) from cfg.data_meta_override"),
             'editable': False, 'takes_effect': '下一轮', 'endpoint': '/prompts/meta',
             'editor': '', 'note': '管理员改名用'},
        ]},
        {'key': 'contract', 'name': '⑧ 指标契约', 'items': [
            {'name': '指标口径（怎么算）', 'where': 'cfg.caliber_metric',
             'size': '%d 条' % _one("select count(*) from cfg.caliber_metric"),
             'editable': False, 'takes_effect': '下一轮', 'endpoint': '/caliber/list',
             'editor': '', 'note': '指标公式；metric_query 直接按它算'},
            {'name': '口径底表（必需过滤）', 'where': 'cfg.caliber_base',
             'size': '%d 条' % _one("select count(*) from cfg.caliber_base"),
             'editable': False, 'takes_effect': '下一轮', 'endpoint': '/caliber/list',
             'editor': '', 'note': '命中契约表时 SQL 必须带的过滤，缺了就拒'},
        ]},
        {'key': 'tool', 'name': '⑨ 工具与安全闸', 'items': [
            {'name': '工具定义与参数 schema', 'where': 'agent.TOOLS',
             'size': '%d 只' % _tool_count(), 'editable': False, 'takes_effect': '改代码',
             'endpoint': '/prompts/tools', 'editor': '',
             'note': '模型看到的那份；description 决定它怎么用。SQL 安全闸与数字核对也在代码里，'
                     '这页不列没有内容可看的东西'},
        ]},
        {'key': 'answer', 'name': '⑩ 作答', 'items': [
            {'name': '作答规则（answer）', 'where': 'cfg.prompt_material.answer',
             'size': '%d 字 / %d 节' % (answer_chars, answer_secs), 'editable': True,
             'takes_effect': '下一轮', 'endpoint': '/prompts/material/answer',
             'editor': 'material:answer', 'note': '出答案的格式与出处要求'},
            {'name': '文风与格式偏好', 'where': 'base.t_user_pref',
             'size': '%d 条' % _one("select count(*) from base.t_user_pref"),
             'editable': False, 'takes_effect': '下一轮', 'endpoint': '', 'editor': '',
             'note': '口吻、字号、字体'},
        ]},
    ]
