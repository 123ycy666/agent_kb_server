"""合流护栏：路由传给 ask() 的关键字，必须都在 ask() 的签名里。

背景（2026-10-19 真实故障）：
    平台线的 api/v1/agent.py 调 ask(..., kb_ids=kb_scope)，
    而秘书线的 ask() 没有 kb_ids 形参
    ⇒ 每次提问都直接 TypeError: ask() got an unexpected keyword argument 'kb_ids'。
    两侧单测都绿，因为没人测「跨层调用契约」。

做法：**直接解析路由源码**里 run_in_threadpool(agent_mod.ask, ...) 的实参，
再和 ask() 的签名比对 —— 手写关键字清单会漏，源码不会。
不连数据库、不调模型。
"""
import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # server/
sys.path.insert(0, str(ROOT))

from app.secretary import agent as agent_mod        # noqa: E402

ROUTE_FILE = ROOT / "app" / "api" / "v1" / "agent.py"


def route_kwargs():
    """取出路由里 agent_mod.ask 那次调用的关键字实参。"""
    src = ROUTE_FILE.read_text(encoding="utf-8")
    anchor = src.index("run_in_threadpool(")
    i = anchor + len("run_in_threadpool(")
    depth, j = 1, i
    while depth and j < len(src):
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
        j += 1
    block = src[anchor:j]
    if "agent_mod.ask" not in block:
        raise AssertionError("路由里找不到 agent_mod.ask 调用，测试需同步更新")
    return sorted(set(re.findall(r"\b(\w+)\s*=", block)) - {"agent_mod"})


def main() -> int:
    params = set(inspect.signature(agent_mod.ask).parameters)
    used = route_kwargs()
    missing = [k for k in used if k not in params]
    ok = True

    print("  路由关键字: %s" % ", ".join(used))
    if missing:
        print("  FAIL 这些参数 ask() 不接受: %s" % ", ".join(missing))
        ok = False
    else:
        print("  PASS %d 个关键字全部在 ask() 签名内" % len(used))

    # kb_ids 通过 kb_adapter.set_scope() 的 ContextVar 传递，不能当形参
    if "kb_ids" in params or "kb_ids" in used:
        print("  FAIL kb_ids 不能走形参（应走 kb_adapter.set_scope）")
        ok = False
    else:
        print("  PASS kb_ids 未作为参数传递（走 ContextVar）")

    print("\n%s" % ("全部通过" if ok else "存在不通过项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
