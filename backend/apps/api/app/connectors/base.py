"""连接器公共契约。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ConnectorMeta:
    """连接器元数据。"""

    name: str
    display_name: str
    auth_type: str  # api_key / oauth2 / none
    scopes: list[str]


class Connector(ABC):
    """所有外部系统连接器实现该抽象。"""

    meta: ConnectorMeta

    @abstractmethod
    async def fetch(self, *, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """抓取外部数据并以统一 schema 返回。"""