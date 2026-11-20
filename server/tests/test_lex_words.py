# -*- coding: utf-8 -*-
"""纠错词表入库（FR-MIGR-002）：词面在库，算法在代码，代码里没有业务词。

    python tests/test_lex_words.py

四条：
  A. 词表从 `cfg.t_nc_word` 读出来，形状对（configure 认得）；
  B. 注入前是空表（代码默认），注入后生效 —— 证明「词面不在代码里」；
  C. 注入后名称归一仍然工作（拿库里的第一条名称做样本，测试里不写死地名）；
  D. `server/app` 里检索不到 NFR-004 列的 14 个单位名。
"""
import json
import os
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

FAILS = []
OKS = []
WORDS = ["随州", "国网随州供电公司", "随县", "曾都", "广水", "高新", "环潭", "两水",
         "厉山", "淅河", "万和", "淮河", "白鹤", "龚家棚"]


def check(name, ok, detail=""):
    (OKS if ok else FAILS).append(name)
    print(("  [ OK ] " if ok else "  [FAIL] ") + name + ((" | " + str(detail)) if detail else ""))


def main() -> int:
    from app.secretary import lex_words
    from app.secretary.libs.name_correction_lib import name_corrector as nc

    w = lex_words.load(refresh=True)
    check("词表从库读出（非空）", bool(w.get("unit_kind")) and bool(w.get("area_suffix")),
          {k: (len(v) if hasattr(v, "__len__") else v) for k, v in w.items()})
    check("单位级别的 extra 带 level", isinstance(w.get("unit_level"), dict)
          and all(isinstance(v, int) for v in w["unit_level"].values()), w.get("unit_level"))
    check("口语级别词带 kinds", bool(w.get("asked_unit_suffix"))
          and all(r.get("kinds") for r in w["asked_unit_suffix"]))

    # B. 注入前：模块级常量必须是空的（代码里没词面）
    nc.configure({})
    check("未注入时单位类型为空（代码默认）", nc.UNIT_KINDS == set() and nc.AREA_SUFFIX == ()
          and nc.COMMON_WORDS == ())
    check("未注入时正则永不匹配", not nc.UNIT_SUFFIX_RE.search("供电所")
          and not nc.AREA_SUFFIX_RE.search("台区"))

    # 注入后生效
    nc.configure(w)
    check("注入后单位类型有词", len(nc.UNIT_KINDS) >= 1, sorted(nc.UNIT_KINDS))
    check("注入后可匹配单位后缀", bool(nc.UNIT_SUFFIX_RE.search("供电所")))

    # C. 拿库里的真实名称做样本（不写死任何地名）
    from app.secretary import name_fix

    sample = ""
    try:
        from app.secretary import pgdb
        with pgdb.connect(autocommit=True) as c:
            cur = c.cursor()
            cur.execute("select name from t_nc_catalog where kind like %s limit 1",
                        ("%供电所%",))
            row = cur.fetchone()
            sample = str((row or {}).get("name") or "")
    except Exception as exc:                          # noqa: BLE001
        print("    （样本读取失败：%s）" % exc)
    if sample:
        got = name_fix.fix(sample).get("text")
        check("库里名称原样通过（不误改）", got == sample, "%s → %s" % (sample, got))
    else:
        check("库里名称原样通过（不误改）", False, "取不到样本 —— 词典表可能没灌，这条不算过")

    # D. 代码里没有业务词
    hits = []
    for path in sorted((SERVER / "app").rglob("*")):
        if not path.is_file() or path.suffix not in (".py", ".md", ".json", ".txt", ".yaml"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for wd in WORDS:
            if wd in text:
                hits.append("%s → %s" % (path.relative_to(SERVER), wd))
    check("server/app 无 NFR-004 单位名", not hits, hits[:6])

    print("\n结果：%d 通过 / %d 失败" % (len(OKS), len(FAILS)))
    if FAILS:
        print("失败项：", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
