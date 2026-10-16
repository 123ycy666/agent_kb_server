"""密码哈希、JWT 签发校验、密钥脱敏。

设计要点
--------
1. 用户密码分两套来源：
   - **统一用户表 dlj_data.t_employee**（argon2id，HR 系统维护，只读）→ `verify_employee_password`
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


def verify_employee_password(password: str, stored_hash: str) -> bool:
    """校验统一用户表（t_employee）的 argon2id 密码。

    - stored_hash 为空 / 非 argon2 格式 → 返回 False（不允许空密码登录）
    - 密码错误 / 哈希损坏 → False
    ⚠️ 故意不区分「用户不存在」与「密码错误」，由调用方统一返回同一报错，
       避免账号枚举。
    """
    if not stored_hash or not password:
        return False
    try:
        return _ARGON2.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHash, ValueError, TypeError):
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
