"""知识库 / 连接器数据摄入任务占位。"""

from app.workers.celery_app import celery_app


@celery_app.task(name="ingestion.ingest")
def ingest(item_id: str) -> dict[str, str]:
    """占位实现。M1 阶段调用 chunker + embeddings + store 完成入库。"""
    return {"item_id": item_id, "status": "accepted"}