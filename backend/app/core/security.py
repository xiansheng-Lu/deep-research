"""安全原语：JWT 签发/校验、密码哈希。

密码哈希直接使用 ``bcrypt`` 而非 ``passlib``，避开 passlib 1.7.x 在
Python 3.12 + bcrypt 4.x 下的 ``detect_wrap_bug`` 探测失败问题。
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import Settings, get_settings

# bcrypt 仅消费前 72 字节；超出部分会被忽略。统一在调用侧截断，
# 避免静默丢字符（注册与登录使用同一函数，行为对齐）。
_BCRYPT_MAX_BYTES = 72


def _truncate_password(plain: str) -> bytes:
    """截断密码到 bcrypt 安全上限 72 字节（UTF-8）。"""
    return plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain: str) -> str:
    """对明文密码进行 bcrypt 哈希。"""
    return bcrypt.hashpw(_truncate_password(plain), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文与哈希是否匹配。"""
    try:
        return bcrypt.checkpw(_truncate_password(plain), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def create_access_token(
    subject: str,
    *,
    extra_claims: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> str:
    """签发访问令牌。"""
    cfg = settings or get_settings()
    now = datetime.now(tz=timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=cfg.jwt_access_ttl_minutes)).timestamp()),
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, cfg.secret_key.get_secret_value(), algorithm=cfg.jwt_algorithm)


def create_refresh_token(
    subject: str,
    *,
    settings: Settings | None = None,
) -> str:
    """签发刷新令牌（有效期更长）。"""
    cfg = settings or get_settings()
    now = datetime.now(tz=timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=cfg.jwt_refresh_ttl_days)).timestamp()),
        "type": "refresh",
    }
    return jwt.encode(payload, cfg.secret_key.get_secret_value(), algorithm=cfg.jwt_algorithm)


def decode_token(token: str, *, settings: Settings | None = None) -> dict[str, Any]:
    """解码并校验令牌；失败时抛出 JWTError。"""
    cfg = settings or get_settings()
    try:
        return jwt.decode(token, cfg.secret_key.get_secret_value(), algorithms=[cfg.jwt_algorithm])
    except JWTError as exc:  # 由调用方捕获并转换为业务异常
        raise exc