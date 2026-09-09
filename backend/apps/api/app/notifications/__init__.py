"""通知中心：站内通知 + 可选的邮件 / Webhook 投递。"""

from app.notifications.base import Notification, NotificationChannel

__all__ = ["Notification", "NotificationChannel"]