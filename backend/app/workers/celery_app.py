"""Celery 应用工厂。

启动 worker：``deep-research-worker``（见 pyproject.toml scripts）。
"""

from celery import Celery

from app.core.config import get_settings


def _build_celery() -> Celery:
    cfg = get_settings()
    app = Celery(
        "deep-research",
        broker=cfg.celery_broker_url,
        backend=cfg.celery_result_backend,
        include=[
            "app.workers.tasks.research",
            "app.workers.tasks.report",
            "app.workers.tasks.ingestion",
        ],
    )
    app.conf.update(
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_reject_on_worker_lost=True,
        broker_connection_retry_on_startup=True,
        timezone="UTC",
    )
    return app


celery_app: Celery = _build_celery()


def run() -> None:
    """入口函数：``deep-research-worker`` 调用以启动 worker。"""
    celery_app.start(["worker", "-l", "info", "-Q", "research,report,ingestion"])