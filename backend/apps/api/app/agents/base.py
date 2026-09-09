"""智能体公共契约：输入上下文与输出结果的数据结构。"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass(slots=True)
class AgentContext:
    """调用智能体时传入的上下文。

    Attributes:
        run_id: 当前研究执行 ID。
        project_id: 所属项目 ID。
        user_id: 发起用户 ID。
        tier: 成本档位（quick / standard / deep / extreme）。
        payload: 智能体输入数据，随智能体类型变化。
        trace_id: 链路追踪 ID。
    """

    run_id: UUID
    project_id: UUID
    user_id: UUID
    tier: str
    payload: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None


@dataclass(slots=True)
class AgentResult:
    """智能体执行结果。

    Attributes:
        agent: 智能体名称。
        patches: 写回 LangGraph 状态的部分字段。
        usage: 消耗的 token 等计量。
    """

    agent: str
    patches: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)