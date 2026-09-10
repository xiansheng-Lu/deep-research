"""鉴权相关 Pydantic Schema。

对齐 LLD §6.2 / §8.2；M1 阶段支撑登录、刷新、登出、当前用户 4 个端点。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """``POST /auth/login`` 请求体。"""

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    """``POST /auth/refresh`` 请求体（纯 JSON 方案，不依赖 Cookie）。"""

    refresh_token: str = Field(min_length=1)


class TokenPair(BaseModel):
    """访问令牌 + 刷新令牌对（login / refresh 响应）。"""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="访问令牌剩余秒数")


class CurrentUser(BaseModel):
    """当前登录用户视图。"""

    id: str
    team_id: str
    email: EmailStr
    display_name: str
    role: str
    last_login_at: datetime | None = None
