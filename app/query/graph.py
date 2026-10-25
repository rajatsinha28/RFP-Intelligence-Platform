from __future__ import annotations

import asyncio
import uuid
from time import perf_counter
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.cache.service import CacheService
from app.config import Settings
from app.core.hashing import stable_hash
from app.generation.citations import build_citations, complete_claim_citations
from app.generation.grounding import validate_grounding
from app.generation.service import OpenAIGenerator
from app.observability.logging import get_logger
from app.query.answerability import Answerability, assess_answerability
from app.query.classifier import classify_query
from app.query.transform import QueryTransformer
from app.retrieval.compression import compress_context
from app.retrieval.context import expand_adjacent_chunks
from app.retrieval.service import HybridRetriever, RetrievalResult
from app.rfp.search import rfp_query_variants
from app.schemas import (
    AnswerStatus,
    QueryRequest,
    QueryResponse,
    QueryRoute,
    RetrievalDiagnostic,
    RetrievalHit,
    RetrievalMetadata,
    TokenUsage,
)

FALLBACK_TEXT = "I couldn't find this information in the provided documents."
CACHE_SCHEMA_VERSION = 8
CACHEABLE_ANSWER_STATUSES = frozenset({AnswerStatus.ANSWERED, AnswerStatus.PARTIALLY_ANSWERED})
logger = get_logger()


class QueryState(TypedDict, total=False):
    request: QueryRequest
    request_id: str
    kb_version: str
    config_version: str
    cache_scope: str
    route: QueryRoute
    query_variants: list[str]
    query_vector: list[float]
    hits: list[RetrievalHit]
    retrieval_metadata: RetrievalMetadata
    answerability: Answerability
    retry_count: int
    response: QueryResponse
    latencies: dict[str, float]


class RAGEngine:
    def __init__(
        self,
        settings: Settings,
        cache: CacheService,
        retriever: HybridRetriever,
        transformer: QueryTransformer,
        generator: OpenAIGenerator,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.retriever = retriever
        self.transformer = transformer
        self.generator = generator
        self.config_version = stable_hash(
            {
                "cache_schema": CACHE_SCHEMA_VERSION,
                "fast": settings.fast_model,
                "advanced": settings.advanced_model,
                "embedding": settings.embedding_model,
                "dimensions": settings.embedding_dimensions,
                "chunk": [settings.chunk_strategy, settings.chunk_size, settings.chunk_overlap],
                "retrieval": [
                    settings.dense_top_k,
                    settings.bm25_top_k,
                    settings.rerank_top_k,
                    settings.hybrid_alpha,
                    settings.rrf_k,
                    settings.mmr_lambda,
                ],
                "reranker": [settings.reranker_provider, settings.reranker_model],
                "answerability": [
                    settings.similarity_threshold,
                    settings.answerability_high_threshold,
                    settings.answerability_medium_threshold,
                    settings.max_retrieval_retries,
                ],
                "generation": [
                    settings.final_context_k,
                    settings.context_neighbor_radius,
                    settings.max_context_tokens,
                    settings.max_output_tokens,
                ],
            }
        )[:16]
        self.graph = self._build_graph()

    @staticmethod
    def _timed(state: QueryState, name: str, started: float) -> None:
        state.setdefault("latencies", {})[name] = round((perf_counter() - started) * 1000, 3)

    async def _cache_lookup(self, state: QueryState) -> QueryState:
        started = perf_counter()
        request = state["request"]
        cache_scope = stable_hash(request.metadata_filter)[:16]
        try:
            kb_version = await self.cache.kb_version(request.knowledge_base_id)
            cached = await self.cache.get_exact(
                request.query, kb_version, self.config_version, cache_scope
            )
        except Exception:
            kb_version, cached = "unavailable", None
        state["kb_version"] = kb_version
        state["config_version"] = self.config_version
        state["cache_scope"] = cache_scope
        if cached:
            cached = cached.model_copy(
                update={
                    "route": QueryRoute.CACHE_HIT,
                    "cache_hit": True,
                    "request_id": state["request_id"],
                }
            )
            state["response"] = cached
        self._timed(state, "cache_lookup_ms", started)
        return state

    async def _classify(self, state: QueryState) -> QueryState:
        started = perf_counter()
        classification = classify_query(state["request"].query)
        state["route"] = classification.route
        state["query_variants"] = [state["request"].query]
        state["retry_count"] = 0
        self._timed(state, "query_classification_ms", started)
        return state

    async def _semantic_cache(self, state: QueryState) -> QueryState:
        started = perf_counter()
        query_vector = await self.retriever.embedder.embed_query(state["request"].query)
        state["query_vector"] = query_vector
        try:
            cached = await self.cache.get_semantic(
                query_vector,
                state["kb_version"],
                state["config_version"],
                state["cache_scope"],
            )
        except Exception:
            cached = None
        if cached:
            state["response"] = cached.model_copy(
                update={
                    "route": QueryRoute.CACHE_HIT,
                    "cache_hit": True,
                    "request_id": state["request_id"],
                }
            )
        self._timed(state, "semantic_cache_ms", started)
        return state

    async def _transform(self, state: QueryState) -> QueryState:
        started = perf_counter()
        route = state["route"]
        variants = await self.transformer.transform(
            state["request"].query,
            decompose=route in {QueryRoute.ADVANCED_RAG, QueryRoute.MULTI_HOP_RAG}
            or state.get("retry_count", 0) > 0,
        )
        if state["request"].knowledge_base_id == self.settings.rfp_knowledge_base_id:
            variants = rfp_query_variants(state["request"].query, variants)
        state["query_variants"] = variants
        self._timed(state, "query_transform_ms", started)
        return state

    async def _retrieve(self, state: QueryState) -> QueryState:
        started = perf_counter()
        request = state["request"]
        namespace = f"kb-{request.knowledge_base_id}"
        variants = state.get("query_variants") or [request.query]
        first_vector = state.get("query_vector")
        results = await asyncio.gather(
            *(
                self.retriever.retrieve(
                    query=variant,
                    metadata_filter=request.metadata_filter,
                    namespace=namespace,
                    use_weighted_fusion=state.get("retry_count", 0) > 0,
                    query_vector=first_vector if index == 0 else None,
                )
                for index, variant in enumerate(variants)
            )
        )
        by_id: dict[str, RetrievalHit] = {}
        best_variants: dict[str, str] = {}
        for variant, result in zip(variants, results, strict=True):
            for hit in result.hits:
                previous = by_id.get(hit.chunk_id)
                if previous is None or hit.reranker_score > previous.reranker_score:
                    by_id[hit.chunk_id] = hit
                    best_variants[hit.chunk_id] = variant[:240]
        hits = sorted(by_id.values(), key=lambda item: item.reranker_score, reverse=True)
        state["hits"] = hits[: self.settings.rerank_top_k]
        primary: RetrievalResult = results[0]
        state["query_vector"] = primary.query_vector
        top_hits = [
            RetrievalDiagnostic(
                chunk_id=hit.chunk_id,
                page=hit.page_number,
                source=hit.source_name,
                fusion_score=hit.fusion_score,
                reranker_score=hit.reranker_score,
                query_variant=best_variants.get(hit.chunk_id, request.query[:240]),
            )
            for hit in state["hits"][:10]
        ]
        state["retrieval_metadata"] = RetrievalMetadata(
            dense_results=sum(item.dense_count for item in results),
            bm25_results=sum(item.sparse_count for item in results),
            fused_results=len(by_id),
            reranked_results=len(state["hits"]),
            retrieval_attempts=state.get("retry_count", 0) + 1,
            top_hits=top_hits,
        )
        logger.info(
            "retrieval_diagnostics",
            query_variant_count=len(variants),
            top_hits=[item.model_dump() for item in top_hits],
        )
        self._timed(state, "retrieval_ms", started)
        return state

    async def _assess(self, state: QueryState) -> QueryState:
        started = perf_counter()
        state["answerability"] = assess_answerability(
            state["request"].query,
            state.get("hits", []),
            self.settings.answerability_high_threshold,
            self.settings.answerability_medium_threshold,
            self.settings.similarity_threshold,
        )
        self._timed(state, "answerability_ms", started)
        return state

    async def _prepare_retry(self, state: QueryState) -> QueryState:
        state["retry_count"] = state.get("retry_count", 0) + 1
        return state

    async def _fallback(self, state: QueryState) -> QueryState:
        confidence = state.get("answerability", Answerability(0, False, False, [])).confidence
        state["route"] = QueryRoute.FALLBACK
        state["response"] = QueryResponse(
            answer=FALLBACK_TEXT,
            status=AnswerStatus.NOT_FOUND,
            confidence=confidence,
            citations=[],
            route=QueryRoute.FALLBACK,
            model_used=None,
            cache_hit=False,
            retrieval_metadata=state.get("retrieval_metadata", RetrievalMetadata()),
            latency_ms=state.get("latencies", {}),
            token_usage=TokenUsage(),
            request_id=state["request_id"],
        )
        return state

    async def _generate(self, state: QueryState) -> QueryState:
        started = perf_counter()
        request = state["request"]
        max_chars = self.settings.max_context_tokens * 4
        expanded_hits = await expand_adjacent_chunks(
            state.get("hits", [])[: self.settings.final_context_k],
            self.settings.context_neighbor_radius,
        )
        hits = compress_context(
            request.query,
            expanded_hits,
            max_chars,
        )
        logger.info(
            "context_expansion",
            retrieved_count=min(len(state.get("hits", [])), self.settings.final_context_k),
            expanded_count=len(expanded_hits),
            compressed_count=len(hits),
        )
        advanced = state["route"] in {QueryRoute.ADVANCED_RAG, QueryRoute.MULTI_HOP_RAG}
        generated = await self.generator.generate(request.query, hits, advanced=advanced)
        generated.claims = complete_claim_citations(
            generated.claims, hits, required_context=request.query
        )
        grounding = validate_grounding(generated.claims, hits)
        if not grounding.valid:
            original_usage = generated.token_usage
            retried = await self.generator.generate(
                request.query, hits, advanced=advanced, strict_retry=True
            )
            retried.token_usage = TokenUsage(
                input_tokens=original_usage.input_tokens + retried.token_usage.input_tokens,
                output_tokens=original_usage.output_tokens + retried.token_usage.output_tokens,
                reasoning_tokens=(
                    original_usage.reasoning_tokens + retried.token_usage.reasoning_tokens
                ),
                total_tokens=original_usage.total_tokens + retried.token_usage.total_tokens,
                estimated_cost_usd=round(
                    original_usage.estimated_cost_usd + retried.token_usage.estimated_cost_usd,
                    8,
                ),
            )
            generated = retried
            generated.claims = complete_claim_citations(
                generated.claims, hits, required_context=request.query
            )
            grounding = validate_grounding(generated.claims, hits)
        if not grounding.valid:
            generated.answer = FALLBACK_TEXT
            generated.status = AnswerStatus.NOT_FOUND
            generated.claims = []
        if generated.status in {
            AnswerStatus.NOT_FOUND,
            AnswerStatus.INSUFFICIENT_EVIDENCE,
        }:
            generated.answer = FALLBACK_TEXT
            generated.status = AnswerStatus.NOT_FOUND
            generated.claims = []
        state["response"] = QueryResponse(
            answer=generated.answer,
            status=generated.status,
            confidence=state["answerability"].confidence,
            citations=build_citations(generated.claims, hits),
            route=state["route"],
            model_used=generated.model_used,
            cache_hit=False,
            retrieval_metadata=state["retrieval_metadata"],
            latency_ms=state.get("latencies", {}),
            token_usage=generated.token_usage,
            request_id=state["request_id"],
        )
        self._timed(state, "generation_validation_ms", started)
        state["response"].latency_ms = state["latencies"]
        return state

    async def _cache_store(self, state: QueryState) -> QueryState:
        request, response = state["request"], state["response"]
        # A fallback is evidence-dependent and can become stale after ingestion,
        # retrieval tuning, or a transient provider issue. Cache only validated
        # answers so a previous no-answer never masks newly available evidence.
        if response.status in CACHEABLE_ANSWER_STATUSES:
            try:
                await asyncio.gather(
                    self.cache.set_exact(
                        request.query,
                        state["kb_version"],
                        state["config_version"],
                        response,
                        state["cache_scope"],
                    ),
                    self.cache.set_semantic(
                        request.query,
                        state["query_vector"],
                        state["kb_version"],
                        state["config_version"],
                        response,
                        state["cache_scope"],
                    ),
                )
            except Exception as exc:
                logger.warning("query_cache_write_failed", error_type=type(exc).__name__)
        return state

    @staticmethod
    def _after_cache(state: QueryState) -> str:
        return "done" if "response" in state else "classify"

    def _after_semantic_cache(self, state: QueryState) -> str:
        if "response" in state:
            return "done"
        request = state["request"]
        return (
            "transform"
            if state["route"] != QueryRoute.SIMPLE_RAG
            or request.knowledge_base_id == self.settings.rfp_knowledge_base_id
            else "retrieve"
        )

    def _after_assess(self, state: QueryState) -> str:
        result = state["answerability"]
        if result.sufficient:
            return "generate"
        if (
            result.should_retry
            and state.get("retry_count", 0) < self.settings.max_retrieval_retries
        ):
            return "retry"
        return "fallback"

    def _build_graph(self) -> Any:
        graph = StateGraph(QueryState)
        graph.add_node("cache_lookup", self._cache_lookup)
        graph.add_node("classify", self._classify)
        graph.add_node("semantic_cache", self._semantic_cache)
        graph.add_node("transform", self._transform)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("assess", self._assess)
        graph.add_node("retry", self._prepare_retry)
        graph.add_node("fallback", self._fallback)
        graph.add_node("generate", self._generate)
        graph.add_node("cache_store", self._cache_store)
        graph.add_edge(START, "cache_lookup")
        graph.add_conditional_edges(
            "cache_lookup", self._after_cache, {"done": END, "classify": "classify"}
        )
        graph.add_edge("classify", "semantic_cache")
        graph.add_conditional_edges(
            "semantic_cache",
            self._after_semantic_cache,
            {"done": END, "transform": "transform", "retrieve": "retrieve"},
        )
        graph.add_edge("transform", "retrieve")
        graph.add_edge("retrieve", "assess")
        graph.add_conditional_edges(
            "assess",
            self._after_assess,
            {"generate": "generate", "retry": "retry", "fallback": "fallback"},
        )
        graph.add_edge("retry", "transform")
        graph.add_edge("generate", "cache_store")
        graph.add_edge("fallback", "cache_store")
        graph.add_edge("cache_store", END)
        return graph.compile()

    async def answer(self, request: QueryRequest, request_id: str | None = None) -> QueryResponse:
        state: QueryState = {
            "request": request,
            "request_id": request_id or str(uuid.uuid4()),
            "latencies": {},
        }
        result = await self.graph.ainvoke(state)
        response: QueryResponse = result["response"]
        response.latency_ms.setdefault("total_ms", round(sum(response.latency_ms.values()), 3))
        return response
