"""编排层节点共享依赖（§6.4 NodeDeps）。

M1 节点签名升级目标：``async def <node>(state: ResearchState, *, deps: NodeDeps) -> dict``。
M0 占位阶段（图节点仍以无 deps 形式调用）保留向后兼容：``deps`` 缺省视为 ``None``，
节点内部按需 ``getattr(deps, "llm", None)`` 走降级路径。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.provider.client import LLMClient


@dataclass(slots=True)
class NodeDeps:
    """节点运行时依赖（最小可用版本）。

    Attributes:
        run_id: 当前研究运行 ID。
        team_id: 所属租户 ID。
        trace_id: 链路追踪 ID。
        llm: LLM 客户端门面；缺省时节点走无 LLM 降级路径（占位返回）。
    """

    run_id: str
    team_id: str
    trace_id: str
    llm: LLMClient | None = None

    def safe(self) -> dict[str, Any]:
        """返回便于日志输出的轻量字典（不暴露敏感字段）。"""
        return {
            "run_id": self.run_id,
            "team_id": self.team_id,
            "trace_id": self.trace_id,
            "has_llm": self.llm is not None,
        }


__all__ = ["NodeDeps"]
