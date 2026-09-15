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
        # Celery 5.x 规范名（旧名 reject_on_worker_lost 仅为兼容别名）：
        # worker 硬 kill 时消息 requeue，配合租约 NX 幂等防重跑（§16.4）
        task_reject_on_worker_lost=True,
        broker_connection_retry_on_startup=True,
        timezone="UTC",
        # 按任务名前缀固定路由到命名队列（.delay/.apply_async 发送端生效），
        # worker 以 -Q research,report,ingestion 精确消费；不配置时消息会落到
        # 默认 celery 队列导致无人消费。
        task_routes={
            "research.*": {"queue": "research"},
            "report.*": {"queue": "report"},
            "ingestion.*": {"queue": "ingestion"},
        },
    )
    return app


celery_app: Celery = _build_celery()


def run() -> None:
    """入口函数：``deep-research-worker`` 调用以启动 worker。"""
    celery_app.start(["worker", "-l", "info", "-Q", "research,report,ingestion"])
