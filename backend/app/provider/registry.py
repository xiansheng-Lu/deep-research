"""Provider 注册表：集中管理主备 Provider 实例。

M1 阶段：通过 ``build_pair`` 从 Settings 配置构造 OpenAI 主备配对。
后续 M2+：可扩展为多 Provider 注册 + 按 alias 选择。
"""

from typing import Any

from app.core.config import get_settings
from app.provider.base import LLMProvider


class _ProviderRegistry:
    """Provider 注册表：持有已注册实例，支持按 alias 构造主备配对。"""

    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}

    def register(self, name: str, provider: LLMProvider) -> None:
        self._providers[name] = provider

    def get(self, name: str) -> LLMProvider | None:
        return self._providers.get(name)

    def build_pair(self, alias: str) -> tuple[LLMProvider | None, LLMProvider | None]:
        """根据别名构造 (主, 备) Provider 配对。

        M1 阶段：按 ``LLM_PRIMARY_*`` / ``LLM_BACKUP_*`` 配置构造 OpenAI Provider；
        若已注册则优先使用已注册实例。
        """
        _ = alias  # M2 扩展 alias 路由时使用

        # 优先使用已注册实例
        primary = self._providers.get("primary")
        backup = self._providers.get("backup")

        # 若未注册，从 Settings 动态构造
        if primary is None:
            try:
                from app.provider.openai import build_openai_provider
                primary = build_openai_provider()
            except Exception:
                pass  # 配置缺失时不构造，上层会抛 ProviderUnavailableError

        if backup is None:
            try:
                from app.provider.openai import build_openai_backup_provider
                backup = build_openai_backup_provider()
            except Exception:
                pass

        return (primary, backup)


default_registry = _ProviderRegistry()

_ = Any  # 防止未使用导入告警