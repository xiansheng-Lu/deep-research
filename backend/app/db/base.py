"""ORM 基类与公共列。

所有业务模型继承自 Base；多租户字段（team_id / created_at / updated_at）
统一在此声明，避免各模型重复实现。

主键约定遵循 LLD §5.1：CHAR(26) ULID，由应用层生成（``python-ulid``）。
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from ulid import ULID

# 命名约定便于 Alembic 生成可读的迁移
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def new_ulid() -> str:
    """生成主键：26 字符 ULID（时间序）。"""
    return str(ULID())


class Base(DeclarativeBase):
    """所有 ORM 模型的声明基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IdMixin:
    """主键列：ULID ``CHAR(26)``，全局唯一、时间序。"""

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_ulid)


class TimestampMixin:
    """时间戳列：创建时间、更新时间（UTC）。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )