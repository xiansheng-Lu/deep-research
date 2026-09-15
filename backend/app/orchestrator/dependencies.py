"""编排层节点共享依赖（§6.4 NodeDeps）。

M1 节点签名升级目标：``async def <node>(state: ResearchState, *, deps: NodeDeps) -> dict``。
M0 占位阶段（图节点仍以无 deps 形式调用）保留向后兼容：``deps`` 缺省视为 ``None``，
节点内部按需 ``getattr(deps, "llm", None)`` 走降级路径。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.provider.client import LLMClient
    from app.realtime.hub import RealtimeHub
    from app.retrieval.client import RetrievalClient


@dataclass(slots=True)
class NodeDeps:
    """节点运行时依赖（最小可用版本）。

    Attributes:
        run_id: 当前研究运行 ID。
        team_id: 所属租户 ID。
        trace_id: 链路追踪 ID。
        llm: LLM 客户端门面；缺省时节点走无 LLM 降级路径（占位返回）。
        retrieval_client: 检索门面；缺省时节点回退到全局 ``get_default_client()``。
        db_session: 异步 SQLAlchemy 会话；缺省时 ``researcher_fan_out`` 仅写入 state，
            不直接落库（外层编排可在 checkpoint 时统一持久化）。
        hub: 实时事件总线；critic 检出 high 冲突逐条推送 ``conflict.detected``、
            await_human 回流时推送 ``conflict.verdicts``；缺省不推送。
        report_assembly: M2-7 reporter 节点写回的结构化终稿装配产物
            （reporting.blocks.ReportAssembly：blocks/outline/audit/引文行），
            run 级进程内通道，不入 ResearchState；executor 据此落 final 报告。
    """

    run_id: str
    team_id: str
    trace_id: str
    llm: LLMClient | None = None
    retrieval_client: RetrievalClient | None = None
    db_session: AsyncSession | None = None
    hub: RealtimeHub | None = None
    report_assembly: Any = None

    def safe(self) -> dict[str, Any]:
        """返回便于日志输出的轻量字典（不暴露敏感字段）。"""
        return {
            "run_id": self.run_id,
            "team_id": self.team_id,
            "trace_id": self.trace_id,
            "has_llm": self.llm is not None,
            "has_retrieval": self.retrieval_client is not None,
            "has_db_session": self.db_session is not None,
            "has_hub": self.hub is not None,
        }


__all__ = ["NodeDeps"]
