"""密码哈希、JWT 签发校验、密钥脱敏。

设计要点
--------
1. 用户密码分两套来源：
   - **统一用户表 dlj_data.t_employee**（argon2id，HR 系统维护，只读）→ `verify_employee_password`
     注意：对方系统的密码原文是 `password + "_" + employeeUid.upper() + "_" + employeeUid.lower()`，
     不是裸明文，详见 `employee_password_raws`。
   - 本地 bcrypt（历史遗留 / 测试）→ `verify_password`
2. JWT     ：PyJWT，HS256
"""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError

from app.core.config import settings

# ══════════════════════════════════════════════════════════════
#  密码
# ══════════════════════════════════════════════════════════════

_BCRYPT_MAX_BYTES = 72

# argon2id 校验器（t_employee.login_pwd 的格式：
# $argon2id$v=19$m=16384,t=2,p=1$<salt>$<hash>）。
# 参数编码在哈希串里，verify 时自动读取，无需知道 HR 侧用的参数。
_ARGON2 = PasswordHasher()


def _prepare_password(password: str) -> bytes:
    """bcrypt 只支持前 72 字节，超长密码先做摘要以保证等效安全强度。"""
    raw = password.encode("utf-8")
    if len(raw) > _BCRYPT_MAX_BYTES:
        # 先 sha256 再 base64，得到定长 44 字节，避免静默截断
        return base64.b64encode(hashlib.sha256(raw).digest())
    return raw


def hash_password(password: str) -> str:
    """生成密码哈希（bcrypt，仅本地测试/遗留用途）。"""
    return bcrypt.hashpw(_prepare_password(password), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    """校验密码（bcrypt）。任何异常都视为校验失败。"""
    try:
        return bcrypt.checkpw(_prepare_password(password), hashed.encode())
    except (ValueError, TypeError):
        return False


def employee_password_raws(password: str, employee_uid: str | None) -> list[str]:
    """密码**原文**候选（按优先级返回，逐个拿去 argon2 比对）。

    权威源（数智问效 / Snowy 系后端）**不是**直接对明文做 argon2，
    而是先拼一段文本再哈希：

        raw = f"{password}_{uid.upper()}_{uid.lower()}"

    对应对方 Java 侧：
        String raw = password + "_" + employeeUid.toUpperCase() + "_" + employeeUid.toLowerCase();
        Argon2PasswordEncoder.defaultsForSpringSecurity_v5_8().matches(raw, loginPwd)

    ⚠️ 两点必须记住，否则下次还会踩：

    1. **这不是「服务端私有 pepper」**。employee_uid 就躺在同一行里，
       任何人都能算出来 —— 它只是一个「每用户固定、无需额外存储」的盐，
       用来打断通用彩虹表。真正的安全边界仍然是 argon2 本身。
    2. **员工没登录过 ≠ 密码错**：employee_uid 为空时无法拼出原文，
       这类账号在本规则下天然登不上（对方 Java 侧会 NPE）。

    兼容：本系统早期自建账号（seed 出来的 kbadmin 等）存的是**裸明文** argon2，
    因此把裸明文追加为兜底候选，保证这次修复不会打死老账号。
    """
    raws: list[str] = []
    if employee_uid:
        raws.append(f"{password}_{employee_uid.upper()}_{employee_uid.lower()}")
    raws.append(password)
    return raws


def verify_employee_password(
    password: str, stored_hash: str, employee_uid: str | None = None
) -> bool:
    """校验统一用户表（t_employee）的 argon2id 密码。

    依次尝试「UID 拼接原文」（**对方系统的权威规则**）与「裸明文」（本地老账号兼容），
    命中任一即通过。详见 :func:`employee_password_raws`。

    - stored_hash 为空 / 非 argon2 格式 → 返回 False（不允许空密码登录）
    - 密码错误 / 哈希损坏 → False
    ⚠️ 故意不区分「用户不存在」与「密码错误」，由调用方统一返回同一报错，
       避免账号枚举。
    """
    if not stored_hash or not password:
        return False
    for raw in employee_password_raws(password, employee_uid):
        try:
            if _ARGON2.verify(stored_hash, raw):
                return True
        except (VerifyMismatchError, InvalidHash, ValueError, TypeError):
            continue
    return False


# ══════════════════════════════════════════════════════════════
#  JWT
# ══════════════════════════════════════════════════════════════


def create_access_token(subject: str | int, extra: dict[str, Any] | None = None) -> str:
    """签发访问令牌。subject 为用户 id。"""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
        "typ": "access",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """解析访问令牌，失败返回 None（不抛异常，便于中间件处理）。"""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None


# ══════════════════════════════════════════════════════════════
#  密钥脱敏展示
# ══════════════════════════════════════════════════════════════


def mask_secret(secret: str, keep_head: int = 6, keep_tail: int = 4) -> str:
    """脱敏展示，如 sk-ab****cd12。"""
    if not secret:
        return ""
    if len(secret) <= keep_head + keep_tail:
        return "*" * len(secret)
    return f"{secret[:keep_head]}{'*' * 6}{secret[-keep_tail:]}"
