"""外部连接器：OAuth 绑定、同步调度、统一 HTTP 客户端。"""

from app.connectors.base import Connector, ConnectorMeta

__all__ = ["Connector", "ConnectorMeta"]