# -*- coding: utf-8 -*-
"""AC-03 空库状态下问数不返回具体数字，页面显示「该现场尚未配置」。

判据（SRS-01 AC-03 / FR-EMPTY-001 / UI-004）：
- `/api/ask` 200，答案含「该现场尚未配置」，**一个数字都没有**；
- `site_empty=true`、没有 qa_id（不落库、不查库）；
- `/api/v1/site/status` 的 `business_empty=true`（前端据此显示空态）。

    python tests/ac/ac03_empty_ask.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util as U  # noqa: E402

QUESTIONS = ["现在还有多少条缺陷工单没闭环？", "上个月全市线损率是多少？"]


def main() -> int:
    c = U.Checker("AC-03", "空库问数不给数字")
    st = U.site_status()
    c.check("当前现场是空库 ad_empty", st.get("site_id", "").endswith("/ad_empty"),
            st.get("site_id"))
    c.check("business_empty=true", st.get("business_empty") is True)

    token = U.login(U.EMPTY_ADMIN["username"], U.EMPTY_ADMIN["password"])
    c.check("空库能登录", bool(token))
    if not token:
        c.save()
        return 1

    for q in QUESTIONS:
        code, body, raw = U.req("POST", "/api/ask", {"question": q}, token=token)
        answer = str((body or {}).get("answer") or "")
        c.log("问：%s\n答：%s" % (q, answer))
        c.check("「%s」HTTP 200" % q, code == 200, code)
        c.check("答案明说未配置", "该现场尚未配置" in answer, answer[:60])
        c.check("答案里没有任何数字", re.search(r"\d", answer) is None,
                re.findall(r"\d+", answer))
        c.check("带 site_empty 标记", (body or {}).get("site_empty") is True,
                (body or {}).get("site_empty"))
        c.check("没有落库（qa_id 为空）", (body or {}).get("qa_id") in (None, ""),
                (body or {}).get("qa_id"))

    qa_rows = U.psql("ad_empty", "select count(*) from qa.t_chat_query_trace")[0][0]
    c.check("空库没有新增问数轨迹", int(qa_rows) == 0, qa_rows)
    c.save()
    return 0 if not c.failed else 1


if __name__ == "__main__":
    sys.exit(main())
