"""pytest 框架烟测：验证 conftest 与异步模式工作正常。"""

from __future__ import annotations

import pytest

from app.core.config import Settings, get_settings


def test_settings_loads_in_test_env(settings: Settings) -> None:
    assert settings.app_env == "dev"
    assert settings.secret_key.get_secret_value() == "pytest-secret-key-do-not-use-in-prod"


def test_get_settings_is_singleton_after_cache_clear() -> None:
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


@pytest.mark.asyncio
async def test_async_fixture_works() -> None:
    # 异步模式烟测：后续 WP-1 / WP-5 单测都依赖此项
    import asyncio
    await asyncio.sleep(0)
    assert True