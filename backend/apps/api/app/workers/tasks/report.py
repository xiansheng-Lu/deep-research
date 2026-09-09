"""报告导出与分享任务占位。"""

from app.workers.celery_app import celery_app


@celery_app.task(name="report.export")
def export_report(report_id: str, format: str) -> dict[str, str]:
    """占位实现。M1 阶段调用对象存储写入文件。"""
    return {"report_id": report_id, "format": format, "status": "accepted"}