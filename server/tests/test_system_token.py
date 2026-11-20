# -*- coding: utf-8 -*-
"""系统账号（FR-SYS-001 / IF-SYS-001）：`X-Internal-Token` 挂在服务上，全局一份。

    python tests/test_system_token.py

三条必须成立：
  A. 令牌与服务配置一致 → 放行（不要求登录）
  B. 令牌不一致 → 401（不得降级成匿名）
  C. 服务没配令牌（本地开发）→ 不校验（保持现行为）
另外钉一条：这个令牌**不进库**，切现场不受影响（看 site 层不参与即可）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILS = []
OKS = []


def check(name, ok, detail=""):
    (OKS if ok else FAILS).append(name)
    print(("  [ OK ] " if ok else "  [FAIL] ") + name + ((" | " + str(detail)) if detail else ""))


def main() -> int:
    from app.core import system_auth
    from app.core.config import settings

    original = settings.internal_service_token
    try:
        settings.internal_service_token = "a" * 64
        check("配了令牌：对的值放行", system_auth.check("a" * 64) is True)
        check("配了令牌：错的值拒绝", system_auth.check("b" * 64) is False)
        check("配了令牌：空头拒绝", system_auth.check("") is False)
        check("配了令牌：大小写/长度不同都拒绝", system_auth.check(("a" * 63)) is False)

        settings.internal_service_token = ""
        check("没配令牌：不校验（本地开发）", system_auth.check("") is True)
        check("没配令牌：带垃圾值也放行", system_auth.check("whatever") is True)

        settings.internal_service_token = "c" * 64
        check("令牌只来自服务配置（不含库里任何字段）",
              system_auth.token() == "c" * 64 and system_auth.SOURCE == "service-env")
    finally:
        settings.internal_service_token = original

    print("\n结果：%d 通过 / %d 失败" % (len(OKS), len(FAILS)))
    if FAILS:
        print("失败项：", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
