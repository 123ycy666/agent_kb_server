# -*- coding: utf-8 -*-
"""F4-3 验收：个人偏好（文风/格式）——真 PG 存取 + 三级覆盖。

钉死六条
--------
1. 白名单外的键**报错**，不静默丢。
2. 数值越界报错（字号 12–22、行距 20–36）。
3. 真 PG：存 → 读回一致；整体替换生效。
4. 三级覆盖：站点默认 ← 个人 ← 本次请求，后者覆盖前者。
5. 没设偏好的人：拿到站点默认（不报错）。
6. 文档生成真的用上了个人偏好（pipeline 读的是 merge_fmt）。

运行：py -X utf8 server/tests/agent/test_prefs.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVER))

from app.secretary import prefs as P, prompts_data  # noqa: E402

_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = "") -> None:
    _checks.append((label, bool(cond), extra))


def main() -> int:
    print("F4-3：个人偏好（文风/格式）")
    base = prompts_data.DOC_DEFAULTS

    # ── 1 白名单 ───────────────────────────────────────────────
    chk("不认识的键报错", _raises(lambda: P.normalize({'hack': 1})))
    chk("字号越界报错", _raises(lambda: P.normalize({'body_pt': 40})))
    chk("行距越界报错", _raises(lambda: P.normalize({'line_spacing_pt': 5})))
    chk("字号非数字报错", _raises(lambda: P.normalize({'body_pt': '三号'})))
    ok = P.normalize({'body_font_cn': '仿宋', 'body_pt': 15, 'line_spacing_pt': 26,
                      'sign_off': '环潭供电所'})
    chk("合法偏好原样保留", ok == {'body_font_cn': '仿宋', 'body_pt': 15,
                              'line_spacing_pt': 26, 'sign_off': '环潭供电所'}, str(ok))
    chk("空值被忽略", P.normalize({'body_font_cn': '', 'body_pt': None}) == {})

    # ── 2 真 PG 存取 ───────────────────────────────────────────
    res = P.set(2026, {'body_pt': 15, 'line_spacing_pt': 26, 'sign_off': '环潭供电所'})
    if res.get('saved'):
        got = P.get(2026)
        chk("真库：存进去读得回来",
            got.get('body_pt') == 15 and got.get('sign_off') == '环潭供电所', str(got))
        P.set(2026, {'body_pt': 18})
        got2 = P.get(2026)
        chk("真库：整体替换（旧键消失）", got2 == {'body_pt': 18}, str(got2))

        # ── 3 三级覆盖 ────────────────────────────────────────
        fmt = P.merge_fmt(base, 2026)
        chk("个人偏好覆盖站点默认（字号 18）",
            fmt.get('body_pt') == 18 and fmt.get('body_font_cn'), str(fmt)[:120])
        fmt2 = P.merge_fmt(base, 2026, {'body_pt': 22})
        chk("本次请求覆盖个人偏好（22）", fmt2.get('body_pt') == 22, str(fmt2['body_pt']))
        fmt3 = P.merge_fmt(base, 999999)
        chk("没设偏好的人拿站点默认",
            fmt3.get('body_pt') == (base.get('docx') or {}).get('body_pt'), str(fmt3['body_pt']))
        P.set(2026, {})
        chk("清空后回到站点默认", P.get(2026) == {})
    else:
        print("  [跳过] 真库不可用：%s" % res.get('warning'))

    # ── 4 文档生成用上了偏好 ───────────────────────────────────
    import io
    src = io.open(str(SERVER / 'app/secretary/pipeline_doc_write.py'), encoding='utf-8').read()
    chk("写公文走 merge_fmt（三级覆盖）", 'merge_fmt' in src)

    bad = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print(("  [OK]   " if ok else "  [FAIL] ") + label + (("   ← " + extra) if not ok and extra else ""))
    print("\n%d 项检查，%d 项失败" % (len(_checks), len(bad)))
    return 1 if bad else 0


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:                              # noqa: BLE001
        return True


if __name__ == '__main__':
    sys.exit(main())
