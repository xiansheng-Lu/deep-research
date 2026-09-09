"""SSE 端点占位：报告流式生成通过 SSE 推送到前端。"""

from collections.abc import AsyncIterator


async def report_stream(report_id: str) -> AsyncIterator[str]:
    """占位：当前仅推送一个空事件。"""
    yield f"event: ping\ndata: report={report_id}\n\n"