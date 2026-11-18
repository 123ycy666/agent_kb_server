# -*- coding: utf-8 -*-
"""智能体自己的表。只新增 qa.agent_*，不动 t_chat_*。"""
from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from app.secretary import pgdb

DDL = """
create table if not exists qa.agent_session (
  id uuid primary key,
  user_id bigint not null,
  title text default '',
  status text default 'active',
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  deleted_flag smallint default 0
);

create table if not exists qa.agent_item (
  id bigserial primary key,
  session_id uuid not null references qa.agent_session(id) on delete cascade,
  type text not null,
  payload jsonb not null,
  tokens int default 0,
  archived boolean default false,
  created_at timestamptz default now()
);
create index if not exists agent_item_session_idx on qa.agent_item (session_id, id);

create table if not exists qa.agent_note (
  id bigserial primary key,
  session_id uuid not null,
  key text not null,
  body text default '',
  updated_at timestamptz default now(),
  unique (session_id, key)
);

create table if not exists qa.agent_memory (
  id bigserial primary key,
  user_id bigint not null,
  kind text not null default 'fact',
  text text not null,
  created_at timestamptz default now()
);
create index if not exists agent_memory_user_idx on qa.agent_memory (user_id, id desc);

create table if not exists qa.agent_run (
  id uuid primary key,
  session_id uuid,
  user_id bigint,
  model text,
  status text default 'running',
  error text default '',
  tokens int default 0,
  ms int default 0,
  created_at timestamptz default now()
);

create table if not exists qa.agent_span (
  id bigserial primary key,
  run_id uuid not null,
  seq int default 0,
  kind text not null,
  name text default '',
  payload jsonb,
  ms int default 0,
  created_at timestamptz default now()
);
create index if not exists agent_span_run_idx on qa.agent_span (run_id, id);
"""


def conn(autocommit: bool = True):
    return psycopg.connect(pgdb.DSN, autocommit=autocommit, row_factory=dict_row)


def ensure() -> None:
    with conn() as c:
        c.execute(DDL)


def query(sql: str, params=()) -> list[dict]:
    with conn() as c:
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


def one(sql: str, params=()) -> dict:
    rows = query(sql, params)
    return rows[0] if rows else {}


def execute(sql: str, params=()) -> None:
    with conn() as c:
        c.execute(sql, params)
