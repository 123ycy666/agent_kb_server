# -*- coding: utf-8 -*-
"""F2-3 验收：PDF 合并 / 拆分 / 抽页（pypdf）。

钉死五条
--------
1. 合并：3 份（1+2+3 页）→ 输出 6 页，且带书签。
2. 拆分：6 页按 2 页一拆 → 3 个文件，页数 2/2/2。
3. 抽页：抽第 2、4 页 → 2 页；页码越界明确报错。
4. 非 PDF / 不存在 → 明确报错（不是静默返回空）。
5. info() 能报页数与大小。

运行：py -X utf8 server/tests/agent/test_pdf.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

from app.secretary import tools_pdf as P  # noqa: E402

_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = "") -> None:
    _checks.append((label, bool(cond), extra))


def _make_pdf(path: Path, pages: int) -> None:
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=595, height=842)     # A4
    with open(path, 'wb') as f:
        w.write(f)


def main() -> int:
    print("F2-3：PDF 合并 / 拆分 / 抽页")
    work = SERVER / '.tmp' / 'pdf'
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    a, b, c = work / '甲.pdf', work / '乙.pdf', work / '丙.pdf'
    _make_pdf(a, 1)
    _make_pdf(b, 2)
    _make_pdf(c, 3)

    # ── 1 合并 ──────────────────────────────────────────────────
    merged = work / '合并.pdf'
    r = P.merge([a, b, c], merged)
    chk("合并：输入 1+2+3 页", [x['pages'] for x in r['inputs']] == [1, 2, 3],
        str(r['inputs']))
    chk("合并：输出 6 页", r['pages'] == 6, str(r['pages']))
    chk("合并：文件真的存在", merged.is_file())
    from pypdf import PdfReader
    rd = PdfReader(str(merged))
    chk("合并：读回 6 页", len(rd.pages) == 6, str(len(rd.pages)))
    chk("合并：书签存在", bool(rd.outline), str(rd.outline)[:60])

    # ── 2 拆分 ──────────────────────────────────────────────────
    out = work / '拆'
    rs = P.split(merged, out, every=2)
    chk("拆分：6 页按 2 页 → 3 个文件", len(rs['outputs']) == 3, str(rs['outputs']))
    sizes = [len(PdfReader(str(out / n)).pages) for n in rs['outputs']]
    chk("拆分：每个文件 2 页", sizes == [2, 2, 2], str(sizes))

    # ── 3 抽页 ──────────────────────────────────────────────────
    picked = work / '抽页.pdf'
    rp = P.pick_pages(merged, picked, [2, 4])
    chk("抽页：抽 2 页", rp['pages'] == 2 and len(PdfReader(str(picked)).pages) == 2)
    chk("抽页：越界页码报错", _raises(lambda: P.pick_pages(merged, picked, [99])))
    chk("抽页：非整数页码报错", _raises(lambda: P.pick_pages(merged, picked, ['二'])))

    # ── 4 异常 ──────────────────────────────────────────────────
    bad = work / '假.pdf'
    bad.write_bytes(b'not a pdf at all')
    chk("非 PDF 报错", _raises(lambda: P.info(bad)))
    chk("不存在的文件报错", _raises(lambda: P.info(work / 'nope.pdf')))
    chk("合并少于 2 个报错", _raises(lambda: P.merge([a], work / 'x.pdf')))

    # ── 5 info ──────────────────────────────────────────────────
    i = P.info(merged)
    chk("info：页数正确", i['pages'] == 6, str(i))
    chk("info：能报大小", i['size_kb'] > 0, str(i['size_kb']))

    bads = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(("  [OK]   " if ok else "  [FAIL] ") + label + (("   ← " + extra) if not ok and extra else ""))
    print("\n%d 项检查，%d 项失败" % (len(_checks), len(bads)))
    return 1 if bads else 0


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:                              # noqa: BLE001
        return True


if __name__ == '__main__':
    sys.exit(main())
