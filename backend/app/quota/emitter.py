"""实时成本发射器（M2-3）。

在执行器流式驱动循环内对每份 state 快照调用一次 ``observe``，承担三件事：

1. ``token.usage.update`` WS 帧：距上一帧不少于 ``COST_UPDATE_INTERVAL_MS``
   且用量发生变化时发送 ``{used, budget}``；``force=True``（终态/挂起点）
   无视节流补发收尾帧，保证看板终值与 ``research_runs.token_used`` 一致。
2. ``cost.warning`` WS 帧：内部 level 状态机
   ``None → warning(ratio≥0.70) → danger(ratio>0.90)``，每级至多发一次；
   单跳越过 0.90 只发 danger（不补 warning）。
3. 写穿：同步更新 ``ResearchRun.token_used``，随执行器的 super-step 提交
   对 REST 成本快照可见。

阈值统一经 ``app.quota.tiers`` 的访问函数读取，与 90% 挂起条件边同源。
帧发布失败一律吞错只告警，不阻断研究主链（语义同 conflict.detected）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Final

from app.core.config import get_settings
from app.core.logging import get_logger
from app.quota.tiers import classify_cost_level, cost_danger_ratio, cost_warning_ratio

if TYPE_CHECKING:
    from app.db.models.run import ResearchRun
    from app.realtime.hub import RealtimeHub

log = get_logger("quota.emitter")

# 预警级别单调序：None < warning < danger
_LEVEL_RANK: Final[dict[str, int]] = {"warning": 1, "danger": 2}


class RunCostEmitter:
    """单次 run 的成本发射状态机（非线程安全；随 run 协程内串行使用）。"""

    def __init__(
        self,
        *,
        hub: RealtimeHub | None,
        run: ResearchRun,
        update_interval_ms: int | None = None,
        warning_ratio: float | None = None,
        danger_ratio: float | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        settings = get_settings()
        self._hub = hub
        self._run = run
        self._run_id = run.id
        self._budget = int(run.token_budget)
        self._interval_s = (
            settings.cost_update_interval_ms if update_interval_ms is None else update_interval_ms
        ) / 1000.0
        self._warning_ratio = cost_warning_ratio() if warning_ratio is None else warning_ratio
        self._danger_ratio = cost_danger_ratio() if danger_ratio is None else danger_ratio
        self._clock = clock or time.monotonic
        # 已发射级别（None/warning/danger）与节流记账
        self._level: str | None = None
        self._last_frame_ts: float | None = None
        self._last_frame_used: int | None = None

    def ratio_of(self, used: int) -> float:
        """用量占预算比例；预算恒正，仍做除零保护。"""
        if self._budget <= 0:
            return 0.0
        return used / self._budget

    def level_for(self, ratio: float) -> str | None:
        """按阈值派生级别（与 cost/snapshot 共用 ``classify_cost_level``）。"""
        return classify_cost_level(
            ratio,
            warning_ratio=self._warning_ratio,
            danger_ratio=self._danger_ratio,
        )

    async def observe(self, used: int, *, force: bool = False) -> None:
        """处理一份快照的累计用量：写穿 run 行 + 边沿预警 + 节流 token 帧。"""
        used = int(used)
        ratio = self.ratio_of(used)

        # 1) 写穿 run 行（随执行器 super-step commit 对 REST 可见）
        if self._run.token_used != used:
            self._run.token_used = used

        # 2) cost.warning 边沿状态机：只允许升级，每级一次
        target = self.level_for(ratio)
        if target is not None and (
            self._level is None or _LEVEL_RANK[target] > _LEVEL_RANK.get(self._level, 0)
        ):
            self._level = target
            await self._publish(
                {
                    "type": "cost.warning",
                    "payload": {
                        "level": target,
                        "used": used,
                        "budget": self._budget,
                        "ratio": ratio,
                    },
                }
            )

        # 3) token.usage.update 节流：用量未变化的快照不发（检索期无 LLM 增量），
        #    force（终态/挂起点）无视节流与去重补发，保证收尾帧精确
        now = self._clock()
        due = self._last_frame_ts is None or (now - self._last_frame_ts) >= self._interval_s
        if force or (due and self._last_frame_used != used):
            self._last_frame_ts = now
            self._last_frame_used = used
            await self._publish(
                {
                    "type": "token.usage.update",
                    "payload": {"used": used, "budget": self._budget},
                }
            )

    async def _publish(self, event: dict[str, object]) -> None:
        """投递 WS 帧；hub 缺省或推送失败均不阻断研究主链。"""
        if self._hub is None:
            return
        try:
            await self._hub.publish(f"runs:{self._run_id}", event)
        except Exception as exc:  # noqa: BLE001 - 实时帧失败只告警
            log.warning(
                "成本帧推送失败",
                extra={"run_id": self._run_id, "type": event.get("type"), "error": repr(exc)},
            )


__all__ = ["RunCostEmitter"]
