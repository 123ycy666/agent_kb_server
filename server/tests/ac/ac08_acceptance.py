# -*- coding: utf-8 -*-
"""AC-08 现有验收用例在随州库上全过。

判据（SRS-01 AC-08 / NFR-003 / DR-002）：
- `server/tests/acceptance.py` 全过（A/B/C/D 四阶段）；
- 跑完 `cfg.*` / `kb.*` 的**行数与关键表内容哈希不变**（验收不能把随州数据改坏）。

⚠️ 勘误（实测，不是本次改造造成的）：acceptance.py 原来写死账号 `kbadmin`，
   业务库 t_employee 里**没有这个人**（128 名员工，只有 `admin`），所以 A1/A5 在任何
   代码版本下都失败。这里用 `AC_USER/AC_PASSWORD` 覆盖成现场初始管理员 `admin` 再跑，
   用例本身的断言一条没改。

    python tests/ac/ac08_acceptance.py
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util as U  # noqa: E402

AC_USER = os.environ.get("AC_USER") or "admin"
AC_PASSWORD = os.environ.get("AC_PASSWORD") or "Suizhou@2026"


def cleanup_uploaded(c: U.Checker) -> None:
    """删掉验收自己上传的文档（文件名以 acceptance- 开头），别污染随州库。"""
    rows = U.psql("ad_new", "select id, filename from kb.document "
                            "where filename like 'acceptance-%'")
    for doc_id, name in rows:
        U.psql("ad_new", "delete from kb.document_chunk where doc_id=%s" % int(doc_id))
        U.psql("ad_new", "delete from kb.document where id=%s" % int(doc_id))
        c.log("清理验收上传的文档：%s(id=%s)" % (name, doc_id))
    left = U.psql("ad_new", "select count(*) from kb.document "
                            "where filename like 'acceptance-%'")[0][0]
    c.check("清理干净（没有残留 acceptance-* 文档）", int(left) == 0, left)


def main() -> int:
    c = U.Checker("AC-08", "随州库上现有验收用例全过")
    U.ensure_ad_new()
    c.check("当前现场是随州 ad_new", U.site_status().get("site_id", "").endswith("/ad_new"),
            U.site_status().get("site_id"))

    before = U.fingerprint("ad_new")
    c.log("验收前 cfg/kb 指纹（行数与关键表哈希）：%s"
          % {k: v for k, v in before.items() if k.endswith("#sha")})

    U.header("跑 server/tests/acceptance.py")
    env = dict(os.environ, AC_USER=AC_USER, AC_PASSWORD=AC_PASSWORD,
               PYTHONIOENCODING="utf-8")
    proc = subprocess.run([U.PY, "tests/acceptance.py"], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(U.SERVER), env=env, timeout=1800)
    out = (proc.stdout or "") + (proc.stderr or "")
    c.log(out[-8000:])
    fails = [ln.strip() for ln in out.splitlines() if "[FAIL]" in ln]
    c.check("验收脚本退出码 0", proc.returncode == 0, "exit=%s" % proc.returncode)
    c.check("没有 [FAIL] 项", not fails, fails[:8])
    summary = [ln for ln in out.splitlines() if "验收结果" in ln]
    c.check("汇总行显示 0 失败", bool(summary) and "0 失败" in summary[-1],
            summary[-1] if summary else "无汇总行")

    U.header("跑完复查：随州数据没被改坏")
    cleanup_uploaded(c)
    after = U.fingerprint("ad_new")
    diff = {k: (before.get(k), after.get(k)) for k in set(before) | set(after)
            if before.get(k) != after.get(k)}
    c.check("cfg/kb 行数与关键表哈希完全一致", not diff, diff)
    c.save()
    return 0 if not c.failed else 1


if __name__ == "__main__":
    sys.exit(main())
