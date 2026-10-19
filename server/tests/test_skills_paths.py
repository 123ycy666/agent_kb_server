# -*- coding: utf-8 -*-
"""回归测试：技能目录必须真的存在且能被读到（纯内存，不连库、不打模型）。

背景（2026-10-16 修的真实故障）
--------------------------------
`server/app/secretary/report.py` 里把技能目录算成了 `app/skills`（多算一层 dirname），
而技能文档实际在 `app/secretary/skills/`。因为 `list_skill_docs()` 里
`os.listdir` 的异常被 `except Exception: pass` 静默吞掉，表现是：

- `GET /api/skills` → `{"skills": []}`（技能台一片空白）
- `GET /api/triggers` → 技能条目 0 个（读不到 `技能触发配置.json`）
- 报告技能读不到 `预算分配.md` / `数据表说明书.md` / `判据规则.md`

**整片技能功能静默失效**，而页面上没有任何报错。这个测试把这条钉死：
路径算错、目录被删、JSON 被挪走，都会立刻红。

运行： python server/tests/test_skills_paths.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.secretary import report  # noqa: E402

# 技能目录下必须有这些文件（新增技能只要加 .md，不用改这里）
REQUIRED_DOCS = ["预算分配.md", "数据表说明书.md", "判据规则.md"]
REQUIRED_JSON = ["技能触发配置.json", "状态变更规则.json"]
MIN_SKILL_DOCS = 10          # 当前 15 个；留点余量，少于这个数说明目录明显不对

_checks: list[tuple[str, bool, str]] = []


def chk(label: str, cond: bool, extra: str = "") -> None:
    _checks.append((label, bool(cond), extra))


def main() -> int:
    print("技能目录回归测试")

    chk("SKILL_DIR 存在", os.path.isdir(report.SKILL_DIR), report.SKILL_DIR)
    chk("SKILL_DIR 在 secretary 下",
        Path(report.SKILL_DIR).name == "skills"
        and Path(report.SKILL_DIR).parent.name == "secretary",
        report.SKILL_DIR)

    for name, path in [("SKILL_DOC", report.SKILL_DOC),
                       ("TABLE_DOC", report.TABLE_DOC),
                       ("RULE_DOC", report.RULE_DOC)]:
        chk("%s 存在" % name, os.path.isfile(path), os.path.basename(path))

    for fn in REQUIRED_DOCS:
        chk("技能文档 %s" % fn, os.path.isfile(os.path.join(report.SKILL_DIR, fn)))
    for fn in REQUIRED_JSON:
        p = os.path.join(report.SKILL_DIR, fn)
        readable = False
        if os.path.isfile(p):
            try:
                readable = json.load(open(p, encoding="utf-8")) is not None
            except Exception:                      # noqa: BLE001
                readable = False
        chk("规则 JSON %s 可解析" % fn, readable)

    docs = report.list_skill_docs()
    chk("list_skill_docs 数量 >= %d" % MIN_SKILL_DOCS, len(docs) >= MIN_SKILL_DOCS,
        "实际 %d" % len(docs))
    chk("每条技能都有标题", bool(docs) and all(d.get("title") for d in docs),
        docs[0]["title"] if docs else "（空）")

    # 这两个接口以前静默返回空，重点盯
    tr = report.list_triggers()
    if isinstance(tr, dict) and tr.get("error"):
        chk("list_triggers 无 error", False, str(tr["error"])[:80])
    else:
        skills = (tr or {}).get("skills") or []
        chk("list_triggers 有技能条目", len(skills) > 0, "%d 条" % len(skills))
        chk("技能条目带技能文档正文",
            any(s.get("doc_text") for s in skills),
            "%d 条带正文" % len([s for s in skills if s.get("doc_text")]))

    sc = report.state_change_sim()
    chk("state_change_sim 正常",
        isinstance(sc, dict) and not sc.get("error"), str((sc or {}).get("error") or "")[:80])

    failed = [c for c in _checks if not c[1]]
    for label, ok, extra in _checks:
        print("  %s %-26s %s" % ("✅" if ok else "❌", label, extra))
    print("\n%d/%d 通过" % (len(_checks) - len(failed), len(_checks)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
