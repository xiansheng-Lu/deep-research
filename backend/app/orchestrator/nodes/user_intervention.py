"""用户介入节点：通过 LangGraph interrupt 暂停并等待人类反馈。"""

from typing import Any

from app.orchestrator.state import ResearchState


async def run(state: ResearchState) -> dict[str, Any]:
    """用户介入占位：M1 阶段经 LangGraph ``interrupt`` 暂停并消费 ``state.human_input``（§6.5.9）。

    介入完成后并入 report（§6.3 固定边）。
    """
    return {}