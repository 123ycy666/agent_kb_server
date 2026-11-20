# -*- coding: utf-8 -*-
"""AC-04 把源现场的 `cfg.*` 与 `kb.*` 灌入空库后，问数跑通，结果与源现场一致。

判据（SRS-01 AC-04 / FR-PACK-001 / DR-004）：
- 两片 100% 复制（逐表行数相等）；
- 空库那边问同一个业务问题能跑通、答案里有数字；
- 与随州库的答案**数字集合一致**（模型措辞可能不同，数字必须同）。
- `qa.*` / `base.*` 不从源现场带过去（账号仍是本库的）。

    python tests/ac/ac04_clone_ask.py
"""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util as U  # noqa: E402

QUESTION = "上个月全市线损率是多少？"


def nums(text: str) -> list[str]:
    """答案里的**指标值** = 带小数的数字（2.62 / 0.85 …）。

    只比整数会把日期、编号、以及「同比变化为 0」这类措辞算成差异 ——
    两个现场问的是同一句、查的是同一个业务库，指标值必须一模一样。
    """
    return sorted(set(re.findall(r"\d+\.\d+", text or "")))


def ask(token: str, q: str):
    code, body, _ = U.req("POST", "/api/ask/data", {"question": q}, token=token, timeout=300)
    return code, (body or {}), str((body or {}).get("answer") or "")


def main() -> int:
    c = U.Checker("AC-04", "灌入 cfg/kb 后问数与源现场一致")
    token_empty = U.ensure_ad_empty()
    c.check("空库管理员可登录", bool(token_empty))
    if not token_empty:
        c.save()
        return 1

    U.header("复制 cfg.* 与 kb.*（ad_new → ad_empty）")
    proc = subprocess.run([U.PY, str(U.PROJECT / "db" / "pg_copy.py"),
                           "--from", "ad_new", "--to", "ad_empty"],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(U.PROJECT))
    out = (proc.stdout or "") + (proc.stderr or "")
    c.log(out[-3000:])
    c.check("pg_copy 退出码 0（逐表行数一致）", proc.returncode == 0, "exit=%s" % proc.returncode)
    for line in out.splitlines():
        if line.startswith("FAIL"):
            c.check("行数一致：%s" % line, False, line)

    st = U.site_status()
    c.check("复制后不再是空现场", st.get("business_empty") is False, st.get("business_empty"))
    n_doc = U.psql("ad_empty", "select count(*) from kb.document")[0][0]
    n_mat = U.psql("ad_empty", "select count(*) from cfg.prompt_material")[0][0]
    c.check("kb.document 有内容", int(n_doc) > 0, n_doc)
    c.check("cfg.prompt_material 有内容", int(n_mat) > 0, n_mat)

    U.header("同一个问题，两个现场各问一次")
    # ad_empty 需要新的 token（复制不动 base 片，token 仍有效；这里重新登录更稳）
    token_empty = U.login(U.EMPTY_ADMIN["username"], U.EMPTY_ADMIN["password"]) or token_empty
    code_e, body_e, ans_e = ask(token_empty, QUESTION)
    c.log("ad_empty 问：%s\n  答（%s）：%s" % (QUESTION, code_e, ans_e))
    c.check("空库（已灌内容）问数跑通", code_e == 200 and bool(ans_e) and "该现场尚未配置" not in ans_e,
            "HTTP %s" % code_e)
    c.check("答案里有指标值（真查了库）", bool(nums(ans_e)), nums(ans_e))

    # 切回随州问同一句
    token_new = U.login(U.SITE_ADMIN["username"], U.SITE_ADMIN["password"])
    if token_new:
        code_a, body_a, ans_a = ask(token_new, QUESTION)
    else:                                             # 现场还在 ad_empty，用空库管理员切回去
        token_new = token_empty
        U.switch_site(U.SITE_A, U.SITE_ADMIN, token=token_empty)
        token_new = U.login(U.SITE_ADMIN["username"], U.SITE_ADMIN["password"])
        code_a, body_a, ans_a = ask(token_new, QUESTION)
    c.log("ad_new 问：%s\n  答（%s）：%s" % (QUESTION, code_a, ans_a))
    c.check("随州问数跑通", code_a == 200 and bool(ans_a), "HTTP %s" % code_a)

    left, right = nums(ans_e), nums(ans_a)
    # 比「指标值」而不是整句：模型可能多说一句「同比变化 0.00%」，那是措辞不是数据。
    # 判据：两边必须有共同的指标值，且**独有的**指标值只能是 0.xx 这种「无变化」表述。
    trivial = {"0.0", "0.00", "0.000"}
    common = set(left) & set(right)
    extra = (set(left) ^ set(right)) - trivial
    c.check("两个现场的指标值一致", bool(common) and not extra,
            "ad_empty=%s / ad_new=%s（共同 %s，独有 %s）" % (left, right, sorted(common),
                                                          sorted(extra)))
    c.log("全部数字（含日期/计数，仅供参考）：ad_empty=%s / ad_new=%s"
          % (re.findall(r"\d+(?:\.\d+)?", ans_e), re.findall(r"\d+(?:\.\d+)?", ans_a)))

    U.header("现场迁移只带 cfg/kb：账号不随现场走")
    users = [r[0] for r in U.psql("ad_empty", "select username from base.app_user")]
    c.check("ad_empty 的账号仍是自己的初始管理员", U.EMPTY_ADMIN["username"] in users, users)
    c.check("没有把源现场的管理员带过来", U.SITE_ADMIN["username"] not in users, users)
    qa = U.psql("ad_empty", "select count(*) from qa.t_chat_session")[0][0]
    c.check("qa.* 没从源现场带过来（会话数是本次自己产生的）", int(qa) < 100, qa)
    c.save()
    return 0 if not c.failed else 1


if __name__ == "__main__":
    sys.exit(main())
