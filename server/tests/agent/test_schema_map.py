# -*- coding: utf-8 -*-
"""库结构读取的验收（Task 1）。

跑法：`py -X utf8 server/tests/agent/test_schema_map.py`
对着本机真库跑（只读为主）；注释写回用一张临时表 public.__probe，跑完删掉。
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from app.secretary import schema_map as S      # noqa: E402

checks: list[tuple[str, bool, str]] = []


def chk(name: str, cond, extra='') -> None:
    checks.append((name, bool(cond), str(extra)[:200]))


def run() -> None:
    S.ensure()
    ov = S.overview()

    chk('四组齐全', {r['group'] for r in ov} == set(S.GROUPS), len(ov))
    chk('表数 ≥ 20', len(ov) >= 20, len(ov))
    chk('每行都有 purpose/document/page 三列',
        all({'purpose', 'documented', 'page', 'cols', 'rows'} <= set(r) for r in ov))

    d = S.table_detail('kb.document')
    chk('字段清单非空', len(d['columns']) >= 10, len(d['columns']))
    chk('每个字段都有 comment 键', all('comment' in c for c in d['columns']))
    chk('能取前 20 行', isinstance(d['rows'], list))
    chk('字段数与本表实际列数一致',
        len(d['columns']) ==
        [r for r in ov if r['group'] == 'kb' and r['table'] == 'document'][0]['cols'])

    S._q('create table if not exists public.__probe (id int, note text)')
    S.set_comment('public.__probe', 'note', '探针字段')
    S.set_table_comment('public.__probe', '探针表')
    d2 = S.table_detail('public.__probe')
    chk('字段注释写回', any(c['name'] == 'note' and c['comment'] == '探针字段'
                          for c in d2['columns']), d2['columns'])
    S._q('drop table if exists public.__probe')

    bad = [c for c in checks if not c[1]]
    for name, ok, extra in checks:
        print(('PASS ' if ok else 'FAIL ') + name + ('  ' + extra if extra else ''))
    print('%d/%d' % (len(checks) - len(bad), len(checks)))
    raise SystemExit(1 if bad else 0)


if __name__ == '__main__':
    run()
