"""知识域：``KnowledgeItem`` 是团队沉淀的研究产物；``KnowledgeEmbedding`` 存向量。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class KnowledgeItem(Base, IdMixin, TimestampMixin):
    """知识条目：跨研究复用的证据片段 / 报告段落 / 外部文档 / 用户笔记。"""

    __tablename__ = "knowledge_items"

    team_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String(26), nullable=True, index=True)
    created_by: Mapped[str] = mapped_column(
        String(26), ForeignKey("users.id"), nullable=False
    )
    type: Mapped[Literal[
        "evidence", "report_section", "external_doc", "user_note"
    ]] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    visibility: Mapped[Literal["private", "team", "project"]] = mapped_column(
        String(16), nullable=False, default="project"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class KnowledgeEmbedding(Base, IdMixin):
    """知识条目向量切片（pgvector ``vector(1536)``，ANN 索引 HNSW）。

    复合主键（``item_id`` + ``chunk_index``）由 ``IdMixin`` 之外的显式 PK 提供，
    这里额外声明主键覆盖 ``id``，避免继承自 ``IdMixin`` 的 ULID 单列主键冲突。
    """

    __tablename__ = "knowledge_embeddings"

    item_id: Mapped[str] = mapped_column(
        String(26),
        ForeignKey("knowledge_items.id"),
        primary_key=True,
    )
    chunk_index: Mapped[int] = mapped_column(primary_key=True)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=True)

    __table_args__ = (
        Index(
            "ix_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
    )