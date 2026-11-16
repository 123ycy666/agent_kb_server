# -*- coding: utf-8 -*-
"""注入八段必须带来源（Task 3）。

跑法：`py -X utf8 server/tests/agent/test_inject_source.py`
需要本机 PG（cfg.inject_text 在它上面）；不写库，只读。
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from app.secretary import inject          # noqa: E402

checks: list[tuple[str, bool, str]] = []


def chk(name: str, cond, extra='') -> None:
    checks.append((name, bool(cond), str(extra)[:200]))


def run() -> None:
    out = inject.build(1, 'probe-session', '你好')
    parts = out['parts']

    chk('八段且顺序等于 ORDER', [p['name'] for p in parts] == list(inject.ORDER),
        [p['name'] for p in parts])

    allowed = {'库', '代码兜底', '自动生成', '调用方传入'}
    chk('每段都有 source', all(p.get('source') in allowed for p in parts),
        [(p['name'], p.get('source')) for p in parts])

    by = {p['name']: p for p in parts}
    chk('身份来自库', by['identity']['source'] == '库', by['identity']['source'])
    chk('目录行是自动生成', by['menu']['source'] == '自动生成', by['menu']['source'])
    chk('工具说明标明兜底或库', by['tools']['source'] in ('库', '代码兜底'),
        by['tools']['source'])
    chk('tools 段的来源与字数自洽',
        (by['tools']['chars'] > 0) or (by['tools']['source'] == '代码兜底'),
        '%s/%s' % (by['tools']['chars'], by['tools']['source']))

    bad = [c for c in checks if not c[1]]
    for name, ok, extra in checks:
        print(('PASS ' if ok else 'FAIL ') + name + ('  ' + extra if extra else ''))
    print('%d/%d' % (len(checks) - len(bad), len(checks)))
    raise SystemExit(1 if bad else 0)


if __name__ == '__main__':
    run()
