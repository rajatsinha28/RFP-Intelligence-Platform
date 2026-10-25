from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    app_name: str = "Advance RAG"
    app_api_key: SecretStr | None = None
    allowed_origins: str = "http://localhost:8000,http://localhost:5173"

    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    fast_model: str = "gpt-6-luna"
    advanced_model: str = "gpt-6.1-sol"
    embedding_model: str = "text-embedding-3-large"
    embedding_dimensions: int = 3072
    max_output_tokens: int = 1800
    fast_input_cost_per_million: float = 0.0
    fast_output_cost_per_million: float = 0.0
    advanced_input_cost_per_million: float = 0.0
    advanced_output_cost_per_million: float = 0.0

    pinecone_api_key: SecretStr | None = None
    pinecone_index: str = "advance-rag"
    pinecone_namespace: str = "kb-default"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"
    pinecone_auto_create: bool = True
    pinecone_query_timeout_seconds: float = 20.0

    postgres_url: str = "postgresql+asyncpg://advance_rag:advance_rag@localhost:5432/advance_rag"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    storage_path: Path = Path("storage")
    bid_output_path: Path = Path("outputs/bids")
    rfp_knowledge_base_id: str = "rfp-intelligence"
    max_upload_mb: int = 50
    web_max_download_mb: int = 25
    web_fetch_timeout_seconds: int = 20
    ocr_languages: str = "eng"

    chunk_strategy: Literal["recursive", "token", "sentence", "semantic", "structure"] = "structure"
    chunk_size: int = 900
    chunk_overlap: int = 120
    min_chunk_size: int = 120
    max_chunk_size: int = 1600
    embedding_batch_size: int = 64

    dense_top_k: int = 30
    bm25_top_k: int = 30
    rerank_top_k: int = 8
    final_context_k: int = 8
    context_neighbor_radius: int = 1
    hybrid_alpha: float = 0.72
    rrf_k: int = 60
    mmr_lambda: float = 0.72
    similarity_threshold: float = 0.22
    answerability_high_threshold: float = 0.72
    answerability_medium_threshold: float = 0.48
    max_retrieval_retries: int = 1
    max_field_validation_retries: int = 2

    reranker_provider: Literal["local", "lexical", "cohere"] = "local"
    reranker_model: str = "BAAI/bge-reranker-base"
    cohere_api_key: SecretStr | None = None

    cache_ttl_seconds: int = 3600
    embedding_cache_ttl_seconds: int = 604800
    semantic_cache_threshold: float = 0.93
    semantic_cache_max_candidates: int = 250
    max_context_tokens: int = 12000

    rate_limit_per_minute: int = 60
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "advance-rag"

    @field_validator("chunk_overlap")
    @classmethod
    def validate_overlap(cls, value: int) -> int:
        if value < 0:
            raise ValueError("CHUNK_OVERLAP must be non-negative")
        return value

    @field_validator("hybrid_alpha", "mmr_lambda", "semantic_cache_threshold")
    @classmethod
    def validate_unit_interval(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("value must be between 0 and 1")
        return value

    @property
    def origins(self) -> list[str]:
        return [item.strip() for item in self.allowed_origins.split(",") if item.strip()]

    @property
    def upload_limit_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def web_download_limit_bytes(self) -> int:
        return self.web_max_download_mb * 1024 * 1024

    @property
    def openai_key(self) -> str | None:
        return self.openai_api_key.get_secret_value() if self.openai_api_key else None

    @property
    def pinecone_key(self) -> str | None:
        return self.pinecone_api_key.get_secret_value() if self.pinecone_api_key else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
