"""节点通用工具：阶段标记、耗时统计、异常包装。"""

import functools
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.orchestrator.state import ResearchStage, ResearchState

log = get_logger("orchestrator.node")


def stage_update(stage: ResearchStage) -> dict[str, Any]:
    """生成当前阶段切换的状态增量。"""
    now = datetime.now(tz=timezone.utc)
    return {
        "current_stage": stage,
        "updated_at": now,
    }


def instrument(stage: ResearchStage) -> Callable[[Callable[..., Awaitable[dict[str, Any]]]], Callable[..., Awaitable[dict[str, Any]]]]:
    """装饰器：记录节点耗时与状态切换，自动吞掉异常并写入 failure_reason。"""

    def decorator(fn: Callable[..., Awaitable[dict[str, Any]]]) -> Callable[..., Awaitable[dict[str, Any]]]:
        @functools.wraps(fn)
        async def wrapper(state: ResearchState, *args: Any, **kwargs: Any) -> dict[str, Any]:
            start = time.perf_counter()
            log.info(
                "节点进入",
                extra={"stage": stage.value, "run_id": str(state.get("run_id"))},
            )
            try:
                patch = await fn(state, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - 编排层统一兜底
                log.exception("节点异常", extra={"stage": stage.value})
                return {
                    **stage_update(stage),
                    "failure_reason": f"{stage.value}: {exc!r}",
                }
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            log.info(
                "节点退出",
                extra={"stage": stage.value, "elapsed_ms": elapsed_ms},
            )
            return {**stage_update(stage), **patch}

        return wrapper

    return decorator