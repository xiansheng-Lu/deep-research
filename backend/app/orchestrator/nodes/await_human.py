"""等待人类节点：阻塞直到用户对澄清问题或冲突裁决作答。

M2-2 critique 回流（FR-5 / FR-11）：

- 图以 ``interrupt_before=["await_human"]`` 在本节点之前挂起；恢复执行后本节点
  从 ``state.human_input.answers.verdicts`` 解析裁决集合
  （key=conflict_id，值含 choice/reason/additional_note/user_id），追加到
  ``state.verdicts``（按 conflict_id 去重，重放安全），并推送一条
  ``conflict.verdicts`` WS 帧；随后条件边 ``decide_after_await_human`` 按
  ``interrupt_reason="critique"`` 回流 critic 做四值收敛。
- clarify 分支只负责保持 ``interrupt_reason``，澄清答案由 clarifier 自行读取
  ``human_input`` 合并。
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.orchestrator.dependencies import NodeDeps
from app.orchestrator.state import ResearchState, VerdictDict

log = get_logger("orchestrator.await_human")


def _parse_verdicts(state: ResearchState) -> list[VerdictDict]:
    """从 human_input.answers.verdicts 解析尚未入 state 的裁决列表。

    裁决答案形态（对齐前端 HumanInputAnswers）：
    ``{"verdicts": {"<conflict_id>": {"choice": "...", "reason": "...",
    "additional_note": "...", "user_id": "..."}}}``；user_id 由恢复服务端
    按当前登录用户注入（Task 8），缺省为空串。
    """
    human_input = state.get("human_input") or {}
    answers = human_input.get("answers") if isinstance(human_input, dict) else None
    raw_verdicts = answers.get("verdicts") if isinstance(answers, dict) else None
    if not isinstance(raw_verdicts, dict):
        return []

    existing_ids = {v["conflict_id"] for v in (state.get("verdicts") or [])}
    parsed: list[VerdictDict] = []
    for conflict_id, answer in raw_verdicts.items():
        if not isinstance(conflict_id, str) or conflict_id in existing_ids:
            continue
        if not isinstance(answer, dict):
            continue
        verdict: VerdictDict = {
            "conflict_id": conflict_id,
            "user_id": str(answer.get("user_id") or ""),
            "choice": str(answer.get("choice") or ""),
            "reason": answer.get("reason"),
        }
        if answer.get("additional_note") is not None:
            verdict["additional_note"] = answer.get("additional_note")
        parsed.append(verdict)
    return parsed


async def _publish_verdicts(
    deps: NodeDeps | None,
    *,
    run_id: str,
    verdicts: list[VerdictDict],
) -> None:
    """回流时推送一条 conflict.verdicts 帧（FR-5）；hub 缺省/失败不阻断主链路。"""
    hub = getattr(deps, "hub", None) if deps is not None else None
    if hub is None or not verdicts:
        return
    event = {
        "type": "conflict.verdicts",
        "run_id": run_id,
        "payload": {
            "verdicts": [
                {
                    "conflict_id": v["conflict_id"],
                    "choice": v.get("choice") or "",
                }
                for v in verdicts
            ]
        },
    }
    try:
        await hub.publish(f"runs:{run_id}", event)
    except Exception as exc:  # noqa: BLE001 - 实时事件失败不阻断恢复
        log.warning(
            "conflict.verdicts 推送失败",
            extra={
                "run_id": run_id,
                "conflict_ids": [v["conflict_id"] for v in verdicts],
                "error": repr(exc),
            },
        )


async def run(state: ResearchState, *, deps: NodeDeps | None = None) -> dict[str, Any]:
    """等待人类节点入口。

    返回 ``interrupt_reason`` 供条件边回流（LangGraph 无 reducer，verdicts
    必须返回历史全集 + 本跳新增，覆盖式回写）。
    """
    reason = str(state.get("interrupt_reason") or "critique")
    if reason != "critique":
        return {"interrupt_reason": "clarify"}

    run_id = str(state.get("run_id") or "")
    new_verdicts = _parse_verdicts(state)
    all_verdicts: list[VerdictDict] = list(state.get("verdicts") or []) + new_verdicts
    await _publish_verdicts(deps, run_id=run_id, verdicts=new_verdicts)
    return {"interrupt_reason": "critique", "verdicts": all_verdicts}


__all__ = ["run"]
