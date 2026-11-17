# -*- coding: utf-8 -*-
"""P2 落库侧验收：会话人设的 SQL 流程（用假 pgdb，不需要真 PG）。

为什么用假驱动（2026-10-21）
--------------------------
本机没有 PostgreSQL、没有 docker，pip 又被沙箱挡住（临时目录不可写/不可删），装不上
`psycopg`。所以这里用一个**最小假 pgdb** 承接 `persona.py` 发出的三条 SQL，
验证：语句形态（ON CONFLICT / UPDATE 列名 / WHERE）、参数顺序、存取回环、清空后读回 None。

⚠️ 它证明"我们的 SQL 与流程对"，**不证明 Postgres 真能跑**（语法兼容、列类型、
ON CONFLICT 行为要真库验）。真库验收在内网做一次即可。

运行：py -X utf8 server/tests/agent/test_persona_db.py
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

_checks: list[tuple[str, bool, str]] = []
STATES: list[tuple[str, tuple]] = []


def chk(label: str, cond: bool, extra: str = "") -> None:
    _checks.append((label, bool(cond), extra))


class FakeCursor:
    def __init__(self, store: dict):
        self.store = store
        self._rows: list = []

    def execute(self, sql, params=None):
        STATES.append((' '.join(sql.split()), tuple(params or ())))
        s = ' '.join(sql.split()).lower()
        p = tuple(params or ())
        if s.startswith('insert into t_chat_session'):
            sid = p[0]
            self.store.setdefault(sid, {'persona_name': None, 'persona_prompt': None,
                                        'user_id': p[1], 'user_code': p[2]})
        elif s.startswith('update t_chat_session set persona_name'):
            name, prompt, sid = (p + (None, None, None))[:3]
            row = self.store.setdefault(sid, {})
            row['persona_name'] = name
            row['persona_prompt'] = prompt
        elif s.startswith('select persona_name'):
            sid = p[0]
            row = self.store.get(sid)
            self._rows = [(row.get('persona_name'), row.get('persona_prompt'))] if row else []
        else:
            raise AssertionError('没预料到的 SQL：%s' % sql)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


class FakeConn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return FakeCursor(self.store)

    def close(self):
        pass


def install_fake_pgdb() -> dict:
    store: dict = {}
    mod = types.ModuleType('app.secretary.pgdb')
    mod.connect = lambda autocommit=True, **kw: FakeConn(store)
    sys.modules['app.secretary.pgdb'] = mod
    return store


def main() -> int:
    print("P2 落库侧：会话人设 SQL 流程（假 pgdb）")
    store = install_fake_pgdb()

    from app.secretary import persona as P

    # ── 1 设 → 读回 ────────────────────────────────────────────
    r = P.set_persona('s_1', name='参谋长', text='只挑毛病，不给客套话',
                      user_id=7, user_code='sz013')
    chk("设置返回 saved=True", r.get('saved') is True, str(r)[:100])
    chk("插入用 ON CONFLICT DO NOTHING",
        any('on conflict' in s.lower() for s, _ in STATES))
    chk("UPDATE 用 persona_name/persona_prompt",
        any(s.lower().startswith('update t_chat_session set persona_name') for s, _ in STATES))
    got = P.get_persona('s_1')
    chk("读回人设（名字+正文）",
        got == {'name': '参谋长', 'prompt': '只挑毛病，不给客套话'}, str(got))
    chk("落库记录里有该会话", 's_1' in store and bool(store['s_1'].get('persona_prompt')))

    # ── 2 清空 → 读回 None ─────────────────────────────────────
    P.set_persona('s_1', name='', text='')
    chk("清空后读回 None", P.get_persona('s_1') is None, str(P.get_persona('s_1')))

    # ── 3 不存在的会话 ─────────────────────────────────────────
    chk("未设置过的会话读回 None", P.get_persona('s_not_exist') is None)

    # ── 4 超长在落库前就被拦（不产生 SQL）──────────────────────
    n_sql = len(STATES)
    try:
        P.set_persona('s_2', name='x', text='字' * (P.MAX_TEXT + 1))
        blocked = False
    except P.PersonaError:
        blocked = True
    chk("超长被拦且没发 SQL", blocked and len(STATES) == n_sql,
        'SQL 多了 %d 条' % (len(STATES) - n_sql))

    # ── 5 人设真进了 system（端到端装配）───────────────────────
    P.set_persona('s_3', name='参谋长', text='只挑毛病，先结论后依据')
    from app.secretary import agent, agent_v2
    per = (P.get_persona('s_3') or {}).get('prompt')
    sysmsg = agent_v2.system('general', agent._system, persona=per)
    chk("会话人设进到最终 system", '只挑毛病，先结论后依据' in sysmsg)
    chk("红线仍在人设之前",
        sysmsg.index('诚信与安全红线') < sysmsg.index('只挑毛病，先结论后依据'))

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(("  [OK]   " if ok else "  [FAIL] ") + label + (("   ← " + extra) if not ok and extra else ""))
    print("\n%d 项检查，%d 项失败" % (len(_checks), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
