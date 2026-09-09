"""安全原语：JWT 签发/校验、密码哈希。"""

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import Settings, get_settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """对明文密码进行 bcrypt 哈希。"""
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文与哈希是否匹配。"""
    try:
        return _pwd_context.verify(plain, hashed)
    except ValueError:
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