"""``app.core.security`` 纯函数测试：密码哈希与 JWT 签发/解码往返。

不依赖数据库；通过 ``monkeypatch`` 注入最小配置。
"""

from __future__ import annotations

import time

import pytest

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_hash_password_produces_bcrypt_hash() -> None:
    """bcrypt 哈希应以 ``$2`` 开头且每次结果不同。"""
    plain = "correct-horse-battery-staple"
    h1 = hash_password(plain)
    h2 = hash_password(plain)
    assert h1.startswith("$2")
    assert h1 != h2, "bcrypt 应该用随机盐，相同明文应产出不同哈希"


def test_verify_password_roundtrip() -> None:
    """``verify_password`` 对正确密码返回 True，对错误密码返回 False。"""
    h = hash_password("pw-123")
    assert verify_password("pw-123", h) is True
    assert verify_password("wrong", h) is False


def test_verify_password_handles_invalid_hash() -> None:
    """非法哈希应被 ``verify_password`` 捕获并返回 False（不抛）。"""
    assert verify_password("any", "not-a-bcrypt-hash") is False


def test_create_access_token_includes_claims() -> None:
    """访问令牌应含 ``sub/iat/exp/type=access``。"""
    token = create_access_token("user-123")
    claims = decode_token(token)
    assert claims["sub"] == "user-123"
    assert claims["type"] == "access"
    assert claims["iat"] <= claims["exp"]
    assert claims["exp"] - claims["iat"] == 30 * 60  # 默认 30 分钟


def test_create_refresh_token_has_longer_ttl() -> None:
    """刷新令牌 type=refresh 且 TTL 长于 access。"""
    access = create_access_token("user-123")
    refresh = create_refresh_token("user-123")
    a = decode_token(access)
    r = decode_token(refresh)
    assert r["type"] == "refresh"
    assert r["exp"] - a["exp"] > 0, "refresh 应晚于 access 过期"


def test_decode_token_rejects_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    """将 access TTL 调到 0 后签发，刷新后立即过期，``decode_token`` 应抛。"""
    cfg = get_settings()
    monkeypatch.setattr(cfg, "jwt_access_ttl_minutes", 0)
    token = create_access_token("user-123", settings=cfg)
    # 等待 1 秒确保 iat != exp（TTL=0 时 exp 与 iat 相同，可能仍被认为有效）
    time.sleep(1.1)
    with pytest.raises(Exception):  # jose.JWTError
        decode_token(token, settings=cfg)


def test_decode_token_rejects_bad_signature() -> None:
    """篡改签名的 token 应被拒绝。"""
    token = create_access_token("user-123")
    tampered = token[:-2] + ("AB" if token[-2:] != "AB" else "CD")
    with pytest.raises(Exception):
        decode_token(tampered)


def test_extra_claims_are_merged() -> None:
    """``create_access_token(extra_claims=...)`` 应把自定义声明合并进 payload。"""
    token = create_access_token("user-123", extra_claims={"role": "admin"})
    claims = decode_token(token)
    assert claims["role"] == "admin"
    assert claims["sub"] == "user-123"  # 不会被 extra 覆盖
