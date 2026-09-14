"""ORM 模型集中注册。

新增模型文件时请在本模块 ``import``，确保 ``Base.metadata`` 在
Alembic 自动生成迁移时能发现全部表。
"""

from app.db.models.audit import AuditEntry
from app.db.models.conflict import Conflict, Verdict
from app.db.models.evidence import Evidence
from app.db.models.identity import Team, User
from app.db.models.intervention import RunIntervention
from app.db.models.knowledge import KnowledgeEmbedding, KnowledgeItem
from app.db.models.project import Project
from app.db.models.report import Report, ReportCitation
from app.db.models.run import ResearchRun, Stage, SubQuestion

__all__ = [
    "AuditEntry",
    "Conflict",
    "Evidence",
    "KnowledgeEmbedding",
    "KnowledgeItem",
    "Project",
    "Report",
    "ReportCitation",
    "ResearchRun",
    "RunIntervention",
    "Stage",
    "SubQuestion",
    "Team",
    "User",
    "Verdict",
]
