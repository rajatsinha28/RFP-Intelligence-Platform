from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    ACTIVE = "active"
    FAILED = "failed"
    DELETED = "deleted"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    PARTIALLY_ANSWERED = "partially_answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_FOUND = "not_found"
    ERROR = "error"


class QueryRoute(StrEnum):
    CACHE_HIT = "CACHE_HIT"
    DIRECT_DOCUMENT_LOOKUP = "DIRECT_DOCUMENT_LOOKUP"
    SIMPLE_RAG = "SIMPLE_RAG"
    ADVANCED_RAG = "ADVANCED_RAG"
    MULTI_HOP_RAG = "MULTI_HOP_RAG"
    FALLBACK = "FALLBACK"


class Citation(BaseModel):
    document_id: str
    document: str
    chunk_id: str
    page: int | None = None
    section: str | None = None
    source_url: str | None = None
    quote: str | None = None


class Claim(BaseModel):
    text: str
    citation_ids: list[str] = Field(default_factory=list)


class RetrievalMetadata(BaseModel):
    dense_results: int = 0
    bm25_results: int = 0
    fused_results: int = 0
    reranked_results: int = 0
    retrieval_attempts: int = 1
    top_hits: list[RetrievalDiagnostic] = Field(default_factory=list)


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


class RetrievalDiagnostic(BaseModel):
    chunk_id: str
    page: int | None = None
    source: str
    fusion_score: float = 0.0
    reranker_score: float = 0.0
    query_variant: str = ""


class QueryRequest(BaseModel):
    query: str = Field(min_length=2, max_length=4000)
    knowledge_base_id: str = Field(default="default", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    metadata_filter: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str | None = Field(default=None, max_length=128)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        return " ".join(value.split())


class QueryResponse(BaseModel):
    answer: str
    status: AnswerStatus
    confidence: float = Field(ge=0, le=1)
    citations: list[Citation] = Field(default_factory=list)
    route: QueryRoute
    model_used: str | None = None
    cache_hit: bool = False
    retrieval_metadata: RetrievalMetadata = Field(default_factory=RetrievalMetadata)
    latency_ms: dict[str, float] = Field(default_factory=dict)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    request_id: str


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    source_name: str
    source_type: str
    source_url: str | None
    mime_type: str
    status: DocumentStatus
    current_version: int
    content_hash: str
    size_bytes: int
    bid_id: UUID | None = None
    doc_type: str | None = None
    addendum_number: int | None = None
    document_date: datetime | None = None
    relative_path: str | None = None
    created_at: datetime
    updated_at: datetime


class DocumentList(BaseModel):
    items: list[DocumentRead]
    total: int


class UploadResponse(BaseModel):
    document_id: UUID
    job_id: UUID
    status: JobStatus


class URLIngestRequest(BaseModel):
    url: HttpUrl
    title: str | None = Field(default=None, max_length=300)
    knowledge_base_id: str = Field(default="default", pattern=r"^[a-zA-Z0-9_-]{1,64}$")


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_id: UUID
    status: JobStatus
    progress: float
    stage: str
    error: str | None
    created_at: datetime
    updated_at: datetime


class EvaluationRequest(BaseModel):
    dataset_path: str = "evaluation/datasets/example.json"
    knowledge_base_id: str = "default"


class EvaluationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: JobStatus
    dataset_name: str
    metrics: dict[str, Any]
    report_path: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class HealthResponse(BaseModel):
    status: str
    version: str
    dependencies: dict[str, str] = Field(default_factory=dict)


class RetrievalHit(BaseModel):
    chunk_id: str
    document_id: str
    text: str
    source_name: str
    source_url: str | None = None
    page_number: int | None = None
    section: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    dense_score: float = 0.0
    sparse_score: float = 0.0
    fusion_score: float = 0.0
    reranker_score: float = 0.0
    vector: list[float] | None = None


class BidCreate(BaseModel):
    bid_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
    title: str = Field(min_length=1, max_length=500)
    knowledge_base_id: str = Field(default="rfp-intelligence", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    source_folder: str | None = Field(default=None, max_length=1200)


class BidRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    bid_key: str
    title: str
    knowledge_base_id: str
    source_folder: str | None
    status: str
    extra_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class BidDetail(BidRead):
    documents: list[DocumentRead] = Field(default_factory=list)


class BidList(BaseModel):
    items: list[BidRead]
    total: int


class FolderImportRequest(BaseModel):
    path: str = Field(min_length=1, max_length=2000)
    knowledge_base_id: str = Field(default="rfp-intelligence", pattern=r"^[a-zA-Z0-9_-]{1,64}$")


class FolderImportResult(BaseModel):
    bids: list[BidRead]
    document_jobs: list[UploadResponse]
    ignored_files: list[str] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=4000)
    knowledge_base_id: str = Field(default="rfp-intelligence", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    bid_keys: list[str] = Field(default_factory=list, max_length=20)
    doc_types: list[str] = Field(default_factory=list, max_length=20)
    addendum_numbers: list[int] = Field(default_factory=list, max_length=50)
    source_names: list[str] = Field(default_factory=list, max_length=50)
    top_k: int = Field(default=8, ge=1, le=50)
    mode: str = Field(default="hybrid", pattern=r"^(dense|hybrid|hybrid_rerank)$")


class SearchResponse(BaseModel):
    query: str
    expanded_queries: list[str]
    hits: list[RetrievalHit]
    dense_results: int
    bm25_results: int
    request_id: str
