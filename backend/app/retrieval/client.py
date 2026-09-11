"""检索门面：主备 WebSearchProvider + 熔断 + 去重。"""

from __future__ import annotations

import logging
from dataclasses import replace

from app.core.config import Settings, get_settings
from app.core.exceptions import ExternalServiceError, ProviderUnavailableError
from app.provider.circuit_breaker import CircuitBreaker
from app.retrieval.base import RetrievalHit, RetrievalRequest
from app.retrieval.bocha import build_bocha_provider
from app.retrieval.dedup import dedupe
from app.retrieval.web_search import (
    WebSearchProvider,
    build_tavily_provider,
)

log = logging.getLogger("retrieval.client")


class RetrievalClient:
    """对外统一门面：主备 Provider、熔断、用量归集（todo）、调用去重。

    调用流程：
        ``search`` → 主 provider → 失败切备 → 失败抛 ExternalServiceError
        主失败时 trip 主 provider 熔断，避免雪崩
    """

    def __init__(
        self,
        *,
        primary: WebSearchProvider | None,
        backup: WebSearchProvider | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._primary = primary
        self._backup = backup
        self._breaker = breaker or CircuitBreaker()

    async def search(self, request: RetrievalRequest) -> list[RetrievalHit]:
        """执行检索并去重。"""
        if not self._primary and not self._backup:
            raise ProviderUnavailableError("未注册任何 Web 检索 Provider")
        provider = self._select_provider()
        try:
            hits = await provider.search(request)
            self._breaker.record_success(provider.name)
        except ExternalServiceError as exc:
            log.warning("主 Provider %s search 失败: %s", provider.name, exc)
            self._breaker.record_failure(provider.name)
            if self._backup is None or self._backup is provider:
                raise
            backup_hits = await self._backup.search(request)
            self._breaker.record_success(self._backup.name)
            return dedupe(backup_hits)
        return dedupe(hits)

    async def extract(self, hits: list[RetrievalHit]) -> list[RetrievalHit]:
        """对一组命中执行正文抽取（保留主备与熔断）。"""
        if not hits:
            return []
        provider = self._select_provider()
        try:
            extracted = await provider.extract(hits)
        except ExternalServiceError as exc:
            log.warning("主 Provider %s extract 失败: %s", provider.name, exc)
            self._breaker.record_failure(provider.name)
            if self._backup is None or self._backup is provider:
                return hits
            return await self._backup.extract(hits)
        return extracted

    def _select_provider(self) -> WebSearchProvider:
        if self._primary is None:
            if self._backup is None:
                raise ProviderUnavailableError("未注册任何 Web 检索 Provider")
            return self._backup
        if self._breaker.is_open(self._primary.name):
            if self._backup is None:
                raise ExternalServiceError(
                    f"主 Provider {self._primary.name} 已熔断，且无备用 Provider"
                )
            return self._backup
        return self._primary

    async def aclose(self) -> None:
        for provider in (self._primary, self._backup):
            if provider is None:
                continue
            try:
                await provider.aclose()
            except Exception as exc:  # noqa: BLE001 - 关闭不强错
                log.warning("Provider aclose 异常: %s", exc)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> RetrievalClient | None:
        """按 ``WEB_SEARCH_PROVIDER`` 构造主检索客户端。

        - ``bocha``（默认）：构造 BochaProvider；
        - ``tavily``：构造 TavilyProvider。

        对应供应方未配置密钥时返回 ``None``，由调用方（lifespan / 节点）
        明确走"检索不可用"路径，不构造 search 时才报错的空壳客户端。
        """
        cfg = settings or get_settings()
        provider_name = (cfg.web_search_provider or "bocha").lower()
        if provider_name == "bocha":
            primary = build_bocha_provider(cfg)
        elif provider_name == "tavily":
            primary = build_tavily_provider(cfg)
        else:
            log.warning("未知 WEB_SEARCH_PROVIDER=%s，未构造检索客户端", provider_name)
            primary = None
        if primary is None:
            log.warning(
                "公域检索 Provider %s 未配置有效密钥，检索节点将走失败路径",
                provider_name,
            )
            return None
        return cls(primary=primary)


_default_client: RetrievalClient | None = None


def get_default_client() -> RetrievalClient:
    """惰性构造默认客户端；无可用 Provider 时抛 ``ProviderUnavailableError``。"""
    global _default_client
    if _default_client is None:
        client = RetrievalClient.from_settings()
        if client is None:
            raise ProviderUnavailableError("未配置任何可用的 Web 检索 Provider")
        _default_client = client
    return _default_client


def _apply_fingerprint(hit: RetrievalHit, fp: str) -> RetrievalHit:
    """把 fingerprint 写回 ``raw``，避免 dataclass 字段再次扩展。"""
    raw = dict(hit.raw or {})
    raw["fingerprint"] = fp
    return replace(hit, raw=raw)
