"""研究执行任务占位：M1 阶段接 LangGraph 异步执行。"""

from app.workers.celery_app import celery_app


@celery_app.task(name="research.execute_run")
def execute_run(run_id: str) -> dict[str, str]:
    """占位实现：返回 run_id。M1 阶段调度 ResearchOrchestrator 执行。"""
    return {"run_id": run_id, "status": "accepted"}