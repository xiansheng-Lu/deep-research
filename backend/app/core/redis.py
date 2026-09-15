"""异步 Redis 客户端工厂（M2-8b）。

API 进程与 worker 进程各自在生命周期内持有一个 ``redis.asyncio`` 客户端；
不做全局单例（应用工厂/多 worker 测试需要独立实例）。无 Redis 回退由调用方
（Hub/registry）按 ``worker_enable_redis`` 决定，本模块只负责建/关连接。
"""

from __future__ import annotations

from redis.asyncio import Redis, from_url

from app.core.config import Settings


def build_redis(settings: Settings) -> Redis:
    """按配置构造异步 Redis 客户端（decode_responses=True 直接拿 str）。"""
    # redis-py 5.3 的 from_url 缺返回注解且 **kwargs 无类型，显式忽略这两条库侧缺口
    return from_url(  # type: ignore[no-untyped-call, no-any-return]
        settings.redis_url, encoding="utf-8", decode_responses=True
    )


async def close_redis(client: Redis | None) -> None:
    """关闭客户端，吞掉关闭期异常（与 lifespan 其他资源清理一致）。"""
    if client is None:
        return
    await client.aclose()
