"""通知分发器占位：M1 阶段实现站内通知表写入与可选邮件投递。"""

from app.notifications.base import Notification


class NotificationDispatcher:
    """通知分发门面占位。"""

    async def send(self, notif: Notification) -> bool:
        """占位实现：返回 True。M1 阶段落 ORM 与可选外部渠道。"""
        _ = notif
        return True