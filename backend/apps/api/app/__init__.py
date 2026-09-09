"""AI 研究者助手 - 后端应用包。

本包按职责拆分为多个子模块：
    api          FastAPI 路由层（HTTP / WebSocket / SSE）
    core         基础设施（配置、日志、安全、上下文、生命周期）
    db           ORM、会话与 Alembic 迁移
    schemas      入参/出参的 Pydantic 模型
    orchestrator LangGraph 状态图编排
    agents       8 个智能体实现
    provider     LLM Provider 适配层
    retrieval    检索、排序、抽取
    knowledge    知识库存储与向量检索
    connectors   外部系统连接器
    workers      Celery 异步任务
    realtime     实时通信（WS Hub / SSE）
    quota        成本治理与配额
    audit        审计日志
    notifications 通知中心
    templates    报告/项目模板
"""

__version__ = "0.1.0"