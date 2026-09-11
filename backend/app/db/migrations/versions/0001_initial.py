"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-10

对齐 LLD §5.3 / §5.4.2：一次性建出 teams / users / projects / research_runs /
stages / sub_questions / evidence / conflicts / verdicts / reports /
report_citations / knowledge_items / knowledge_embeddings / audit_entries 共 14 张表。

启用扩展：pgcrypto（用于数据库侧 ULID/UUID 辅助）、pgvector（向量 ANN）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 启用扩展
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    # pgvector 镜像中扩展注册名为 vector（非 pgvector），此处必须与 pg_available_extensions.name 对齐
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # ===== teams =====
    op.create_table(
        "teams",
        sa.Column("id", sa.String(length=26), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("plan", sa.String(length=16), nullable=False, server_default="free"),
        sa.Column("settings", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_teams_id", "teams", ["id"])

    # ===== users =====
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=26), primary_key=True, nullable=False),
        sa.Column(
            "team_id",
            sa.String(length=26),
            sa.ForeignKey("teams.id", name="fk_users_team_id_teams"),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_team_id", "users", ["team_id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ===== projects =====
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=26), primary_key=True, nullable=False),
        sa.Column(
            "team_id",
            sa.String(length=26),
            sa.ForeignKey("teams.id", name="fk_projects_team_id_teams"),
            nullable=False,
        ),
        sa.Column(
            "owner_id",
            sa.String(length=26),
            sa.ForeignKey("users.id", name="fk_projects_owner_id_users"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_template_id", sa.String(length=26), nullable=True),
        sa.Column(
            "default_tier", sa.String(length=16), nullable=False, server_default="standard"
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="active"
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_projects_id", "projects", ["id"])
    op.create_index("ix_projects_team_id", "projects", ["team_id"])
    op.create_index("ix_projects_team_status", "projects", ["team_id", "status"])

    # ===== research_runs =====
    op.create_table(
        "research_runs",
        sa.Column("id", sa.String(length=26), primary_key=True, nullable=False),
        sa.Column(
            "project_id",
            sa.String(length=26),
            sa.ForeignKey("projects.id", name="fk_research_runs_project_id_projects"),
            nullable=False,
        ),
        sa.Column(
            "creator_id",
            sa.String(length=26),
            sa.ForeignKey("users.id", name="fk_research_runs_creator_id_users"),
            nullable=False,
        ),
        sa.Column("template_id", sa.String(length=26), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("clarification", JSONB, nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("current_stage", sa.String(length=32), nullable=True),
        sa.Column("orchestrator_state", JSONB, nullable=True),
        sa.Column("token_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_budget", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_research_runs_id", "research_runs", ["id"])
    op.create_index("ix_research_runs_project_id", "research_runs", ["project_id"])
    op.create_index(
        "ix_runs_project_status", "research_runs", ["project_id", "status"]
    )
    op.create_index("ix_runs_creator", "research_runs", ["creator_id"])

    # ===== stages =====
    op.create_table(
        "stages",
        sa.Column("id", sa.String(length=26), primary_key=True, nullable=False),
        sa.Column(
            "run_id",
            sa.String(length=26),
            sa.ForeignKey("research_runs.id", name="fk_stages_run_id_research_runs"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=16), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("output", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_stages_id", "stages", ["id"])
    op.create_index("ix_stages_run_id", "stages", ["run_id"])

    # ===== sub_questions =====
    op.create_table(
        "sub_questions",
        sa.Column("id", sa.String(length=26), primary_key=True, nullable=False),
        sa.Column(
            "run_id",
            sa.String(length=26),
            sa.ForeignKey(
                "research_runs.id", name="fk_sub_questions_run_id_research_runs"
            ),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column(
            "depends_on", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sub_questions_id", "sub_questions", ["id"])
    op.create_index("ix_sub_questions_run_id", "sub_questions", ["run_id"])

    # ===== evidence =====
    op.create_table(
        "evidence",
        sa.Column("id", sa.String(length=26), primary_key=True, nullable=False),
        sa.Column(
            "run_id",
            sa.String(length=26),
            sa.ForeignKey("research_runs.id", name="fk_evidence_run_id_research_runs"),
            nullable=False,
        ),
        sa.Column(
            "sub_question_id",
            sa.String(length=26),
            sa.ForeignKey(
                "sub_questions.id", name="fk_evidence_sub_question_id_sub_questions"
            ),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("raw_storage_key", sa.String(length=255), nullable=True),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_level", sa.String(length=16), nullable=False),
        sa.Column("credibility", sa.String(length=1), nullable=False),
        sa.Column(
            "relevance_score", sa.Float(), nullable=False, server_default="0.0"
        ),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "excluded_by_user", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evidence_id", "evidence", ["id"])
    op.create_index("ix_evidence_run_id", "evidence", ["run_id"])
    op.create_index(
        "ix_evidence_sub_question_id", "evidence", ["sub_question_id"]
    )
    op.create_index("ix_evidence_run_subq", "evidence", ["run_id", "sub_question_id"])
    op.create_index("ix_evidence_fingerprint", "evidence", ["fingerprint"])

    # ===== conflicts =====
    op.create_table(
        "conflicts",
        sa.Column("id", sa.String(length=26), primary_key=True, nullable=False),
        sa.Column(
            "run_id",
            sa.String(length=26),
            sa.ForeignKey("research_runs.id", name="fk_conflicts_run_id_research_runs"),
            nullable=False,
        ),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column(
            "evidence_a_id",
            sa.String(length=26),
            sa.ForeignKey("evidence.id", name="fk_conflicts_evidence_a_id_evidence"),
            nullable=False,
        ),
        sa.Column(
            "evidence_b_id",
            sa.String(length=26),
            sa.ForeignKey("evidence.id", name="fk_conflicts_evidence_b_id_evidence"),
            nullable=False,
        ),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="detected"
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conflicts_id", "conflicts", ["id"])
    op.create_index("ix_conflicts_run_id", "conflicts", ["run_id"])

    # ===== verdicts =====
    op.create_table(
        "verdicts",
        sa.Column("id", sa.String(length=26), primary_key=True, nullable=False),
        sa.Column(
            "conflict_id",
            sa.String(length=26),
            sa.ForeignKey("conflicts.id", name="fk_verdicts_conflict_id_conflicts"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "user_id",
            sa.String(length=26),
            sa.ForeignKey("users.id", name="fk_verdicts_user_id_users"),
            nullable=False,
        ),
        sa.Column("choice", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
    )
    op.create_index("ix_verdicts_id", "verdicts", ["id"])

    # ===== reports =====
    op.create_table(
        "reports",
        sa.Column("id", sa.String(length=26), primary_key=True, nullable=False),
        sa.Column(
            "run_id",
            sa.String(length=26),
            sa.ForeignKey("research_runs.id", name="fk_reports_run_id_research_runs"),
            nullable=False,
            unique=True,
        ),
        sa.Column("template_id", sa.String(length=26), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="draft"
        ),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("content_json", JSONB, nullable=False),
        sa.Column("token_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reports_id", "reports", ["id"])

    # ===== report_citations =====
    op.create_table(
        "report_citations",
        sa.Column("id", sa.String(length=26), primary_key=True, nullable=False),
        sa.Column(
            "report_id",
            sa.String(length=26),
            sa.ForeignKey(
                "reports.id", name="fk_report_citations_report_id_reports"
            ),
            nullable=False,
        ),
        sa.Column(
            "evidence_id",
            sa.String(length=26),
            sa.ForeignKey(
                "evidence.id", name="fk_report_citations_evidence_id_evidence"
            ),
            nullable=False,
        ),
        sa.Column("claim_id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
    )
    op.create_index("ix_report_citations_id", "report_citations", ["id"])
    op.create_index("ix_report_citations_report_id", "report_citations", ["report_id"])
    op.create_index(
        "ix_citation_claim", "report_citations", ["report_id", "claim_id"]
    )

    # ===== knowledge_items =====
    op.create_table(
        "knowledge_items",
        sa.Column("id", sa.String(length=26), primary_key=True, nullable=False),
        sa.Column("team_id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=True),
        sa.Column(
            "created_by",
            sa.String(length=26),
            sa.ForeignKey(
                "users.id", name="fk_knowledge_items_created_by_users"
            ),
            nullable=False,
        ),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=True),
        sa.Column(
            "visibility", sa.String(length=16), nullable=False, server_default="project"
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_knowledge_items_id", "knowledge_items", ["id"])
    op.create_index("ix_knowledge_items_team_id", "knowledge_items", ["team_id"])
    op.create_index("ix_knowledge_items_project_id", "knowledge_items", ["project_id"])

    # ===== knowledge_embeddings =====
    op.create_table(
        "knowledge_embeddings",
        sa.Column(
            "item_id",
            sa.String(length=26),
            sa.ForeignKey(
                "knowledge_items.id",
                name="fk_knowledge_embeddings_item_id_knowledge_items",
            ),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
    )
    # pgvector 的 HNSW 索引必须显式指定向量操作符类；embedding 用于语义检索，采用余弦距离
    op.create_index(
        "ix_embedding_hnsw",
        "knowledge_embeddings",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    # ===== audit_entries =====
    op.create_table(
        "audit_entries",
        sa.Column("id", sa.String(length=26), primary_key=True, nullable=False),
        sa.Column("team_id", sa.String(length=26), nullable=False),
        sa.Column("user_id", sa.String(length=26), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=26), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_entries_id", "audit_entries", ["id"])
    op.create_index("ix_audit_entries_team_id", "audit_entries", ["team_id"])
    op.create_index("ix_audit_entries_user_id", "audit_entries", ["user_id"])
    op.create_index("ix_audit_entries_target_id", "audit_entries", ["target_id"])
    op.create_index("ix_audit_entries_created_at", "audit_entries", ["created_at"])


def downgrade() -> None:
    # 反序拆除；扩展保留（避免影响其他 schema）
    op.drop_table("audit_entries")
    op.drop_index("ix_embedding_hnsw", table_name="knowledge_embeddings")
    op.drop_table("knowledge_embeddings")
    op.drop_table("knowledge_items")
    op.drop_table("report_citations")
    op.drop_table("reports")
    op.drop_table("verdicts")
    op.drop_table("conflicts")
    op.drop_table("evidence")
    op.drop_table("sub_questions")
    op.drop_table("stages")
    op.drop_table("research_runs")
    op.drop_table("projects")
    op.drop_table("users")
    op.drop_table("teams")