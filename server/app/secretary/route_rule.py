# -*- coding: utf-8 -*-
"""入口分流判据（阶段 5：从代码常量搬到页面）。

原来这三个词表写死在 `router.py` 里 —— 页面上存不进（`prompt_text` 的
`TEXT_KEYS` 白名单没有这俩键，保存被拒），这就是「代码读、页面存不进」的两处之一。

现在整份判据是 `cfg.route_rule` 的一行 JSON：product / make_verb / deliver_tail /
ask_hint / file_ref / priority。页面能看、能改、能看字数与修改人；
改完 60 秒内生效（_TTL），本进程改完立刻 invalidate。
"""
from __future__ import annotations

import json
import time

from psycopg.rows import dict_row

from app.secretary import pgdb

TTL = 60
_CACHE: dict = {'row': None, 'at': 0.0}
_READY = False

DEFAULTS = {
    'product': {
        'pptx': ['ppt', '幻灯片', '演示', '汇报材料', '课件'],
        'xlsx': ['excel', '表格', '台账', '统计表', '明细表', '汇总表', '清单表', '看板'],
        'docx': ['word', '文档', '报告', '通知', '公文', '请示', '函', '纪要', '方案',
                 '总结', '汇报', '材料', '模板', '初稿', '文书', '讲话稿', '简报'],
    },
    'make_verb': ['写', '做', '生成', '出一份', '出个', '出一版', '整理成', '整理个',
                  '整理一份', '转成', '起草', '拟一份', '汇总成', '排版', '套版', '做成', '弄成'],
    'deliver_tail': ['发我', '给我', '交上去', '报上去', '导出', '发过来', '传给我'],
    'ask_hint': ['多少', '几', '怎么样', '如何', '为什么', '排名', '占比', '对比',
                 '查一下', '看看', '分析', '情况'],
    'file_ref': ['这个文件', '这份文件', '这个文章', '这篇文章', '这份材料', '这篇材料',
                 '这个报告', '这份报告', '该文件', '该文章', '这份文档', '这个文档',
                 '上面那份', '刚才那份', '刚才那个', '刚才上传', '附件', '文件里',
                 '文章里', '材料里', '文中', '全文', '这份', '这篇', '这个表', '这份表'],
    'priority': ['用户点名', '说明书点名', '本人', '全局', '管理员默认', '最新版'],
}


def _q(sql, params=None):
    import psycopg
    with psycopg.connect(pgdb.DSN, autocommit=True, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall() if cur.description else []


def ensure() -> None:
    global _READY
    if _READY:
        return
    _q("""create table if not exists cfg.route_rule (
              k          varchar(40) primary key,
              body       jsonb not null,
              version_no integer not null default 1,
              updated_by varchar(64) not null default '',
              updated_at timestamptz not null default now())""")
    _q("insert into cfg.route_rule (k, body) values ('router', %s::jsonb) "
       "on conflict (k) do nothing", (json.dumps(DEFAULTS, ensure_ascii=False),))
    _READY = True


def invalidate() -> None:
    _CACHE['row'] = None
    _CACHE['at'] = 0.0


def rules() -> dict:
    now = time.time()
    hit = _CACHE['row']
    if hit is not None and now - _CACHE['at'] < TTL:
        return hit
    ensure()
    out = json.loads(json.dumps(DEFAULTS, ensure_ascii=False))
    try:
        rows = _q("select body from cfg.route_rule where k='router'")
        if rows and isinstance(rows[0]['body'], dict):
            out.update({k: v for k, v in rows[0]['body'].items() if v})
    except Exception as exc:                       # noqa: BLE001 - 读不到用代码默认
        print('[route_rule] 读判据失败（用代码默认）：%s: %s'
              % (type(exc).__name__, str(exc)[:100]), flush=True)
    _CACHE['row'] = out
    _CACHE['at'] = now
    return out


def save(body: dict, user='') -> dict:
    """整份合并保存。只认 DEFAULTS 里的键，避免页面存进代码不认识的字段。"""
    ensure()
    clean = {}
    for k, v in DEFAULTS.items():
        val = (body or {}).get(k)
        if val is None:
            continue
        if isinstance(v, dict) and isinstance(val, dict):
            clean[k] = {kk: [str(x) for x in vv] for kk, vv in val.items() if vv}
        elif isinstance(v, list) and isinstance(val, list):
            clean[k] = [str(x) for x in val if str(x).strip()]
    merged = rules()
    merged.update(clean)
    _q("""insert into cfg.route_rule (k, body, updated_by, updated_at)
          values ('router', %s::jsonb, %s, now())
          on conflict (k) do update set body=excluded.body,
                version_no=cfg.route_rule.version_no+1,
                updated_by=excluded.updated_by, updated_at=now()""",
       (json.dumps(merged, ensure_ascii=False), str(user or '')))
    invalidate()
    return {'ok': True, 'keys': sorted(clean),
            'terms': sum(len(v) for v in merged.values() if isinstance(v, list))}


def meta() -> dict:
    """三态徽标用：来源、版本、修改人、字数。"""
    ensure()
    rows = _q("select version_no, updated_by, updated_at from cfg.route_rule where k='router'")
    r = rows[0] if rows else {}
    body = rules()
    return {'k': 'router', 'source': '库 · cfg.route_rule', 'state': '可改',
            'version_no': r.get('version_no') or 1,
            'updated_by': r.get('updated_by') or '',
            'updated_at': str(r.get('updated_at') or ''),
            'chars': len(json.dumps(body, ensure_ascii=False)),
            'keys': {k: len(v) for k, v in body.items()}}
