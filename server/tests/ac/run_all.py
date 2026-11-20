# -*- coding: utf-8 -*-
"""顺序跑 AC-01..AC-08，并写总索引。

    python tests/ac/run_all.py            # 全跑
    python tests/ac/run_all.py AC-03      # 只跑某一项

顺序不能乱：AC-01 要先把现场弄坏再恢复；AC-02 重建空库；AC-03 在空库上问；
AC-04 灌内容；AC-05/06 来回切；AC-07 纯静态；AC-08 最后回随州跑现有验收。
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _util as U  # noqa: E402

ORDER = ["ac01_empty_gate", "ac02_config_login", "ac03_empty_ask", "ac04_clone_ask",
         "ac05_switch_token", "ac06_system_token", "ac07_no_site_words", "ac08_acceptance"]


def main() -> int:
    only = [a.upper().replace("AC", "AC-").replace("--", "-") for a in sys.argv[1:]]
    results = []
    for name in ORDER:
        ac = "AC-" + name[2:4]
        if only and ac not in only:
            continue
        U.header("跑 %s" % name)
        t0 = time.time()
        proc = subprocess.run([U.PY, "-u", str(HERE / (name + ".py"))],
                              capture_output=True, text=True, encoding="utf-8",
                              env={**__import__("os").environ,
                                   "PYTHONIOENCODING": "utf-8"})
        out = (proc.stdout or "") + (proc.stderr or "")
        print(out[-4000:])
        results.append((ac, proc.returncode == 0, round(time.time() - t0, 1), name))
        if proc.returncode != 0 and not only:
            print("\n%s 未通过，后续依赖它的项没有继续跑。" % ac)
            break

    lines = ["# 验收总索引（2026-11-19 骨架与业务库）", "",
             "> 全部由 `server/tests/ac/run_all.py` 实际跑出；逐项证据见同目录 AC-*.md。", "",
             "| 验收 | 结果 | 耗时(s) | 脚本 |", "|---|---|---|---|"]
    for ac, ok, sec, name in results:
        lines.append("| %s | %s | %s | `server/tests/ac/%s.py` |"
                     % (ac, "PASS" if ok else "FAIL", sec, name))
    passed = sum(1 for _, ok, _, _ in results if ok)
    lines += ["", "**合计：%d/%d 通过**（%s）"
              % (passed, len(results), time.strftime("%Y-%m-%d %H:%M:%S")), ""]
    lines += ["", "## 各项结论", ""]
    for ac, ok, _, _ in results:
        lines.append("- %s：**%s**" % (ac, "通过" if ok else "不通过"))
    p = U.EVID / "00-index.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print("\n总索引：%s" % p)
    print("合计：%d/%d 通过" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
