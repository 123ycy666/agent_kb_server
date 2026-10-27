# -*- coding: utf-8 -*-
"""P0 回归测试：Excel 直接问（openpyxl → 内存 sqlite3 → 一条只读 SELECT）。

用合成数据（造一个"环潭合计不平"的小表），验三件事：
1. 能查出来 —— GROUP BY + HAVING 找出合计不平的台区（这是 P0 的验收场景的最小版）。
2. 只读守卫生效 —— 写语句、多语句、空语句都拒。
3. 截断不静默 —— 超过 limit 时 truncated=True。

⚠️ 这是**合成数据**，只证明逻辑对；真实台账（10 万行）要等 Fred 给样本再验。

运行：py -X utf8 server/tests/agent/test_excel_query.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # server/

from app.secretary import tools_excel  # noqa: E402

ROWS = [
    ('台区', '明细金额', '上报合计'),   # 上报合计 = 该台区报上来的合计（同台区每行重复）
    ('环潭', 100, 100),
    ('环潭', 50, 100),                  # 明细 150 ≠ 上报 100 → **不平**
    ('文峰', 200, 200),                 # 200 = 200 → 平
    ('均川', 30, 50),                   # 30+20 = 50 → 平
    ('均川', 20, 50),
]
_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = "") -> None:
    _checks.append((label, bool(cond), extra))


def _make_xlsx(path: Path, rows) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = '预算台账'
    for r in rows:
        ws.append(list(r))
    wb.save(str(path))


def main() -> int:
    print("P0：Excel 直接问")
    # 固定目录（不用 mkdtemp：它在沙箱下建出来的目录写不进文件，见计划 §根因）
    tmp = Path(__file__).resolve().parents[2] / '.tmp' / 'xlsx_q'
    tmp.mkdir(parents=True, exist_ok=True)
    book = tmp / '台账.xlsx'
    _make_xlsx(book, ROWS)

    # ── 1 能查出不平的台区 ──────────────────────────────
    sql = ('SELECT 台区, SUM(明细金额) AS 明细合计, MAX(上报合计) AS 上报合计 '
           'FROM t GROUP BY 台区 HAVING 明细合计 <> 上报合计')
    res = tools_excel.query(book, sql)
    chk("1 查出 1 个不平台区", len(res['rows']) == 1, str(res['rows']))
    chk("1b 就是环潭，150 vs 100",
        res['rows'] == [['环潭', 150, 100]], str(res['rows']))
    chk("1c 引擎是 sqlite3、sheet 名对、总行数 5",
        res['engine'] == 'sqlite3' and res['sheet'] == '预算台账' and res['nrows'] == 5,
        '%s/%s/%s' % (res['engine'], res['sheet'], res['nrows']))
    chk("1d 中文列名原样可用", res['columns'] == ['台区', '明细合计', '上报合计'],
        str(res['columns']))

    # ── 2 只读守卫 ──────────────────────────────────────
    for bad in ('DELETE FROM t', 'UPDATE t SET 明细金额 = 0',
                'SELECT 1; DROP TABLE t', '', 'INSERT INTO t VALUES (1,2,3)'):
        try:
            tools_excel.query(book, bad)
            ok = False
        except tools_excel.QueryError:
            ok = True
        chk("2 拒绝 %r" % bad[:28], ok)

    # ── 3 截断不静默 ────────────────────────────────────
    small = tools_excel.query(book, 'SELECT 台区 FROM t', limit=2)
    chk("3 limit=2 → 只回 2 行且 truncated=True",
        len(small['rows']) == 2 and small['truncated'] is True,
        '%d/%s' % (len(small['rows']), small['truncated']))

    # ── 4 表头清洗（空列名 / 重名）────────────────────────
    chk("4 表头清洗：空→cN、重名加序号",
        tools_excel._headers(['', 'a', 'a']) == ['c1', 'a', 'a_2'],
        str(tools_excel._headers(['', 'a', 'a'])))

    bad_checks = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(("  [OK]   " if ok else "  [FAIL] ") + label + (("   ← " + extra) if not ok and extra else ""))
    print("\n%d 项检查，%d 项失败" % (len(_checks), len(bad_checks)))
    return 1 if bad_checks else 0


if __name__ == '__main__':
    sys.exit(main())
