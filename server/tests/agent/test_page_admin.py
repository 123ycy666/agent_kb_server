# -*- coding: utf-8 -*-
"""页面可改的词典 / 时间地点 / 元数据覆盖（2026-11-02）。

跑法：`py -X utf8 server/tests/agent/test_page_admin.py`
**不需要外部数据库**：用 `pgserver` 起一个临时 PG，建与线上一致的表。

覆盖的角度
----------
1. 纠错词典四张表：查（分页+搜索）/ 增 / 改 / 软删，`deleted_flag` 与 `update_time` 都要动
   （name_fix 靠 `MAX(update_time)` 探版本，不动这个字段 = 改了不生效）。
2. 列白名单：传 `id` / `deleted_flag` 之类的列要被忽略，不能顺手改掉。
3. 必填校验：`wrong` 空拒绝、`term` 空拒绝。
4. 标准词表新增时拼音码/首字母按字表自动补。
5. 时间地点规则：页面写入后访问器立刻用新值；坏 JSON 拒绝；reset 后回退代码常量。
6. 数据表元数据覆盖：写覆盖 → apply 后生效；值清空 → 回退业务库原值。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

import pgserver                                          # noqa: E402
import psycopg                                           # noqa: E402

_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = '') -> None:
    _checks.append((label, bool(cond), str(extra)[:220]))


DDL = """
CREATE TABLE IF NOT EXISTS t_nc_typo (
  id bigserial PRIMARY KEY, org varchar(64) NOT NULL DEFAULT '',
  wrong varchar(64) NOT NULL, right_word varchar(64) NOT NULL,
  freq_level varchar(32) NOT NULL DEFAULT '', reason varchar(64) NOT NULL DEFAULT '',
  sample varchar(200) NOT NULL DEFAULT '', deleted_flag smallint NOT NULL DEFAULT 0,
  create_time timestamp NOT NULL DEFAULT now(), update_time timestamp NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS t_nc_term (
  id bigserial PRIMARY KEY, term varchar(64) NOT NULL,
  definition varchar(400) NOT NULL DEFAULT '', alias varchar(200) NOT NULL DEFAULT '',
  org varchar(64) NOT NULL DEFAULT '', deleted_flag smallint NOT NULL DEFAULT 0,
  create_time timestamp NOT NULL DEFAULT now(), update_time timestamp NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS t_nc_ambiguous (
  id bigserial PRIMARY KEY, py_code varchar(64) NOT NULL DEFAULT '',
  name_a varchar(64) NOT NULL, kind_a varchar(32) NOT NULL DEFAULT '',
  name_b varchar(64) NOT NULL, kind_b varchar(32) NOT NULL DEFAULT '',
  action varchar(64) NOT NULL DEFAULT '', deleted_flag smallint NOT NULL DEFAULT 0,
  create_time timestamp NOT NULL DEFAULT now(), update_time timestamp NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS t_nc_wordlist (
  id bigserial PRIMARY KEY, word varchar(64) NOT NULL,
  domain varchar(32) NOT NULL DEFAULT '', src varchar(64) NOT NULL DEFAULT '',
  py_full varchar(100) NOT NULL DEFAULT '', initials varchar(40) NOT NULL DEFAULT '',
  deleted_flag smallint NOT NULL DEFAULT 0,
  create_time timestamp NOT NULL DEFAULT now(), update_time timestamp NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS t_nc_pinyin (
  id bigserial PRIMARY KEY, hanzi varchar(4) NOT NULL,
  initial varchar(4) NOT NULL DEFAULT '', final varchar(8) NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS prompt_text (
  k varchar(64) PRIMARY KEY, body text NOT NULL, version_no int NOT NULL DEFAULT 1,
  updated_by varchar(64) NOT NULL DEFAULT '', updated_at timestamptz NOT NULL DEFAULT now());
"""


def main() -> int:
    print('页面可改（词典 / 时间地点 / 元数据）验收')
    try:
        db = pgserver.get_server(Path(tempfile.gettempdir()) / 'pgsrv_page_admin')
        uri = db.get_uri()
    except Exception as exc:                              # noqa: BLE001
        print('  [SKIP] 起不了内嵌 PG：%s' % str(exc)[:120])
        return 0
    conn = psycopg.connect(uri, autocommit=True)
    conn.execute(DDL)
    conn.close()
    os.environ['PG_DSN'] = uri
    print('  PG = %s' % uri.rsplit('@', 1)[-1])

    from app.secretary import lex_admin, meta_admin, prompt_text

    meta_admin.ensure()                                   # 覆盖表由它自己建（幂等）

    def q(sql, params=()):
        c = psycopg.connect(uri, autocommit=True)
        try:
            cur = c.execute(sql, params)
            return [dict(zip([d.name for d in cur.description], r)) for r in cur.fetchall()]
        finally:
            c.close()

    def reset():
        c = psycopg.connect(uri, autocommit=True)
        c.execute("TRUNCATE t_nc_typo, t_nc_term, t_nc_ambiguous, t_nc_wordlist, "
                  "t_nc_pinyin, prompt_text, data_meta_override RESTART IDENTITY")
        c.close()
        prompt_text.clear_cache()
        meta_admin.clear_cache()

    # ── 1 纠错词典：增 / 查 / 改 / 删
    reset()
    r = lex_admin.create('typo', {'wrong': '两水', 'right_word': '两水供电所'})
    chk('新增一条错别字', r.get('ok') and r.get('id') == 1, r)
    r = lex_admin.rows('typo')
    chk('查得到这一条', r.get('total') == 1 and r['rows'][0]['right_word'] == '两水供电所', r.get('rows'))
    chk('查询返回列定义', [c['key'] for c in r['cols']][:2] == ['wrong', 'right_word'],
        r['cols'][:2])
    r = lex_admin.rows('typo', q='两水')
    chk('模糊搜索命中', r.get('total') == 1, r.get('total'))
    r = lex_admin.rows('typo', q='不存在的东西')
    chk('搜索不到时是 0 条', r.get('total') == 0, r.get('total'))

    old = q("select update_time from t_nc_typo where id=1")[0]['update_time']
    r = lex_admin.update('typo', 1, {'wrong': '凉水', 'right_word': '两水供电所'})
    chk('改一条', r.get('ok'), r)
    row = q("select wrong, right_word, update_time from t_nc_typo where id=1")[0]
    chk('改后内容正确', row['wrong'] == '凉水')
    chk('改后 update_time 变了（不然 name_fix 探测不到）', row['update_time'] > old, row['update_time'])

    r = lex_admin.delete('typo', 1)
    chk('删一条走软删', r.get('ok') and r.get('deleted'))
    row = q("select deleted_flag, update_time from t_nc_typo where id=1")[0]
    chk('软删后 deleted_flag=1', row['deleted_flag'] == 1, row)
    chk('软删也动了 update_time', row['update_time'] > old)
    chk('软删后列表里看不到', lex_admin.rows('typo').get('total') == 0)
    chk('再删一次报错', 'error' in lex_admin.delete('typo', 1))

    # ── 2 列白名单 / 必填
    reset()
    r = lex_admin.create('typo', {'wrong': '甲', 'right_word': '乙',
                                  'id': 999, 'deleted_flag': 1, 'create_time': '1999-01-01'})
    chk('非白名单列被忽略（新增成功）', r.get('ok'), r)
    row = q('select id, deleted_flag from t_nc_typo')[0]
    chk('id 没用传进来的 999', row['id'] == 1, row['id'])
    chk('deleted_flag 没被改成 1', row['deleted_flag'] == 0, row['deleted_flag'])
    chk('必填为空要拒绝（错别字）', 'error' in lex_admin.create('typo', {'wrong': '', 'right_word': 'x'}))
    chk('必填为空要拒绝（术语）', 'error' in lex_admin.create('term', {'term': '  '}))
    chk('未知词表要拒绝', 'error' in lex_admin.create('nope', {}))

    # ── 3 标准词表拼音自动补
    reset()
    c = psycopg.connect(uri, autocommit=True)
    c.execute("INSERT INTO t_nc_pinyin (hanzi, initial, final) VALUES "
              "('业','y','e'),('扩','k','uo'),('报','b','ao'),('装','z','uan')")
    c.close()
    r = lex_admin.create('wordlist', {'word': '业扩报装'})
    row = q("select py_full, initials from t_nc_wordlist where word='业扩报装'")[0]
    chk('首字母自动补（声母串，与库里格式一致）', row['initials'] == 'ykbz', row['initials'])
    chk('拼音码不瞎补（库里是离线编译的混合码，格式不可靠）',
        row['py_full'] == '', repr(row['py_full']))
    r = lex_admin.create('wordlist', {'word': '报装', 'py_full': '自定义', 'initials': 'zd'})
    row = q("select py_full, initials from t_nc_wordlist where word='报装'")[0]
    chk('手填的拼音不被覆盖', row['py_full'] == '自定义' and row['initials'] == 'zd', row)

    # ── 4 概览
    reset()
    lex_admin.create('typo', {'wrong': 'a', 'right_word': 'b'})
    ov = {x['key']: x for x in lex_admin.overview()}
    chk('概览四张表都在', sorted(ov) == ['ambiguous', 'term', 'typo', 'wordlist'], sorted(ov))
    chk('概览行数正确', ov['typo']['rows'] == 1, ov['typo'])

    # ── 5 时间地点规则：**已整体删除**（2026-11-03），页面与 prompt_text 里不再有 time_* 键
    chk('time_* 键已删除', not any(k.startswith('time_') for k in prompt_text.TEXT_KEYS),
        sorted(prompt_text.TEXT_KEYS))

    # ── 6 数据表元数据覆盖层
    reset()
    payload = {
        'tables': [{'table_name': 't_power_work_order', 'table_cn_name': '工单表',
                    'business_desc': '业务库原描述', 'level': 1}],
        'cols': {'t_power_work_order': {
            'budget_amount': {'column_name': 'budget_amount', 'column_cn_name': '',
                              'business_desc': ''}}},
    }
    r = meta_admin.save('table', 't_power_work_order', 'cn_name', '工单主表（页面改的）')
    chk('保存表级覆盖', r.get('ok'), r)
    meta_admin.clear_cache()
    meta_admin.apply(payload)
    chk('表级覆盖生效', payload['tables'][0]['table_cn_name'] == '工单主表（页面改的）',
        payload['tables'][0])
    chk('没覆盖的字段仍用业务库原值',
        payload['tables'][0]['business_desc'] == '业务库原描述', payload['tables'][0])

    r = meta_admin.save('column', 't_power_work_order', 'cn_name', '预算金额',
                        column_name='budget_amount')
    chk('保存字段级覆盖', r.get('ok'), r)
    meta_admin.clear_cache()
    meta_admin.apply(payload)
    chk('字段级覆盖生效',
        payload['cols']['t_power_work_order']['budget_amount']['column_cn_name'] == '预算金额',
        payload['cols']['t_power_work_order']['budget_amount'])

    chk('kind 校验', 'error' in meta_admin.save('nope', 't', 'cn_name', 'x'))
    chk('field 校验', 'error' in meta_admin.save('table', 't', 'nope', 'x'))
    chk('字段级不能改 level',
        'error' in meta_admin.save('column', 't', 'level', 'x', column_name='c'))

    r = meta_admin.save('table', 't_power_work_order', 'cn_name', '')
    chk('值清空 = 取消覆盖', r.get('ok') and r.get('cleared'), r)
    meta_admin.clear_cache()
    payload2 = {'tables': [{'table_name': 't_power_work_order', 'table_cn_name': '工单表',
                            'business_desc': '业务库原描述', 'level': 1}], 'cols': {}}
    meta_admin.apply(payload2)
    chk('取消后回退业务库原值', payload2['tables'][0]['table_cn_name'] == '工单表',
        payload2['tables'][0])

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(('  [OK]   ' if ok else '  [FAIL] ') + label
              + (('   ← ' + extra) if (not ok and extra) else ''))
    print('\n%d 项检查，%d 项失败' % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
