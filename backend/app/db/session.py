"""异步会话管理：提供 FastAPI 依赖与显式上下文管理器两种入口。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


class SessionFactory:
    """会话工厂封装：单例持有 engine 与 sessionmaker，便于依赖注入与测试替换。"""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    def maker(self) -> async_sessionmaker[AsyncSession]:
        return self._sessionmaker


def init_engine(settings: Settings | None = None) -> SessionFactory:
    """初始化全局引擎与工厂。仅在应用启动时调用一次。"""
    global _engine, _session_factory
    cfg = settings or get_settings()
    _engine = create_async_engine(
        cfg.db_async_url,
        pool_size=cfg.db_pool_size,
        max_overflow=cfg.db_max_overflow,
        pool_pre_ping=True,
        future=True,
    )
    _session_factory = SessionFactory(_engine)
    return _session_factory


def get_engine() -> AsyncEngine:
    if _engine is None:  # pragma: no cover - 应用未启动
        raise RuntimeError("数据库引擎尚未初始化，请先调用 init_engine()")
    return _engine


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每次请求提供一个会话。

    统一承载请求级事务边界：路由内只做 ``flush``，请求正常结束时由本依赖
    统一提交；路由抛出任何异常（含业务 AppError）时回滚，避免脏写落库。
    """
    if _session_factory is None:  # pragma: no cover - 应用未启动
        raise RuntimeError("会话工厂尚未初始化，请先调用 init_engine()")
    async with _session_factory.maker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """供后台任务与脚本使用的显式上下文。"""
    if _session_factory is None:  # pragma: no cover
        raise RuntimeError("会话工厂尚未初始化，请先调用 init_engine()")
    async with _session_factory.maker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def shutdown_engine() -> None:
    """关闭数据库连接池（应用关闭时调用）。"""
    global _engine, _session_factory
    if _engine is not None:
        # dispose 是同步方法，但需要通过 async 适配器运行
        _engine.sync_engine.dispose()  # type: ignore[attr-defined]
    _engine = None
    _session_factory = None