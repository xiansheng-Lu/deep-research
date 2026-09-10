"""鉴权路由：登录、刷新、登出、当前用户。

对齐 LLD §6.2 / §8.2；M1 阶段交付的 4 个端点：
- ``POST /auth/login``：邮箱+密码登录，签发 access/refresh 双令牌
- ``POST /auth/refresh``：用 refresh token 换发新 access token
- ``POST /auth/logout``：登出（当前为软登出，仅回写 ``last_login_at`` 审计锚点；
  refresh token 黑名单留到 M2 接入 Redis 后落地）
- ``GET  /auth/me``：返回当前登录用户视图

令牌签发 / 校验 / 密码哈希统一走 ``app.core.security``；
异常统一抛 ``AuthError``（401），由全局异常处理器映射。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from jose import JWTError
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession, SettingsDep
from app.core.config import Settings
from app.core.exceptions import AuthError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.db.models.identity import User
from app.schemas.auth import (
    CurrentUser as CurrentUserSchema,
)
from app.schemas.auth import LoginRequest, RefreshRequest, TokenPair

router = APIRouter(prefix="/auth", tags=["auth"])


def _build_token_pair(user: User, settings: Settings) -> TokenPair:
    """基于已认证用户签发 access + refresh 双令牌。"""
    access = create_access_token(user.id, settings=settings)
    refresh = create_refresh_token(user.id, settings=settings)
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.jwt_access_ttl_minutes * 60,
    )


@router.post("/login", response_model=TokenPair, summary="邮箱密码登录")
async def login(payload: LoginRequest, session: DBSession, settings: SettingsDep) -> TokenPair:
    """校验邮箱 + 密码，签发访问/刷新令牌。"""
    user = await session.scalar(select(User).where(User.email == payload.email))
    if user is None or user.deleted_at is not None:
        # 不区分"用户不存在"与"密码错误"，避免账户枚举
        raise AuthError("邮箱或密码错误")
    if not verify_password(payload.password, user.hashed_password):
        raise AuthError("邮箱或密码错误")

    # 回写最近登录时间（审计锚点；不抛错）
    user.last_login_at = datetime.now(tz=timezone.utc)
    await session.flush()

    return _build_token_pair(user, settings)


@router.post("/refresh", response_model=TokenPair, summary="刷新访问令牌")
async def refresh(payload: RefreshRequest, session: DBSession, settings: SettingsDep) -> TokenPair:
    """用 refresh token 换发新的 access token（refresh token 同时轮换）。"""
    try:
        claims = decode_token(payload.refresh_token, settings=settings)
    except JWTError as exc:
        raise AuthError(f"刷新令牌无效：{exc}") from exc

    if claims.get("type") != "refresh":
        raise AuthError("令牌类型错误，应为 refresh")

    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise AuthError("刷新令牌缺少 sub 声明")

    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None or user.deleted_at is not None:
        raise AuthError("用户不存在")

    return _build_token_pair(user, settings)


@router.post("/logout", summary="登出（M1 软登出）")
async def logout(current_user: CurrentUser, session: DBSession) -> dict[str, str]:
    """软登出：仅同步 ``last_login_at`` 作为审计锚点。

    注：M1 不引入 refresh token 黑名单（需 Redis）；前端清理本地令牌即可。
    M2 接入 Redis 后会补充黑名单校验。
    """
    current_user.last_login_at = datetime.now(tz=timezone.utc)
    await session.flush()
    return {"status": "ok"}


@router.get("/me", response_model=CurrentUserSchema, summary="当前用户")
async def me(current_user: CurrentUser) -> CurrentUserSchema:
    """返回当前登录用户的视图。"""
    return CurrentUserSchema(
        id=current_user.id,
        team_id=current_user.team_id,
        email=current_user.email,
        display_name=current_user.display_name,
        role=current_user.role,
        last_login_at=current_user.last_login_at,
    )
