"""pytest 公共 fixture：settings 隔离、临时 .env、后续模块挂载点。

测试环境约束：
    - 不依赖真实 Postgres / Redis / 对象存储（CI 无外部依赖）
    - 通过 monkeypatch + tmp_path 注入最小可用配置，保证 Settings() 可构造
    - 各业务模块的 fixture（DB session、auth client 等）按 WP 增量补在本目录
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings, get_settings

# 测试环境最小变量集：覆盖 Settings() 强制字段，保证 get_settings() 不抛
_REQUIRED_ENV: dict[str, str] = {
    "APP_ENV": "dev",
    "SECRET_KEY": "pytest-secret-key-do-not-use-in-prod",
    "DB_ASYNC_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
    "DB_SYNC_URL": "postgresql://test:test@localhost:5432/test",
    "DB_POOL_SIZE": "10",
    "DB_MAX_OVERFLOW": "20",
    # 测试无外部 Postgres 依赖：lifespan 检查点直接走应用级内存 saver
    "CHECKPOINTER_BACKEND": "memory",
    "REDIS_URL": "redis://localhost:6379/0",
    "CELERY_BROKER_URL": "redis://localhost:6379/0",
    "CELERY_RESULT_BACKEND": "redis://localhost:6379/0",
    "OBJECT_STORAGE_ENDPOINT": "localhost:9000",
    "OBJECT_STORAGE_ACCESS_KEY": "test",
    "OBJECT_STORAGE_SECRET_KEY": "test-secret",
    "OBJECT_STORAGE_BUCKET": "test-bucket",
    # M2-6：离线测试不发真实页面抓取；补采行为在 test_page_metadata 专门开启
    "SOURCE_PAGE_METADATA_ENABLED": "false",
}


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个测试自动注入最小配置并清缓存，避免模块间状态泄漏。"""
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    """当前测试可见的 Settings 实例（lru_cache 已清）。"""
    return get_settings()


@pytest.fixture
def tmp_env_file(tmp_path: Path) -> Path:
    """提供一个空 .env 路径，便于校验 Settings 读取 .env 的逻辑。"""
    return tmp_path / ".env"


def create_all_in_schema(sync_conn: Any, schema_name: str) -> None:
    """在指定 schema 内创建全部 ORM 表（集成测试隔离用）。

    ``MetaData.create_all`` 的 checkfirst 经 Inspector 检查时默认取方言默认
    schema（Postgres=public），即使连接 search_path 指向独立 schema，也会因
    public 存在同名表而跳过建表，导致测试数据实际写入 public。此处临时为每张
    表打上目标 schema 再建表，外键随目标表一起 schema 限定，建完立即还原。
    """
    from app.db.base import Base

    tables = Base.metadata.sorted_tables
    originals = [(table, table.schema) for table in tables]
    try:
        for table in tables:
            table.schema = schema_name
        Base.metadata.create_all(sync_conn, checkfirst=True)
    finally:
        for table, original in originals:
            table.schema = original
