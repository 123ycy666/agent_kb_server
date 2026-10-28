# -*- coding: utf-8 -*-
"""Excel 直接问：openpyxl 读表 → 内存 sqlite3 → 一条只读 SELECT。

为什么用 stdlib sqlite3 而不是 duckdb（2026-10-21）
--------------------------------------------------
内网要离线装包。duckdb 快、SQL 全，但多一个 wheel；sqlite3 是 Python 自带，
一行依赖都不用加。10 万行 × 20 列这个量级，sqlite 全量读进内存毫秒级响应。

ponytail: 全量读进内存，单表 ≤ 约 50 万行够用；再大就换 duckdb + 落盘（接口不变）。

安全边界（内网也要有）
--------------------
- 只允许**一条** SELECT / WITH；出现第二段语句、写关键字一律拒。
- 结果最多 limit 行，多出来的截断并标记 truncated（不静默丢）。

自检：py -X utf8 server/tests/agent/test_excel_query.py
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_BAD = re.compile(r'\b(insert|update|delete|drop|alter|create|attach|detach|pragma|vacuum|replace)\b',
                  re.IGNORECASE)


class QueryError(RuntimeError):
    pass


def sheets(path) -> list[str]:
    from openpyxl import load_workbook
    wb = load_workbook(str(path), read_only=True, data_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def _headers(row) -> list[str]:
    """表头清洗：去空白、去引号、空的和重名的补 c{列号}。中文列名原样保留。"""
    out, seen = [], {}
    for i, v in enumerate(row, start=1):
        name = re.sub(r'[\s"]+', '', str(v or '')) or ('c%d' % i)
        seen[name] = seen.get(name, 0) + 1
        out.append(name if seen[name] == 1 else '%s_%d' % (name, seen[name]))
    return out


def load(path, sheet: str | None = None) -> tuple[sqlite3.Connection, list[str], str, int]:
    """把一张表读进内存 sqlite，返回 (连接, 列名, sheet 名, 行数)。表名固定为 t。"""
    from openpyxl import load_workbook

    path = Path(str(path))
    if not path.exists():
        raise QueryError('文件不存在：%s' % path.name)
    wb = load_workbook(str(path), read_only=True, data_only=True)
    try:
        ws = wb[sheet] if sheet else wb[wb.sheetnames[0]]
        rows = ws.iter_rows(values_only=True)
        head_row = next(rows, None)
        if not head_row:
            raise QueryError('表是空的：《%s》' % ws.title)
        cols = _headers(head_row)
        data = [r[:len(cols)] + (None,) * max(0, len(cols) - len(r)) for r in rows]
        nrow = len(data)
        conn = sqlite3.connect(':memory:')
        conn.execute('CREATE TABLE t (%s)' % ', '.join('"%s"' % c for c in cols))
        conn.executemany('INSERT INTO t VALUES (%s)' % ', '.join('?' * len(cols)), data)
        conn.commit()
        return conn, cols, ws.title, nrow
    finally:
        wb.close()


def _check(sql: str) -> str:
    s = (sql or '').strip().rstrip(';').strip()
    if not s:
        raise QueryError('SQL 是空的')
    if ';' in s:
        raise QueryError('只允许一条语句（发现分号）')
    low = s.lower()
    if not (low.startswith('select') or low.startswith('with')):
        raise QueryError('只允许 SELECT / WITH 查询')
    if _BAD.search(s):
        raise QueryError('只允许只读查询：检出写关键字')
    return s


def query(path, sql: str, *, sheet: str | None = None, limit: int = 200) -> dict:
    """对一张表跑一条只读 SQL。返回 {engine, sheet, columns, rows, nrows, truncated, sql}。"""
    s = _check(sql)
    conn, cols, sheet_name, total = load(path, sheet)
    try:
        cur = conn.execute(s)
        got = cur.fetchmany(limit + 1)
        columns = [d[0] for d in (cur.description or [])]
        truncated = len(got) > limit
        return {'engine': 'sqlite3', 'sheet': sheet_name, 'columns': columns,
                'rows': [list(r) for r in got[:limit]], 'nrows': total,
                'truncated': truncated, 'sql': s, 'table_columns': cols}
    finally:
        conn.close()
