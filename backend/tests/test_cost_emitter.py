"""``RunCostEmitter`` 单元测试（M2-3，AC-10/AC-11）。

不依赖 DB/WS：hub 用收集型替身，时钟用可控单调钟，验证：
- token.usage.update 1s 节流、用量未变不发、终态 force 补发；
- run.token_used 写穿；
- cost.warning 边沿语义（70% warning、90% danger、单跳直达 danger、同级不重发）；
- hub 缺省/抛错不阻断。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.db.models.run import ResearchRun
from app.quota.emitter import RunCostEmitter


class _FakeClock:
    """可控单调钟。"""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _CollectHub:
    """收集 publish 事件的 hub 替身；可配置抛错。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[dict[str, Any]] = []
        self._fail = fail

    async def publish(self, channel: str, event: dict[str, Any]) -> None:
        if self._fail:
            raise RuntimeError("hub boom")
        self.events.append(event)


def _run(*, budget: int = 1000, used: int = 0) -> ResearchRun:
    return ResearchRun(
        id="run-emitter",
        project_id="proj-1",
        creator_id="user-1",
        template_id="generic",
        tier="standard",
        question="成本发射器测试问题",
        token_used=used,
        token_budget=budget,
    )


def _types(hub: _CollectHub) -> list[str]:
    return [str(e["type"]) for e in hub.events]


@pytest.mark.asyncio
async def test_token_frames_throttled_and_write_through() -> None:
    """AC-10：1s 节流合并快更新；用量未变不发；run 行同步写穿。"""
    clock = _FakeClock()
    hub = _CollectHub()
    run = _run()
    emitter = RunCostEmitter(hub=hub, run=run, update_interval_ms=1000, clock=clock)

    await emitter.observe(100)  # 首帧立即
    await emitter.observe(110)  # 不足 1s：节流合并，不发 token 帧
    assert run.token_used == 110  # 写穿不依赖节流
    assert _types(hub) == ["token.usage.update"]

    clock.advance(1.0)
    await emitter.observe(120)  # 到点且用量变化：发
    await emitter.observe(120)  # 用量未变：即使到点也不发
    assert _types(hub) == ["token.usage.update", "token.usage.update"]

    clock.advance(0.2)
    await emitter.observe(120, force=True)  # 终态强制补发（即使值未变）
    assert _types(hub).count("token.usage.update") == 3
    assert hub.events[-1]["payload"]["used"] == 120


@pytest.mark.asyncio
async def test_warning_edges_warning_then_danger() -> None:
    """AC-11：跨 70% 一帧 warning；跨 90% 再一帧 danger。"""
    clock = _FakeClock()
    hub = _CollectHub()
    run = _run()
    emitter = RunCostEmitter(hub=hub, run=run, update_interval_ms=1000, clock=clock)

    await emitter.observe(600)  # 0.60：无预警
    await emitter.observe(700, force=True)  # 0.70：warning（边界含等于）
    warning_frames = [e for e in hub.events if e["type"] == "cost.warning"]
    assert len(warning_frames) == 1
    assert warning_frames[0]["payload"]["level"] == "warning"

    clock.advance(1.0)
    await emitter.observe(900, force=True)  # 0.90：仍 warning，不重复
    clock.advance(1.0)
    await emitter.observe(901, force=True)  # 0.901：danger（严格大于）
    levels = [e["payload"]["level"] for e in hub.events if e["type"] == "cost.warning"]
    assert levels == ["warning", "danger"]


@pytest.mark.asyncio
async def test_single_jump_over_danger_emits_only_danger() -> None:
    """AC-11：单跳越过 90% 只发 danger，不补 warning。"""
    hub = _CollectHub()
    run = _run()
    emitter = RunCostEmitter(hub=hub, run=run, clock=_FakeClock())

    await emitter.observe(950, force=True)
    levels = [e["payload"]["level"] for e in hub.events if e["type"] == "cost.warning"]
    assert levels == ["danger"]


@pytest.mark.asyncio
async def test_same_level_not_repeated_and_ratio_helpers() -> None:
    """同级用量继续增长不重复预警；ratio_of/level_for 口径正确。"""
    clock = _FakeClock()
    hub = _CollectHub()
    run = _run(budget=100)
    emitter = RunCostEmitter(hub=hub, run=run, update_interval_ms=1000, clock=clock)

    assert emitter.ratio_of(50) == 0.5
    assert emitter.level_for(0.7) == "warning"
    assert emitter.level_for(0.9) == "warning"
    assert emitter.level_for(0.9001) == "danger"
    assert emitter.level_for(0.1) is None

    # 预算为 0 的除零保护
    zero_emitter = RunCostEmitter(hub=hub, run=_run(budget=0), clock=clock)
    assert zero_emitter.ratio_of(5) == 0.0

    await emitter.observe(75, force=True)
    clock.advance(1.0)
    await emitter.observe(80, force=True)
    levels = [e["payload"]["level"] for e in hub.events if e["type"] == "cost.warning"]
    assert levels == ["warning"]


@pytest.mark.asyncio
async def test_hub_none_or_failing_does_not_raise() -> None:
    """hub 缺省或推送失败均吞错，不阻断 observe。"""
    run = _run()
    await RunCostEmitter(hub=None, run=run).observe(900, force=True)
    failing = RunCostEmitter(hub=_CollectHub(fail=True), run=run)
    await failing.observe(950, force=True)  # 不抛即通过
    assert run.token_used == 950
