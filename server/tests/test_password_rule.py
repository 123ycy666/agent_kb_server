"""密码校验规则回归测试（纯内存，不连库）。

背景（2026-10-16 定位到的真实故障）
------------------------------------
统一用户表 `dlj_data.t_employee` 由**数智问效系统**写入，它的密码原文规则是：

    raw = password + "_" + employeeUid.toUpperCase() + "_" + employeeUid.toLowerCase()

即 argon2 的输入**不是裸明文**，而是"明文 + 员工 UID 的大小写各拼一份"。
我们早期按"裸明文 argon2"实现，导致**对方系统建的账号全部登不上**，
而自己 seed 的账号（kbadmin）反而能登 —— 两边互为镜像。

本文件把这条兼容规则钉死：谁再把它改回裸明文，CI 立刻红。

运行： python server/tests/test_password_rule.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argon2 import PasswordHasher  # noqa: E402

from app.core.security import employee_password_raws, verify_employee_password  # noqa: E402

PH = PasswordHasher()

UID = "a727a008d4ec42f198f36a28d6a316a0"
PWD = "Passw0rd#1"


def _hash(text: str) -> str:
    return PH.hash(text)


def test_raw_shape_is_plain_uidupper_uidlower():
    """拼接形状必须与对方 Java 侧逐字符一致（下划线、大小写各一份）。"""
    raws = employee_password_raws(PWD, UID)
    assert raws[0] == f"{PWD}_{UID.upper()}_{UID.lower()}", raws[0]
    # 分隔符只有 2 个（pwd_大写_小写），别多加也别少加
    assert raws[0].count("_") - PWD.count("_") == 2
    assert UID.upper() in raws[0] and UID.lower() in raws[0]
    # 兜底候选必须是裸明文，保证老账号可用
    assert raws[1] == PWD


def test_authority_rule_verifies():
    """按权威规则存的哈希，能用 UID 校验通过。"""
    stored = _hash(f"{PWD}_{UID.upper()}_{UID.lower()}")
    assert verify_employee_password(PWD, stored, UID) is True


def test_legacy_plain_argon2_still_verifies():
    """兼容：早期 seed 的裸明文 argon2 仍然能登。"""
    stored = _hash(PWD)
    assert verify_employee_password(PWD, stored, UID) is True


def test_wrong_password_rejected():
    stored = _hash(f"{PWD}_{UID.upper()}_{UID.lower()}")
    for bad in ("", PWD[:-1], PWD + "x", PWD.lower(), UID):
        assert verify_employee_password(bad, stored, UID) is False, bad


def test_missing_uid_degrades_to_plain_only():
    """employee_uid 为空时只能试裸明文（对方 Java 侧这种情况会 NPE）。"""
    assert employee_password_raws(PWD, None) == [PWD]
    stored = _hash(f"{PWD}_{UID.upper()}_{UID.lower()}")
    assert verify_employee_password(PWD, stored, None) is False


def test_empty_hash_never_passes():
    assert verify_employee_password(PWD, "", UID) is False
    assert verify_employee_password(PWD, "not-a-hash", UID) is False


def _run() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  ❌ {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ❌ {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
