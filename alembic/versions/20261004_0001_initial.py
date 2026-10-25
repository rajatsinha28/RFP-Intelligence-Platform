"""Initial application schema."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261004_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("knowledge_base_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("source_name", sa.String(500), nullable=False),
        sa.Column("source_path", sa.String(1200)),
        sa.Column("source_url", sa.String(2000)),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("mime_type", sa.String(150), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("extra_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_documents_kb_status", "documents", ["knowledge_base_id", "status"])
    op.create_index("ix_documents_hash", "documents", ["knowledge_base_id", "content_hash"])
    op.create_table(
        "document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.String(1200), nullable=False),
        sa.Column("parser_version", sa.String(30), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ux_document_version", "document_versions", ["document_id", "version"], unique=True)
    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.String(160), nullable=False),
        sa.Column("parent_chunk_id", sa.String(160)),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chunks_document", "chunks", ["document_id", "document_version"])
    op.create_index("ux_chunks_chunk_id", "chunks", ["chunk_id"], unique=True)
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("stage", sa.String(100), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("celery_task_id", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ingestion_jobs_document_id", "ingestion_jobs", ["document_id"])
    op.create_table(
        "query_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", sa.String(80), nullable=False),
        sa.Column("knowledge_base_id", sa.String(64), nullable=False),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column("route", sa.String(40), nullable=False),
        sa.Column("model_used", sa.String(120)),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("answer_status", sa.String(40), nullable=False),
        sa.Column("citation_count", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.JSON(), nullable=False),
        sa.Column("token_usage", sa.JSON(), nullable=False),
        sa.Column("retrieval_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_query_logs_request_id", "query_logs", ["request_id"], unique=True)
    op.create_index("ix_query_logs_knowledge_base_id", "query_logs", ["knowledge_base_id"])
    op.create_table(
        "evaluation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("dataset_name", sa.String(500), nullable=False),
        sa.Column("knowledge_base_id", sa.String(64), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("report_path", sa.String(1200)),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for table in ["evaluation_runs", "query_logs", "ingestion_jobs", "chunks", "document_versions", "documents"]:
        op.drop_table(table)

