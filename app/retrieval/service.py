from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.core.exceptions import ExternalServiceError
from app.embeddings.openai import OpenAIEmbedder
from app.retrieval.fusion import reciprocal_rank_fusion, weighted_fusion
from app.retrieval.mmr import maximal_marginal_relevance
from app.retrieval.reranking import Reranker
from app.schemas import RetrievalHit
from app.vectorstore.pinecone import PineconeVectorStore


@dataclass(slots=True)
class RetrievalResult:
    hits: list[RetrievalHit]
    query_vector: list[float]
    dense_count: int
    sparse_count: int
    fused_count: int


class HybridRetriever:
    def __init__(
        self,
        embedder: OpenAIEmbedder,
        vector_store: PineconeVectorStore,
        reranker: Reranker,
        dense_top_k: int,
        bm25_top_k: int,
        rerank_top_k: int,
        rrf_k: int,
        hybrid_alpha: float,
        mmr_lambda: float,
    ) -> None:
        self.embedder = embedder
        self.vector_store = vector_store
        self.reranker = reranker
        self.dense_top_k = dense_top_k
        self.bm25_top_k = bm25_top_k
        self.rerank_top_k = rerank_top_k
        self.rrf_k = rrf_k
        self.hybrid_alpha = hybrid_alpha
        self.mmr_lambda = mmr_lambda

    async def retrieve(
        self,
        query: str,
        metadata_filter: dict[str, object],
        namespace: str,
        use_weighted_fusion: bool = False,
        query_vector: list[float] | None = None,
        mode: str = "hybrid_rerank",
        top_k_override: int | None = None,
    ) -> RetrievalResult:
        query_vector = query_vector or await self.embedder.embed_query(query)
        if mode == "dense":
            dense = await self.vector_store.dense_search(
                query_vector, self.dense_top_k, metadata_filter, namespace
            )
            sparse: list[RetrievalHit] = []
        else:
            dense_result, sparse_result = await asyncio.gather(
                self.vector_store.dense_search(
                    query_vector, self.dense_top_k, metadata_filter, namespace
                ),
                self.vector_store.sparse_search(query, self.bm25_top_k, metadata_filter, namespace),
                return_exceptions=True,
            )
            dense = [] if isinstance(dense_result, BaseException) else dense_result
            sparse = [] if isinstance(sparse_result, BaseException) else sparse_result
            if not dense and not sparse:
                errors = [
                    type(result).__name__
                    for result in (dense_result, sparse_result)
                    if isinstance(result, BaseException)
                ]
                raise ExternalServiceError(
                    f"Dense and sparse retrieval both failed: {', '.join(errors) or 'no results'}"
                )
        fused = (
            weighted_fusion(dense, sparse, self.hybrid_alpha)
            if use_weighted_fusion
            else reciprocal_rank_fusion([dense, sparse], self.rrf_k)
        )
        output_k = top_k_override or self.rerank_top_k
        if mode == "hybrid_rerank":
            diversified = maximal_marginal_relevance(
                fused, query_vector, max(output_k * 2, output_k), self.mmr_lambda
            )
            final_hits = await self.reranker.rerank(query, diversified, output_k)
            final_hits = preserve_source_coverage(fused[:output_k], final_hits, output_k)
        else:
            final_hits = fused[:output_k]
        return RetrievalResult(
            hits=final_hits,
            query_vector=query_vector,
            dense_count=len(dense),
            sparse_count=len(sparse),
            fused_count=len(fused),
        )


def preserve_source_coverage(
    baseline: list[RetrievalHit], reranked: list[RetrievalHit], top_k: int
) -> list[RetrievalHit]:
    """Do not let diversification/reranking erase a source already present at top-k."""

    result = list(reranked[:top_k])
    present = {hit.source_name for hit in result}
    for candidate in baseline:
        if candidate.source_name in present:
            continue
        counts = {source: sum(hit.source_name == source for hit in result) for source in present}
        replace_at = next(
            (
                index
                for index in range(len(result) - 1, -1, -1)
                if counts.get(result[index].source_name, 0) > 1
            ),
            None,
        )
        if replace_at is None:
            if len(result) < top_k:
                result.append(candidate)
                present.add(candidate.source_name)
            continue
        removed = result[replace_at].source_name
        result[replace_at] = candidate
        present.add(candidate.source_name)
        if not any(hit.source_name == removed for hit in result):
            present.discard(removed)
    return result
