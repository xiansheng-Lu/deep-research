"""编排检查点工厂（M2-2）：在 PostgresSaver 与应用级内存 Saver 之间收口。

设计口径（对齐《后端详细设计》§6.6）：

- ``CHECKPOINTER_BACKEND=postgres``（默认）：构造应用级单例
  ``AsyncPostgresSaver``（psycopg v3 连接池），启动时 ``setup()`` 建 LangGraph
  checkpoint 表；图以 ``thread_id=run_id`` 持久化线程状态，HITL 挂起可跨请求、
  跨进程恢复（澄清 / 分歧裁决共用）。
- ``CHECKPOINTER_BACKEND=memory``：使用进程级单例 ``InMemorySaver``，供无外部
  数据库的测试环境与 Postgres 暂不可用时兜底。注意内存 saver 不跨进程保留，
  仅保证「同一应用实例内首次执行与恢复执行共用同一 saver」——M1 每 run 新建
  一次性 saver 的挂起即失状态问题由此消除。

Postgres 初始化失败（容器未启动 / 连接失败）不阻断应用启动：记录异常日志后
回退内存 saver，研究主链仍可运行，仅失去跨进程恢复能力。
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger("orchestrator.checkpoint")

# 进程级单例内存 saver：兜底与测试环境复用同一实例，保证线程状态在请求间存活
_memory_saver: InMemorySaver | None = None

# psycopg 建连超时（秒）：启动期容器未就绪时快速失败回退，避免长时间阻塞应用启动
_CONNECT_TIMEOUT_S = 3


def get_memory_saver() -> InMemorySaver:
    """返回进程级单例内存检查点（懒构造）。"""
    global _memory_saver
    if _memory_saver is None:
        _memory_saver = InMemorySaver()
    return _memory_saver


def _to_psycopg_dsn(async_url: str) -> str:
    """把 SQLAlchemy 异步驱动 URL 转为 psycopg v3 可识别的 DSN。

    ``postgresql+asyncpg://user:pass@host:port/db`` →
    ``postgresql://user:pass@host:port/db``
    """
    return async_url.replace("+asyncpg", "", 1)


async def build_checkpointer(
    settings: Settings,
) -> tuple[BaseCheckpointSaver[Any], Any | None]:
    """构造应用级检查点与其关联资源。

    Returns:
        ``(saver, pool)``：memory 后端或回退场景下 ``pool`` 为 None；
        Postgres 后端返回 ``(AsyncPostgresSaver, AsyncConnectionPool)``，
        关闭时由调用方关闭连接池。
    """
    if settings.checkpointer_backend == "memory":
        log.info("使用应用级内存检查点（CHECKPOINTER_BACKEND=memory）")
        return get_memory_saver(), None

    # 延迟导入：memory 模式与未安装可选依赖的环境不触发 psycopg 导入
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    dsn = _to_psycopg_dsn(settings.db_async_url)
    pool: Any | None = None
    try:
        pool = AsyncConnectionPool(
            dsn,
            open=False,
            kwargs={"autocommit": True, "connect_timeout": _CONNECT_TIMEOUT_S},
        )
        await pool.open(wait=True)
        saver = AsyncPostgresSaver(pool)
        # 幂等创建 LangGraph checkpoint 系列表（checkpoints/checkpoint_writes/
        # checkpoint_blobs/migration 等），由 LangGraph 自建，不纳入 Alembic 迁移
        await saver.setup()
        log.info("Postgres 检查点就绪，HITL 状态支持跨请求恢复")
        return saver, pool
    except Exception as exc:  # noqa: BLE001 - 启动兜底：回退内存而非阻断启动
        log.warning(
            "Postgres 检查点初始化失败，回退应用级内存检查点（HITL 状态不跨进程保留）",
            extra={"err": repr(exc)},
        )
        if pool is not None:
            with suppress(Exception):
                await pool.close()
        return get_memory_saver(), None


async def close_checkpointer(pool: Any | None) -> None:
    """关闭检查点关联的 Postgres 连接池；内存 saver 无需释放。"""
    if pool is None:
        return
    with suppress(Exception):
        await pool.close()


__all__ = [
    "build_checkpointer",
    "close_checkpointer",
    "get_memory_saver",
]
