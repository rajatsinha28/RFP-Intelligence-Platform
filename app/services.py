from __future__ import annotations

from functools import lru_cache

from app.cache.service import CacheService
from app.config import get_settings
from app.embeddings.openai import OpenAIEmbedder
from app.generation.service import OpenAIGenerator
from app.query.graph import RAGEngine
from app.query.transform import QueryTransformer
from app.retrieval.reranking import build_reranker
from app.retrieval.service import HybridRetriever
from app.vectorstore.pinecone import PineconeVectorStore


class Services:
    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.cache = CacheService(
            settings.redis_url,
            settings.cache_ttl_seconds,
            settings.embedding_cache_ttl_seconds,
            settings.semantic_cache_threshold,
            settings.semantic_cache_max_candidates,
        )
        self.embedder = OpenAIEmbedder(
            settings.openai_key,
            settings.embedding_model,
            settings.embedding_dimensions,
            settings.embedding_batch_size,
            settings.openai_base_url,
        )
        self.vector_store = PineconeVectorStore(
            settings.pinecone_key,
            settings.pinecone_index,
            settings.pinecone_namespace,
            settings.embedding_dimensions,
            settings.pinecone_cloud,
            settings.pinecone_region,
            settings.pinecone_auto_create,
            settings.pinecone_query_timeout_seconds,
        )
        cohere_key = settings.cohere_api_key.get_secret_value() if settings.cohere_api_key else None
        self.reranker = build_reranker(
            settings.reranker_provider, settings.reranker_model, cohere_key
        )
        self.retriever = HybridRetriever(
            self.embedder,
            self.vector_store,
            self.reranker,
            settings.dense_top_k,
            settings.bm25_top_k,
            settings.rerank_top_k,
            settings.rrf_k,
            settings.hybrid_alpha,
            settings.mmr_lambda,
        )
        self.transformer = QueryTransformer(
            settings.openai_key, settings.fast_model, settings.openai_base_url
        )
        self.generator = OpenAIGenerator(
            settings.openai_key,
            settings.fast_model,
            settings.advanced_model,
            settings.max_output_tokens,
            settings.openai_base_url,
            settings.fast_input_cost_per_million,
            settings.fast_output_cost_per_million,
            settings.advanced_input_cost_per_million,
            settings.advanced_output_cost_per_million,
        )
        self.engine = RAGEngine(
            settings, self.cache, self.retriever, self.transformer, self.generator
        )

    async def close(self) -> None:
        await self.cache.close()
        await self.embedder.close()
        await self.transformer.close()
        await self.generator.close()


@lru_cache(maxsize=1)
def get_services() -> Services:
    return Services()
