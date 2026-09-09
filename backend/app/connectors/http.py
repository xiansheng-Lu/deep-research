"""HTTP 客户端占位：M1 阶段基于 httpx 实现鉴权头注入与限流。"""

from typing import Any


class HTTPClient:
    """HTTP 客户端门面占位。"""

    def __init__(self) -> None:
        self._default_timeout = 30.0

    async def get(self, url: str, **kwargs: Any) -> dict[str, Any]:
        """占位：返回空字典。"""
        _ = (url, kwargs)
        return {}

    async def post(self, url: str, **kwargs: Any) -> dict[str, Any]:
        """占位：返回空字典。"""
        _ = (url, kwargs)
        return {}