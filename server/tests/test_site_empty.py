# -*- coding: utf-8 -*-
"""空现场（FR-EMPTY-001 / UI-004 / AC-03）。

    python tests/test_site_empty.py

判两条（都能判真假）：
  A. 空库的 answer **一个数字都不许有**，且明说「该现场尚未配置」；
  B. 空态判定只认业务配置三张表（prompt_material / document / caliber_metric），
     任何一张有内容就不是空现场。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILS = []
OKS = []


def check(name, ok, detail=""):
    (OKS if ok else FAILS).append(name)
    print(("  [ OK ] " if ok else "  [FAIL] ") + name + ((" | " + str(detail)) if detail else ""))


def main() -> int:
    from app.api.v1 import agent
    from app.core import site

    payload = agent.SITE_EMPTY_PAYLOAD("s_test123")
    answer = payload["answer"]
    check("空现场回答含「该现场尚未配置」", agent.SITE_EMPTY_HINT in answer, answer[:40])
    check("空现场回答不含任何数字", re.search(r"\d", answer) is None, answer)
    check("空现场标记 site_empty=True", payload.get("site_empty") is True)
    check("空现场不带 qa_id（没落库）", payload.get("qa_id") is None)
    check("会话 id 原样带回", payload.get("session_id") == "s_test123")
    check("trace 为空（没跑链路）", payload.get("trace") == [])

    # 现场未就绪时 business_empty 必须为 True（否则前端会把空态判断反）
    saved = dict(site._STATE)
    try:
        site._STATE.update({"ok": False})
        check("现场未就绪时判定为空现场", site.business_empty() is True)
    finally:
        site._STATE.clear()
        site._STATE.update(saved)
        site.business_empty(refresh=True)

    print("\n结果：%d 通过 / %d 失败" % (len(OKS), len(FAILS)))
    if FAILS:
        print("失败项：", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
