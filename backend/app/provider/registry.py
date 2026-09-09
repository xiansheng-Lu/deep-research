"""Provider 注册表：集中管理主备 Provider 实例。

具体实现（OpenAI / Anthropic / 自建网关）由 ``concrete/`` 子包提供，
本模块只负责构造主备配对供 ``LLMClient`` 使用。
"""

from typing import Any

from app.core.config import get_settings
from app.provider.base import LLMProvider

default_registry: "_ProviderRegistry" = _ProviderRegistry()


class _ProviderRegistry:
    """简单的 Provider 注册表占位。"""

    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}

    def register(self, name: str, provider: LLMProvider) -> None:
        self._providers[name] = provider

    def get(self, name: str) -> LLMProvider | None:
        return self._providers.get(name)

    def build_pair(self, alias: str) -> tuple[LLMProvider | None, LLMProvider | None]:
        """根据别名构造 (主, 备) Provider 配对。

        占位实现：M1 阶段按 ``LLM_PRIMARY_*`` 与 ``LLM_BACKUP_*`` 构造；
        当前返回空 tuple，让上层根据空状态抛 ProviderUnavailableError。
        """
        _ = alias, get_settings()  # 保留引用，M1 阶段实现
        return (self._providers.get("primary"), self._providers.get("backup"))


_ = Any  # 防止未使用导入告警