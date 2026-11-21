# -*- coding: utf-8 -*-
"""纠错词典批量导入（页面导入按钮用的后端）。

跑法：`py -X utf8 server/tests/agent/test_lexicon_import.py`
**不需要外部数据库**：用 `pgserver` 起一个临时 PG，建与线上一致的六张词典表 + 词表。

覆盖的角度
----------
1. 模板 / 导出：表头是中文、导出的表头能直接再导回来。
2. 导入：CSV（中文表头 / 库列名都认）、XLSX、表内去重、追加跳过、替换先软删。
3. 拼音码：名称库导入时按引擎同一套规则现算 py_full / py_strict / initials ——
   手敲的拼音码对不上就永远匹配不上，所以导入不接受手填。
4. 失败行：必填为空、JSON 坏、表头缺列都要给出可读原因，且不写半截数据。
5. 词表（cfg.t_nc_word）走 upsert，二次导入不重复、替换模式先置 enabled=false。
6. **导入后真的生效**：导入 → `name_fix.reload_now()` → 同音错字被改成标准名。
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import time
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
CREATE TABLE IF NOT EXISTS t_nc_catalog (
  id bigserial PRIMARY KEY, name varchar(200) NOT NULL,
  kind varchar(32) NOT NULL DEFAULT '', owner varchar(200) NOT NULL DEFAULT '',
  py_full varchar(200) NOT NULL DEFAULT '', py_strict varchar(200) NOT NULL DEFAULT '',
  initials varchar(80) NOT NULL DEFAULT '', core_code varchar(80) NOT NULL DEFAULT '',
  source_id varchar(64) NOT NULL DEFAULT '', deleted_flag smallint NOT NULL DEFAULT 0,
  create_time timestamp NOT NULL DEFAULT now(), update_time timestamp NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS t_nc_pinyin (
  id bigserial PRIMARY KEY, hanzi varchar(4) NOT NULL,
  initial varchar(4) NOT NULL DEFAULT '', final varchar(8) NOT NULL DEFAULT '',
  deleted_flag smallint NOT NULL DEFAULT 0,
  create_time timestamp NOT NULL DEFAULT now(), update_time timestamp NOT NULL DEFAULT now());
CREATE SCHEMA IF NOT EXISTS cfg;
CREATE TABLE IF NOT EXISTS cfg.t_nc_word (
  id bigserial PRIMARY KEY, kind varchar(24) NOT NULL, word varchar(80) NOT NULL,
  extra jsonb NOT NULL DEFAULT '{}', sort_no int NOT NULL DEFAULT 0,
  enabled boolean NOT NULL DEFAULT true, updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (kind, word));
"""

PINYIN_CSV = ('字,声母,韵母\n'
              '环,h,uan\n潭,t,an\n供,g,ong\n电,d,ian\n所,s,uo\n谭,t,an\n'
              '现,x,ian\n损,s,un\n线,x,ian\n')
CATALOG_CSV = '名称,类别,所属\n环潭供电所,供电所,\n'
WORD_CSV = '类型,词,附加,排序\nsuffix_hint,供电所,{},1\nunit_kind,供电所,{},1\n'


def _csv(text: str) -> bytes:
    return ('\ufeff' + text).encode('utf-8')


def _xlsx(rows: list[list[str]]) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def main() -> int:
    print('纠错词典批量导入验收')
    base = Path(tempfile.gettempdir())
    try:
        db = pgserver.get_server(base / ('pgsrv_lex_import_%d_%d' % (os.getpid(), int(time.time()))))
        uri = db.get_uri()
    except Exception as exc:                              # noqa: BLE001
        print('  [SKIP] 起不了内嵌 PG：%s' % str(exc)[:120])
        return 0
    conn = psycopg.connect(uri, autocommit=True)
    conn.execute(DDL)
    conn.close()
    os.environ['PG_DSN'] = uri
    print('  PG = %s' % uri.rsplit('@', 1)[-1])

    from app.secretary import lex_import, name_fix

    def q(sql, params=()):
        c = psycopg.connect(uri, autocommit=True)
        try:
            cur = c.execute(sql, params)
            return [dict(zip([d.name for d in cur.description], r)) for r in cur.fetchall()]
        finally:
            c.close()

    def wipe():
        c = psycopg.connect(uri, autocommit=True)
        c.execute('TRUNCATE t_nc_typo, t_nc_term, t_nc_ambiguous, t_nc_wordlist, '
                  't_nc_catalog, t_nc_pinyin, cfg.t_nc_word RESTART IDENTITY')
        c.close()

    # ── 1 模板：中文表头，照抄就能填
    tpl = lex_import.template_csv('catalog')
    chk('模板有中文表头', bool(tpl) and '名称' in tpl.splitlines()[0]
        and '类别' in tpl.splitlines()[0], (tpl or '').splitlines()[:1])
    chk('未知类型不给模板', lex_import.template_csv('nope') is None)

    # ── 2 拼音表导入
    wipe()
    r = lex_import.import_file('pinyin', 'pinyin.csv', _csv(PINYIN_CSV))
    chk('拼音表导入成功', r.get('ok') and r.get('inserted') == 9, r)
    chk('拼音表落库 9 行', q('select count(*) n from t_nc_pinyin')[0]['n'] == 9)

    # ── 3 名称库：拼音码现算，与引擎同口径
    r = lex_import.import_file('catalog', 'catalog.csv', _csv(CATALOG_CSV))
    chk('名称库导入成功', r.get('ok') and r.get('inserted') == 1, r)
    row = q("select py_full, py_strict, initials, kind, deleted_flag "
            "from t_nc_catalog where name = '环潭供电所'")[0]
    chk('拼音码按引擎规则现算（模糊音已合并）', row['py_full'] == 'huantangondianso', row['py_full'])
    chk('严格音也一并算上', row['py_strict'] == 'huantangongdiansuo', row['py_strict'])
    chk('首字母', row['initials'] == 'htgds', row['initials'])
    chk('类别原样保留', row['kind'] == '供电所', row['kind'])
    chk('新行 deleted_flag=0', row['deleted_flag'] == 0)

    # ── 4 追加模式：同名的跳过，不重复插
    r = lex_import.import_file('catalog', 'catalog.csv', _csv(CATALOG_CSV))
    chk('重复导入全部跳过', r.get('ok') and r.get('inserted') == 0 and r.get('skipped') == 1, r)
    chk('库里还是一行', q('select count(*) n from t_nc_catalog')[0]['n'] == 1)

    # ── 5 表头缺列 / 必填为空 / 坏 JSON，都要给可读原因
    r = lex_import.import_file('catalog', 'bad.csv', _csv('编号,名字\n1,X\n'))
    chk('表头缺必填列要报错', 'error' in r and '名称' in r['error'], r)
    r = lex_import.import_file('typo', 'bad2.csv',
                               _csv('错误写法,正确写法\n,线损\n现损,\n线笋,线损\n'))
    chk('必填为空的行进失败明细',
        r.get('ok') and len(r['failed']) == 2 and r['inserted'] == 1, r)
    chk('失败明细带行号', r['failed'][0]['row'] == 2, r['failed'][:1])
    r = lex_import.import_file('word', 'bad3.csv', _csv('类型,词,附加\nunit_kind,所,{不是json}\n'))
    chk('附加列坏 JSON 要报错', r.get('ok') and len(r['failed']) == 1, r)

    # ── 6 XLSX
    wipe()
    lex_import.import_file('pinyin', 'p.csv', _csv(PINYIN_CSV))
    r = lex_import.import_file('typo', 'typo.xlsx',
                               _xlsx([['错误写法', '正确写法'], ['现损', '线损'], ['线笋', '线损']]))
    chk('XLSX 也能导', r.get('ok') and r.get('inserted') == 2, r)

    # ── 7 替换模式：先把旧行软删，再插新行
    r = lex_import.import_file('catalog', 'c.csv', _csv('名称,类别\n旧供电所,供电所\n'), 'replace')
    chk('替换模式导入新名称', r.get('ok') and r.get('inserted') == 1, r)
    r = lex_import.import_file('catalog', 'c2.csv', _csv('名称,类别\n新供电所,供电所\n'), 'replace')
    chk('替换模式导入第二批', r.get('ok') and r.get('inserted') == 1, r)
    rows = q('select name, deleted_flag from t_nc_catalog order by id')
    chk('旧行是软删不是物理删', len(rows) == 2 and rows[0]['deleted_flag'] == 1, rows)
    chk('列表里只看得到新行',
        q('select count(*) n from t_nc_catalog where deleted_flag = 0')[0]['n'] == 1)

    # ── 8 导出 → 再导入，行数与拼音码一致
    exp = lex_import.export_csv('catalog')
    chk('导出带表头', bool(exp) and exp.splitlines()[0].count(',') >= 2, (exp or '')[:40])
    before = q('select name, py_full from t_nc_catalog where deleted_flag = 0')[0]
    r = lex_import.import_file('catalog', 'export.csv', exp.encode('utf-8'), 'replace')
    chk('导出的文件能直接再导入', r.get('ok')
        and (r.get('inserted', 0) + r.get('updated', 0)) == 1, r)
    after = q('select name, py_full from t_nc_catalog where deleted_flag = 0')[0]
    chk('来回一趟拼音码没变', before == after, [before, after])

    # ── 9 词表：upsert 不重复，替换模式先置 enabled=false
    wipe()
    lex_import.import_file('pinyin', 'p.csv', _csv(PINYIN_CSV))
    lex_import.import_file('word', 'w.csv', _csv(WORD_CSV))
    r = lex_import.import_file('word', 'w.csv', _csv(WORD_CSV))
    chk('词表二次导入走更新', r.get('ok') and r.get('inserted') == 0
        and r.get('updated') == 2, r)
    chk('词表没有重复行', q('select count(*) n from cfg.t_nc_word')[0]['n'] == 2)
    lex_import.import_file('word', 'w2.csv', _csv('类型,词,附加\ncommon_word,负荷,{}\n'), 'replace')
    on = q('select word from cfg.t_nc_word where enabled order by word')
    chk('替换模式只留新导入的词', [x['word'] for x in on] == ['负荷'], on)
    chk('旧词是置 false 不是物理删', q('select count(*) n from cfg.t_nc_word')[0]['n'] == 3)

    # ── 10 体检：缺哪张表就点名
    wipe()
    st = lex_import.status()
    by = {x['key']: x for x in st['items']}
    chk('体检列出五类', sorted(by) == ['catalog', 'pinyin', 'typo', 'word', 'wordlist'], sorted(by))
    chk('拼音表空要提示', '拼音表为空' in by['pinyin']['hint'], by['pinyin']['hint'])
    chk('名称库空要提示', '名称库为空' in by['catalog']['hint'], by['catalog']['hint'])
    chk('错字表空不拦人', '可以为空' in by['typo']['hint'], by['typo']['hint'])

    # ── 11 导入后真的生效：导入 → 立即重载 → 同音错字改成标准名
    wipe()
    r0 = name_fix.reload_now()
    chk('空词典也能重载（不整体降级）', r0.get('ok'), r0)
    chk('空词典下这句话原样返回', name_fix.fix('环谭供电所的现损')['text'] == '环谭供电所的现损')

    lex_import.import_file('pinyin', 'p.csv', _csv(PINYIN_CSV))
    lex_import.import_file('catalog', 'c.csv', _csv('名称,类别\n环潭供电所,供电所\n'))
    lex_import.import_file('typo', 't.csv', _csv('错误写法,正确写法\n现损率,线损率\n'))
    lex_import.import_file('word', 'w.csv', _csv(WORD_CSV))
    r = name_fix.reload_now()
    chk('导入后重载成功', r.get('ok') and r['status'].get('available'), r)
    out = name_fix.fix('环谭供电所的现损率是多少')
    chk('同音错字改成库里的标准名', out['text'] == '环潭供电所的线损率是多少', out['text'])
    chk('明细里能看到改名依据', out['used'] and bool(out['name_fixes'])
        and out['name_fixes'][0]['正确'] == '环潭供电所', out['name_fixes'])
    chk('错字层也生效', bool(out['word_fixes'])
        and out['word_fixes'][0]['正确'] == '线损率', out['word_fixes'])

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(('  [OK]   ' if ok else '  [FAIL] ') + label
              + (('   ← ' + extra) if (not ok and extra) else ''))
    print('\n%d 项检查，%d 项失败' % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
