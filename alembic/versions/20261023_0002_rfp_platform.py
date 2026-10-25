"""Add bid packages and extraction run persistence."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261023_0002"
down_revision: str | None = "20261004_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bids",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("bid_key", sa.String(120), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("knowledge_base_id", sa.String(64), nullable=False),
        sa.Column("source_folder", sa.String(1200)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("extra_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ux_bids_knowledge_base_key", "bids", ["knowledge_base_id", "bid_key"], unique=True
    )
    op.add_column(
        "documents",
        sa.Column(
            "bid_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bids.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column("documents", sa.Column("doc_type", sa.String(40)))
    op.add_column("documents", sa.Column("addendum_number", sa.Integer()))
    op.add_column("documents", sa.Column("document_date", sa.DateTime(timezone=True)))
    op.add_column("documents", sa.Column("relative_path", sa.String(1200)))
    op.create_index("ix_documents_bid_id", "documents", ["bid_id"])
    op.create_index("ix_documents_doc_type", "documents", ["doc_type"])

    op.create_table(
        "extraction_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "bid_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bids.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("question", sa.Text()),
        sa.Column("output_json", sa.JSON(), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=False),
        sa.Column("trace_json", sa.JSON(), nullable=False),
        sa.Column("token_usage", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.JSON(), nullable=False),
        sa.Column("artifact_path", sa.String(1200)),
        sa.Column("error", sa.Text()),
        sa.Column("celery_task_id", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_extraction_runs_bid_id", "extraction_runs", ["bid_id"])
    op.create_index(
        "ix_extraction_runs_bid_status", "extraction_runs", ["bid_id", "status"]
    )

    op.create_table(
        "agent_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("extraction_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("stage", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("input_summary", sa.JSON(), nullable=False),
        sa.Column("tool_calls", sa.JSON(), nullable=False),
        sa.Column("output_summary", sa.JSON(), nullable=False),
        sa.Column("token_usage", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_steps_run_id", "agent_steps", ["run_id"])
    op.create_index("ix_agent_steps_run_agent", "agent_steps", ["run_id", "agent_name"])


def downgrade() -> None:
    op.drop_table("agent_steps")
    op.drop_table("extraction_runs")
    op.drop_index("ix_documents_doc_type", table_name="documents")
    op.drop_index("ix_documents_bid_id", table_name="documents")
    op.drop_column("documents", "relative_path")
    op.drop_column("documents", "document_date")
    op.drop_column("documents", "addendum_number")
    op.drop_column("documents", "doc_type")
    op.drop_column("documents", "bid_id")
    op.drop_table("bids")
