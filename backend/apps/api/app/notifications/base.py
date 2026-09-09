"""通知数据结构。"""

from dataclasses import dataclass
from enum import StrEnum


class NotificationChannel(StrEnum):
    """通知渠道。"""

    IN_APP = "in_app"
    EMAIL = "email"
    WEBHOOK = "webhook"


@dataclass(slots=True)
class Notification:
    """单条通知。"""

    recipient_id: str
    channel: NotificationChannel
    title: str
    body: str
    payload: dict[str, str] | None = None