"""路由层通用依赖：会话、当前用户、团队上下文等。"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session


async def db_session() -> AsyncIterator[AsyncSession]:
    """数据库会话依赖。路由层通过 Annotated 引用。"""
    async for session in get_session():
        yield session


DBSession = Annotated[AsyncSession, Depends(db_session)]