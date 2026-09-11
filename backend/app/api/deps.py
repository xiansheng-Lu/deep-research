"""路由层通用依赖：会话、当前用户、团队上下文等。

WP-2 引入 ``current_user`` 依赖：从 Bearer Token 中提取 user_id，
查 ORM 加载 User 并校验未注销。受保护路由通过 ``CurrentUser`` 类型注解注入。
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthError
from app.core.security import decode_token
from app.db.models.identity import User
from app.db.session import get_session

# 约定：JWT 的 ``sub`` 字段为 ULID(user.id)；``type`` 字段必须为 "access"。
_bearer_scheme = HTTPBearer(auto_error=False, description="JWT Bearer token")


async def db_session() -> AsyncIterator[AsyncSession]:
    """数据库会话依赖。路由层通过 Annotated 引用。"""
    async for session in get_session():
        yield session


DBSession = Annotated[AsyncSession, Depends(db_session)]


def _settings_dep() -> Settings:
    """配置依赖：便于测试 monkeypatch。"""
    return get_settings()


SettingsDep = Annotated[Settings, Depends(_settings_dep)]


async def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    session: DBSession,
    settings: SettingsDep,
) -> User:
    """从 Bearer Token 解析并加载当前用户。

    失败场景统一抛 ``AuthError``，由全局异常处理器映射为 401 响应。
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthError("缺少有效的 Bearer 凭证")

    try:
        payload = decode_token(credentials.credentials, settings=settings)
    except JWTError as exc:
        raise AuthError(f"令牌无效：{exc}") from exc

    if payload.get("type") != "access":
        raise AuthError("令牌类型错误，应为 access")

    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise AuthError("令牌缺少 sub 声明")

    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise AuthError("用户不存在")
    if user.deleted_at is not None:
        raise AuthError("用户已注销")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
