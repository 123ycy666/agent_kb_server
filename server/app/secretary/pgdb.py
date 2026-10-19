# -*- coding: utf-8 -*-
"""PG 兼容层 —— 让原 pymysql 风格的代码（qa_log / lex_source）无感切到 PostgreSQL。

2026-10-15 数据层合并：纠错词典与对话记录从 MySQL agent_data 迁到 PG ad_new
（与知识库同库，一个账号的全部数据可 JOIN）。为不改这两百多行 SQL，
这里做一个 pymysql 风格的薄封装：

- ``connect(autocommit=True)`` → ``psycopg.connect(row_factory=dict_row)``
- ``execute()`` 前自动做方言翻译：反引号 → 双引号、``IFNULL`` → ``COALESCE``
- ``INSERT`` 自动追加 ``RETURNING id`` → ``cursor.lastrowid``（pymysql 习惯）
- ``fetchone()/fetchall()`` 返回 dict（等价 DictCursor）

⚠️ 只覆盖本项目实际用到的 SQL 形态。新写的代码请直接用 psycopg，
    不要依赖这层兼容（它是迁移期的桥，不是长期方案）。
"""
from __future__ import annotations

import re

import psycopg
from psycopg.rows import dict_row

# ad_new 库 DSN（与 .env 的 DATABASE_URL 指向同一个库）
DSN = "host=127.0.0.1 port=5433 dbname=ad_new user=kb password=kb_dev_pwd"


def _translate(sql: str) -> str:
    """MySQL → PG 方言的最小翻译集。"""
    # 反引号标识符 → 双引号（本项目列名均为小写，与 PG 建表一致）
    sql = sql.replace("`", '"')
    # IFNULL(a,b) → COALESCE(a,b)
    sql = re.sub(r"\bIFNULL\s*\(", "COALESCE(", sql, flags=re.I)
    return sql


class _Cursor:
    """pymysql DictCursor 风格。"""

    def __init__(self, cur: psycopg.Cursor):
        self._cur = cur
        self.lastrowid: int | None = None
        self.rowcount: int = 0

    def execute(self, sql: str, params=None) -> None:
        sql = _translate(sql)
        is_insert = sql.strip().lower().startswith("insert")
        if is_insert and "returning" not in sql.lower():
            sql = sql.rstrip().rstrip(";") + " RETURNING id"
        self._cur.execute(sql, params or ())
        if is_insert:
            row = self._cur.fetchone()
            self.lastrowid = int(row["id"]) if row else None
            self.rowcount = self._cur.rowcount if self._cur.rowcount >= 0 else 1
        else:
            self.rowcount = self._cur.rowcount

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def close(self):
        self._cur.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class _Conn:
    """pymysql.Connection 风格（dict 行 + autocommit）。"""

    def __init__(self, autocommit: bool = True):
        self._conn = psycopg.connect(DSN, autocommit=autocommit, row_factory=dict_row)

    def cursor(self) -> _Cursor:
        return _Cursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def ping(self, reconnect: bool = True) -> bool:
        return True


def connect(autocommit: bool = True, **_kwargs) -> _Conn:
    """等价 pymysql.connect(...) 的入口。"""
    return _Conn(autocommit=autocommit)
