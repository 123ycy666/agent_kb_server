# -*- coding: utf-8 -*-
"""纠错词典的批量导入 / 导出 / 模板（页面「导入」用）。

页面一条一条点不动 2 万条名称库，这里给三条路：
  · template_csv(key)  下载表头模板（中文表头，Excel 直接开）
  · import_file(...)   读 CSV / XLSX → 校验 → 算拼音码 → 分批写库
  · export_csv(key)    把当前库导成同格式 CSV（另一套实例拿去导入）

拼音码不让人手填：名称库的 py_full / py_strict / initials 一律按纠错引擎的同一套
规则现算（`name_corrector._encode_with`），与 `Corrector` 运行时读的键完全同口径 ——
手敲的拼音码对不上就是「名字永远匹配不上」，而且不报错。

写库不碰 update_time 的老值：新行用默认 now()，改行显式 now()。
`name_fix` 每 60 秒比一次 MAX(update_time) 指纹，指纹变了才整体重建引擎。
"""
from __future__ import annotations

import csv
import io
import json
import time

import psycopg
from psycopg.rows import dict_row

from app.secretary import pgdb

CHUNK = 500                 # 每批插入行数
MAX_ROWS = 60000            # 单次导入上限（名称库最大 2 万，留余量）
MAX_FAILS = 30              # 失败明细最多回 30 条

# key -> 规格：认哪些列、哪些必填、用什么去重、拼音码要不要现算
SPECS = {
    'catalog': {
        'table': 't_nc_catalog',
        'name': '名称库（公司 / 供电所 / 服务站 / 台区）',
        'why': '同音改名与名称归一的主体。缺它，系统认不出用户说的是哪个单位。',
        'cols': [('name', '名称'), ('kind', '类别'), ('owner', '所属'),
                 ('core_code', '核心码'), ('source_id', '来源ID')],
        'detail': [('py_full', '拼音码'), ('py_strict', '拼音码_严'), ('initials', '首字母')],
        'required': ('name',),
        'key': ('name',),
        'compute': 'catalog',
    },
    'typo': {
        'table': 't_nc_typo',
        'name': '错别字纠正',
        'why': '错字 → 正确写法，精确替换。只换字，不补名字。',
        'cols': [('wrong', '错误写法'), ('right_word', '正确写法'), ('org', '适用组织'),
                 ('freq_level', '频率等级'), ('reason', '错因类型'), ('sample', '纠正示例')],
        'required': ('wrong', 'right_word'),
        'key': ('wrong',),
    },
    'wordlist': {
        'table': 't_nc_wordlist',
        'name': '标准词表',
        'why': '业务标准词，按拼音把同音错字改成标准词。',
        'cols': [('word', '标准词'), ('domain', '领域'), ('src', '来源')],
        'detail': [('initials', '首字母')],
        'required': ('word',),
        'key': ('word',),
        'compute': 'wordlist',
    },
    'pinyin': {
        'table': 't_nc_pinyin',
        'name': '拼音表（汉字 → 声母 / 韵母）',
        'why': '所有同音比较的读音来源。缺它，同音归一整体不可用。',
        'cols': [('hanzi', '字'), ('initial', '声母'), ('final', '韵母')],
        'required': ('hanzi',),
        'key': ('hanzi',),
    },
    'word': {
        'table': 'cfg.t_nc_word',
        'name': '单位规则（类型词 / 后缀 / 停用词）',
        'why': '决定什么算单位名、哪个后缀能认、哪些常用词不许当地名。缺它，名称归一关闭。',
        'cols': [('kind', '类型'), ('word', '词'), ('extra', '附加'), ('sort_no', '排序')],
        'required': ('kind', 'word'),
        'key': ('kind', 'word'),
        'upsert': True,
    },
}

ORDER = ('pinyin', 'word', 'catalog', 'typo', 'wordlist')


def _spec(key):
    return SPECS.get(str(key or '').strip())


def _conn():
    return psycopg.connect(pgdb.DSN, row_factory=dict_row)


def _cols(spec):
    return spec['cols'] + list(spec.get('detail') or [])


def _norm(s):
    return ''.join(str(s or '').replace('\ufeff', '').split()).lower()


def _map_header(spec, header):
    """表头 → {列下标: 库列名}。中文表头、库列名、大小写与空格都认。"""
    alias = {}
    for c, cn in _cols(spec):
        alias[_norm(c)] = c
        alias[_norm(cn)] = c
    out = {}
    for i, h in enumerate(header):
        c = alias.get(_norm(h))
        if c and c not in out.values():
            out[i] = c
    return out


def _read_grid(filename, content):
    name = str(filename or '').lower()
    if name.endswith('.xlsx') or name.endswith('.xlsm'):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        try:
            grid = [[('' if v is None else str(v).strip()) for v in row]
                    for row in wb.active.iter_rows(values_only=True)]
        finally:
            wb.close()
        return grid
    text = ''
    for enc in ('utf-8-sig', 'gbk', 'utf-8'):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        raise ValueError('文件编码认不出来，请存成 UTF-8 或 GBK 的 CSV / XLSX')
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text))]


def _pinyin_table():
    with _conn() as conn:
        rows = conn.execute('select hanzi, initial, final from t_nc_pinyin '
                            'where deleted_flag = 0').fetchall()
    return {str(r['hanzi']): (str(r['initial'] or ''), str(r['final'] or '')) for r in rows}


def _codes(name, table):
    """按纠错引擎的规则算三个拼音列（与 catalog 既有数据同口径）。"""
    from app.secretary.libs.name_correction_lib import name_corrector as nc
    full = nc._encode_with(name, table, True)
    strict = nc._encode_with(name, table, False)
    initials = ''.join((table.get(ch) or (ch, ''))[0] for ch in name)
    return full, strict, initials


def _existing(key, spec):
    """已有键 → {'id': ..., 'deleted': 0/1}。词表没有 deleted_flag，用 enabled 顶替。"""
    cols = list(spec['key'])
    flag = 'case when enabled then 0 else 1 end' if spec.get('upsert') else 'deleted_flag'
    with _conn() as conn:
        rows = conn.execute('select id, %s, %s as flag from %s'
                            % (', '.join(cols), flag, spec['table'])).fetchall()
    out = {}
    for r in rows:
        out[tuple(str(r[c] or '') for c in cols)] = {'id': r['id'],
                                                     'deleted': int(r['flag'] or 0)}
    return out


def _soft_delete_all(conn, spec):
    if spec.get('upsert'):
        conn.execute('UPDATE cfg.t_nc_word SET enabled = false, updated_at = now() '
                     'WHERE enabled')
        return
    conn.execute('UPDATE %s SET deleted_flag = 1, update_time = now() WHERE deleted_flag = 0'
                 % spec['table'])


def import_file(key, filename, content, mode='append'):
    """导入一个文件。返回 {'ok', 'key', 'mode', 'total', 'inserted', 'updated',
    'skipped', 'failed': [{'row': 行号, 'reason': 说明}], 'ms'}。"""
    spec = _spec(key)
    if not spec:
        return {'error': '不支持的导入类型：%s' % key}
    mode = 'replace' if str(mode).strip().lower() == 'replace' else 'append'
    if not content:
        return {'error': '文件是空的'}
    t0 = time.time()
    try:
        grid = _read_grid(filename, content)
    except Exception as exc:                       # noqa: BLE001 - 解析失败要给原话
        return {'error': '文件读不了：%s: %s' % (type(exc).__name__, str(exc)[:160])}
    grid = [r for r in grid if any(str(c).strip() for c in r)]
    if len(grid) < 2:
        return {'error': '文件里没有数据行（第一行必须是表头）'}
    header, body = grid[0], grid[1:]
    if len(body) > MAX_ROWS:
        return {'error': '单次最多 %d 行，这份有 %d 行，请拆开导' % (MAX_ROWS, len(body))}

    mapping = _map_header(spec, header)
    miss = [cn for c, cn in _cols(spec) if c in spec.get('required', ()) and c not in mapping.values()]
    if miss:
        return {'error': '表头缺必填列：%s（模板里有的列请照抄）' % '、'.join(miss)}

    rows, failed = [], []
    for n, raw in enumerate(body, start=2):
        rec = {}
        for i, c in mapping.items():
            rec[c] = str(raw[i]).strip() if i < len(raw) else ''
        for c in spec.get('required', ()):
            if not rec.get(c):
                failed.append({'row': n, 'reason': '「%s」为空' % dict(_cols(spec))[c]})
                break
        else:
            if spec['table'] == 'cfg.t_nc_word':
                try:
                    extra = json.loads(rec.get('extra') or '{}')
                except ValueError:
                    failed.append({'row': n, 'reason': '附加列不是 JSON'})
                    continue
                rec['extra'] = json.dumps(extra, ensure_ascii=False)
                try:
                    rec['sort_no'] = int(float(rec.get('sort_no') or 0))
                except ValueError:
                    failed.append({'row': n, 'reason': '排序列不是数字'})
                    continue
            rows.append((n, rec))

    # 文件内部去重：同一个键保留最后一行
    seen, uniq = {}, []
    for n, rec in rows:
        k = tuple(str(rec.get(c) or '') for c in spec['key'])
        if k in seen:
            uniq[seen[k]] = (n, rec)
        else:
            seen[k] = len(uniq)
            uniq.append((n, rec))

    if not uniq:
        return {'ok': True, 'key': key, 'mode': mode, 'total': len(body), 'inserted': 0,
                'updated': 0, 'skipped': 0, 'failed': failed[:MAX_FAILS],
                'ms': int((time.time() - t0) * 1000),
                'note': '没有可导入的行'}

    # 拼音码现算（名称库 / 标准词表）：先读一次拼音表，整批复用
    warn = ''
    if spec.get('compute'):
        table = _pinyin_table()
        if not table:
            warn = '拼音表是空的，拼音码算不出来（导入的这列会留空，同音匹配不会生效）'
        for _n, rec in uniq:
            name = str(rec.get('name') or rec.get('word') or '')
            full, strict, initials = _codes(name, table) if table else ('', '', '')
            if spec['compute'] == 'catalog':
                rec['py_full'], rec['py_strict'], rec['initials'] = full, strict, initials
            else:
                rec['initials'] = initials
            for c, _cn in spec.get('detail') or []:
                rec.setdefault(c, '')
    else:
        for _n, rec in uniq:
            for c, _cn in spec.get('detail') or []:
                rec.setdefault(c, '')

    inserted = updated = skipped = 0
    try:
        with _conn() as conn:
            have = _existing(key, spec)
            if mode == 'replace':
                _soft_delete_all(conn, spec)
            batch = []
            for _n, rec in uniq:
                k = tuple(str(rec.get(c) or '') for c in spec['key'])
                hit = have.get(k)
                if hit and not spec.get('upsert') and not hit['deleted'] and mode == 'append':
                    skipped += 1
                    continue
                if hit and not spec.get('upsert'):
                    cols = [c for c, _cn in _cols(spec) if c in rec]
                    conn.execute('UPDATE %s SET %s, deleted_flag = 0, update_time = now() '
                                 'WHERE id = %%s'
                                 % (spec['table'], ', '.join('%s = %%s' % c for c in cols)),
                                 tuple(rec.get(c) or '' for c in cols) + (hit['id'],))
                    updated += 1
                    continue
                batch.append((rec, not hit))
            if batch:
                cols = [c for c, _cn in _cols(spec)]
                sql = ('INSERT INTO %s (%s) VALUES (%s)'
                       % (spec['table'], ', '.join(cols), ', '.join(['%s'] * len(cols))))
                if spec.get('upsert'):
                    sql += (' ON CONFLICT (kind, word) DO UPDATE SET extra = excluded.extra, '
                            'sort_no = excluded.sort_no, enabled = true, updated_at = now()')
                for i in range(0, len(batch), CHUNK):
                    part = batch[i:i + CHUNK]
                    with conn.cursor() as cur:
                        cur.executemany(sql, [[r.get(c) if r.get(c) is not None else ''
                                               for c in cols] for r, _new in part])
                    inserted += sum(1 for _r, new in part if new)
                    updated += sum(1 for _r, new in part if not new)
            conn.commit()
    except Exception as exc:                       # noqa: BLE001 - 写库失败要给人看得懂的原话
        return {'error': '写库失败（已回滚）：%s: %s' % (type(exc).__name__, str(exc)[:200])}

    if key == 'word':
        from app.secretary import lex_words
        lex_words.clear_cache()

    out = {'ok': True, 'key': key, 'mode': mode, 'total': len(body),
           'inserted': inserted, 'updated': updated, 'skipped': skipped,
           'failed': failed[:MAX_FAILS], 'ms': int((time.time() - t0) * 1000)}
    if warn:
        out['warning'] = warn
    return out


def _header(key, spec):
    return [cn for _c, cn in _cols(spec)]


def template_csv(key):
    """空模板：中文表头 + 一行示例（示例只教填法，不含任何现场词）。"""
    spec = _spec(key)
    if not spec:
        return None
    sample = {
        'catalog': ['某某县供电公司', '供电公司', '', '', ''],
        'typo': ['某某供电所', '某某供电公司', '', '', '', ''],
        'wordlist': ['某某指标', '电力', '导入'],
        'pinyin': ['字', 'shengmu', 'yunmu'],
        'word': ['suffix_hint', '供电所', '{}', '0'],
    }.get(key, [''] * len(spec['cols']))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_header(key, spec))
    w.writerow(sample)
    return '\ufeff' + buf.getvalue()


def export_csv(key):
    spec = _spec(key)
    if not spec:
        return None
    cols = [c for c, _cn in _cols(spec)]
    with _conn() as conn:
        rows = conn.execute('select %s from %s where deleted_flag = 0 order by id'
                            % (', '.join(cols), spec['table'])).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_header(key, spec))
    for r in rows:
        w.writerow([r.get(c) if r.get(c) is not None else '' for c in cols])
    return '\ufeff' + buf.getvalue()


def _count(spec):
    try:
        with _conn() as conn:
            if spec['table'] == 'cfg.t_nc_word':
                return int(conn.execute('select count(*) n from cfg.t_nc_word '
                                        'where enabled').fetchone()['n'])
            return int(conn.execute('select count(*) n from %s where deleted_flag = 0'
                                    % spec['table']).fetchone()['n'])
    except Exception:                              # noqa: BLE001 - 表没建也算 0
        return 0


def status():
    """页面体检：七类各多少条 + 缺哪一步会导致什么能力关闭。"""
    counts = {k: _count(SPECS[k]) for k in ORDER}
    items = []
    for k in ORDER:
        spec = SPECS[k]
        n = counts[k]
        hint = ''
        if k == 'pinyin' and not n:
            hint = '拼音表为空：同音归一整体不可用，先导这一张。'
        elif k == 'word' and not n:
            hint = '单位规则为空：名称归一关闭，只做错别字层。'
        elif k == 'catalog' and not n:
            hint = '名称库为空：认不出供电所 / 公司名，同音改名不会发生。'
        elif k in ('typo', 'wordlist') and not n:
            hint = '可以为空；缺了只是少了这一层纠错。'
        items.append({'key': k, 'name': spec['name'], 'rows': n,
                      'why': spec['why'], 'hint': hint})
    return {'ok': True, 'items': items, 'counts': counts}
