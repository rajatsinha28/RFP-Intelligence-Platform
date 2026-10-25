from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Bid(Base, TimestampMixin):
    __tablename__ = "bids"
    __table_args__ = (
        Index("ux_bids_knowledge_base_key", "knowledge_base_id", "bid_key", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bid_key: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(500))
    knowledge_base_id: Mapped[str] = mapped_column(String(64), default="rfp-intelligence")
    source_folder: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="created")
    extra_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    documents: Mapped[list[Document]] = relationship(back_populates="bid")
    extraction_runs: Mapped[list[ExtractionRun]] = relationship(
        back_populates="bid", cascade="all, delete-orphan"
    )


class Document(Base, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_kb_status", "knowledge_base_id", "status"),
        Index("ix_documents_hash", "knowledge_base_id", "content_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    knowledge_base_id: Mapped[str] = mapped_column(String(64), default="default")
    title: Mapped[str] = mapped_column(String(300))
    source_name: Mapped[str] = mapped_column(String(500))
    source_path: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    source_type: Mapped[str] = mapped_column(String(50))
    mime_type: Mapped[str] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(String(30), default="uploaded")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    content_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    extra_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    bid_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bids.id", ondelete="CASCADE"), nullable=True, index=True
    )
    doc_type: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    addendum_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    document_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    relative_path: Mapped[str | None] = mapped_column(String(1200), nullable=True)

    bid: Mapped[Bid | None] = relationship(back_populates="documents")
    versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    jobs: Mapped[list[IngestionJob]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentVersion(Base, TimestampMixin):
    __tablename__ = "document_versions"
    __table_args__ = (Index("ux_document_version", "document_id", "version", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE")
    )
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    storage_path: Mapped[str] = mapped_column(String(1200))
    parser_version: Mapped[str] = mapped_column(String(30), default="1")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="uploaded")

    document: Mapped[Document] = relationship(back_populates="versions")
    chunks: Mapped[list[ChunkRecord]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )


class ChunkRecord(Base, TimestampMixin):
    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_document", "document_id", "document_version"),
        Index("ux_chunks_chunk_id", "chunk_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE")
    )
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    document_version: Mapped[int] = mapped_column(Integer)
    chunk_id: Mapped[str] = mapped_column(String(160))
    parent_chunk_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    version: Mapped[DocumentVersion] = relationship(back_populates="chunks")


class IngestionJob(Base, TimestampMixin):
    __tablename__ = "ingestion_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0)
    stage: Mapped[str] = mapped_column(String(100), default="queued")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    document: Mapped[Document] = relationship(back_populates="jobs")


class QueryLog(Base, TimestampMixin):
    __tablename__ = "query_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    knowledge_base_id: Mapped[str] = mapped_column(String(64), index=True)
    query_hash: Mapped[str] = mapped_column(String(64))
    route: Mapped[str] = mapped_column(String(40))
    model_used: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cache_hit: Mapped[bool] = mapped_column(default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    answer_status: Mapped[str] = mapped_column(String(40))
    citation_count: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    token_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    retrieval_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class EvaluationRun(Base, TimestampMixin):
    __tablename__ = "evaluation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    dataset_name: Mapped[str] = mapped_column(String(500))
    knowledge_base_id: Mapped[str] = mapped_column(String(64), default="default")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    report_path: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExtractionRun(Base, TimestampMixin):
    __tablename__ = "extraction_runs"
    __table_args__ = (Index("ix_extraction_runs_bid_status", "bid_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bid_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bids.id", ondelete="CASCADE"), index=True
    )
    mode: Mapped[str] = mapped_column(String(30), default="extraction")
    status: Mapped[str] = mapped_column(String(30), default="queued")
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    validation_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    trace_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    token_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    latency_ms: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    artifact_path: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    bid: Mapped[Bid] = relationship(back_populates="extraction_runs")
    steps: Mapped[list[AgentStep]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class AgentStep(Base, TimestampMixin):
    __tablename__ = "agent_steps"
    __table_args__ = (Index("ix_agent_steps_run_agent", "run_id", "agent_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("extraction_runs.id", ondelete="CASCADE"), index=True
    )
    agent_name: Mapped[str] = mapped_column(String(100))
    stage: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="completed")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    output_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    token_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    latency_ms: Mapped[float] = mapped_column(Float, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[ExtractionRun] = relationship(back_populates="steps")
