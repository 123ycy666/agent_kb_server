# -*- coding: utf-8 -*-
"""AC-07 `server/app` 检索不到 NFR-004 列出的业务词。

判据（SRS-01 AC-07 / NFR-004）：14 个单位名一个都不许出现（含注释、docstring、示例）。

    python tests/ac/ac07_no_site_words.py
"""
import re
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util as U  # noqa: E402

WORDS = ["随州", "国网随州供电公司", "随县", "曾都", "广水", "高新", "环潭", "两水",
         "厉山", "淅河", "万和", "淮河", "白鹤", "龚家棚"]
SUFFIX = (".py", ".md", ".json", ".txt", ".yaml", ".yml", ".html", ".js", ".sql")


def main() -> int:
    c = U.Checker("AC-07", "代码里没有业务单位名")
    root = SERVER / "app"
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix in SUFFIX]
    c.check("扫描范围内有文件", len(files) > 100, "%d 个文件" % len(files))

    hits = []
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for w in WORDS:
                if w in line:
                    hits.append("%s:%d: %s" % (p.relative_to(SERVER), i, w))
    for h in hits[:40]:
        c.log("命中 " + h)
    c.check("14 个单位名命中数为 0", not hits, "%d 处" % len(hits))

    # 反向确认「扫描真有效」：同一个扫描器在 db/ 的迁移源稿里必须能命中
    probe = []
    seed = SERVER.parent / "db" / "seed_b1.py"
    if seed.is_file():
        txt = seed.read_text(encoding="utf-8", errors="ignore")
        probe = [w for w in WORDS if w in txt]
    c.check("同一扫描在 db/seed_b1.py 能命中（证明扫描有效）", len(probe) >= 5, probe)

    # 运行时路径也不许出现：把关键模块的源码再单独扫一遍（防止只在 .md 里清干净）
    runtime = [p for p in (root / "secretary").rglob("*.py")] + \
              [p for p in (root / "api").rglob("*.py")] + \
              [p for p in (root / "core").rglob("*.py")]
    bad = []
    for p in runtime:
        t = p.read_text(encoding="utf-8", errors="ignore")
        bad += ["%s:%s" % (p.name, w) for w in WORDS if w in t]
    c.check("运行时 Python 模块里也没有", not bad, bad[:6])
    c.save()
    return 0 if not c.failed else 1


if __name__ == "__main__":
    sys.exit(main())
