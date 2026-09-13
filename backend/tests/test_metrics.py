"""Prometheus 指标单元/路由测试（M2-3，AC-17）。

- 打点函数：LLM 标签取自 ContextVar、零 token 跳过；检索终态白名单收敛；
- 渲染文本包含四类指标；
- ``METRICS_ENABLED=true`` 时 ``GET /metrics`` 暴露，HTTP 中间件按路由模板打点；
- 关闭时端点 404。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.context import current_run_id, current_stage
from app.main import create_app
from app.observability.metrics import (
    record_evidence_fetch,
    record_llm_usage,
    render_latest,
)


@pytest.fixture
def run_context() -> object:
    """绑定并在用例结束后复位 run/stage ContextVar。"""
    token_run = current_run_id.set("run-metrics")
    token_stage = current_stage.set("retrieve")
    yield None
    current_run_id.reset(token_run)
    current_stage.reset(token_stage)


def test_record_and_render_metrics(run_context: object) -> None:
    """四类指标打点后出现在渲染文本中，标签口径正确。"""
    record_llm_usage(model="gpt-test", total_tokens=1500)
    record_llm_usage(model="gpt-test", total_tokens=0)  # 零值跳过
    record_evidence_fetch(source_type="web", status="succeeded")
    record_evidence_fetch(source_type="web", status="weird-status")  # 收敛 failed

    payload, content_type = render_latest()
    text = payload.decode("utf-8")
    assert content_type.startswith("text/plain")
    assert "http_requests_total" in text
    assert "http_request_duration_seconds" in text
    assert "llm_token_usage_total" in text
    assert 'model="gpt-test",run_id="run-metrics",stage="retrieve"' in text
    assert "ws_active_connections" in text
    assert 'evidence_fetch_total{source_type="web",status="succeeded"}' in text
    assert 'evidence_fetch_total{source_type="web",status="failed"}' in text
    # 零值跳过：gpt-test 只有一次 1500 的累计样本
    sample = 'llm_token_usage_total{model="gpt-test",run_id="run-metrics",stage="retrieve"}'
    assert text.count(sample) == 1
    assert f"{sample} 1500.0" in text


def test_llm_usage_without_context_labels_unknown() -> None:
    """非图上下文缺省标签标 unknown。"""
    record_llm_usage(model="gpt-solo", total_tokens=10)
    text = render_latest()[0].decode("utf-8")
    assert 'llm_token_usage_total{model="gpt-solo",run_id="unknown",stage="unknown"}' in text


def test_metrics_endpoint_enabled_exposes_latest() -> None:
    """METRICS_ENABLED=true：/metrics 200，且业务请求按路由模板累计。"""
    # 不用 with 触发 lifespan（lifespan 会真实连接 PG）
    client = TestClient(create_app(Settings(metrics_enabled=True)))
    assert client.get("/healthz").status_code == 200
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    text = resp.text
    assert "http_requests_total" in text
    assert 'path="/healthz"' in text
    assert "ws_active_connections" in text


def test_metrics_endpoint_disabled_returns_404() -> None:
    """METRICS_ENABLED=false：不注册抓取端点。"""
    client = TestClient(create_app(Settings(metrics_enabled=False)))
    assert client.get("/healthz").status_code == 200
    assert client.get("/metrics").status_code == 404
