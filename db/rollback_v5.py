# -*- coding: utf-8 -*-
"""v5 回滚：public 视图 → 删视图 → 表搬回 public；并给出快照恢复兜底命令。

    python db/rollback_v5.py --dry     # 只看计划
    python db/rollback_v5.py           # 执行回滚
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from migrate_v5 import MOVES, connect, relkind  # noqa: E402


def main(dry=False) -> int:
    from psycopg import sql as S
    with connect() as conn:
        cur = conn.cursor()
        steps = []
        for t, grp in MOVES.items():
            pub, dst = relkind(cur, 'public', t), relkind(cur, grp, t)
            if pub == 'v':
                steps.append((t, grp, 'drop-view+move'))
            elif pub is None and dst in ('r', 'p'):
                steps.append((t, grp, 'move'))
            else:
                steps.append((t, grp, 'noop'))
        print('=== 回滚计划 ===')
        for t, grp, act in steps:
            print('  %-16s %-24s %s' % (act, t, grp))
        if dry:
            print('\n--dry：未执行')
            return 0
        for t, grp, act in steps:
            if act == 'drop-view+move':
                cur.execute(S.SQL('DROP VIEW IF EXISTS public.{}').format(S.Identifier(t)))
            if act in ('drop-view+move', 'move') and relkind(cur, grp, t) in ('r', 'p'):
                cur.execute(S.SQL('ALTER TABLE {}.{} SET SCHEMA public').format(
                    S.Identifier(grp), S.Identifier(t)))
                print('  ROLLBACK %s.%s → public' % (grp, t))
        left = [t for t, g in MOVES.items() if relkind(cur, 'public', t) != 'r']
        print('\n回滚后 public 实体表 %d/%d%s' % (len(MOVES) - len(left), len(MOVES),
                                           ('  缺：%s' % left) if left else ''))
        print('\n兜底：若数据结构本身坏了，用快照恢复：')
        print('  pg_restore -h 127.0.0.1 -p 5433 -U kb -d ad_new --clean --if-exists '
              '"output/backup/v5_before_<ts>/dump.sql"')
        return 0 if not left else 1


if __name__ == '__main__':
    sys.exit(main(dry='--dry' in sys.argv))
