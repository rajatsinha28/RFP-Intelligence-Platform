from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod

import httpx

from app.core.exceptions import ConfigurationError, ExternalServiceError
from app.schemas import RetrievalHit

TOKEN = re.compile(r"[A-Za-z0-9_'-]+")
STOPWORDS = {
    "a",
    "about",
    "after",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "do",
    "does",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
}


def content_tokens(text: str) -> set[str]:
    return {
        token.lower().removesuffix("'s")
        for token in TOKEN.findall(text)
        if token.lower() not in STOPWORDS
    }


def entity_tokens(text: str) -> set[str]:
    return {
        token.lower().removesuffix("'s")
        for token in TOKEN.findall(text)
        if token[0].isupper() and len(token) > 2 and token.lower() not in STOPWORDS
    }


def lexical_rerank_score(query: str, hit: RetrievalHit) -> float:
    query_tokens = content_tokens(query)
    query_entities = entity_tokens(query)
    text_tokens = content_tokens(hit.text)
    overlap = len(query_tokens & text_tokens) / max(1, len(query_tokens))
    entity_overlap = len(query_entities & entity_tokens(hit.text)) / max(1, len(query_entities))
    return 0.55 * overlap + 0.25 * entity_overlap + 0.20 * min(1.0, hit.fusion_score * 100)


class Reranker(ABC):
    @abstractmethod
    async def rerank(self, query: str, hits: list[RetrievalHit], top_k: int) -> list[RetrievalHit]:
        raise NotImplementedError


class LexicalReranker(Reranker):
    async def rerank(self, query: str, hits: list[RetrievalHit], top_k: int) -> list[RetrievalHit]:
        reranked: list[RetrievalHit] = []
        for hit in hits:
            item = hit.model_copy(deep=True)
            item.reranker_score = lexical_rerank_score(query, hit)
            reranked.append(item)
        return sorted(reranked, key=lambda item: item.reranker_score, reverse=True)[:top_k]


class LocalCrossEncoderReranker(Reranker):
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model: object | None = None

    def _load(self) -> object:
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
        return self._model

    async def rerank(self, query: str, hits: list[RetrievalHit], top_k: int) -> list[RetrievalHit]:
        if not hits:
            return []
        model = await asyncio.to_thread(self._load)
        pairs = [(query, hit.text) for hit in hits]
        scores = await asyncio.to_thread(model.predict, pairs)  # type: ignore[attr-defined]
        reranked: list[RetrievalHit] = []
        for hit, score in zip(hits, scores, strict=True):
            item = hit.model_copy(deep=True)
            item.reranker_score = float(score)
            reranked.append(item)
        return sorted(reranked, key=lambda item: item.reranker_score, reverse=True)[:top_k]


class CohereReranker(Reranker):
    def __init__(self, api_key: str | None, model: str) -> None:
        if not api_key:
            raise ConfigurationError("COHERE_API_KEY is required for the Cohere reranker")
        self.api_key = api_key
        self.model = model

    async def rerank(self, query: str, hits: list[RetrievalHit], top_k: int) -> list[RetrievalHit]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.cohere.com/v2/rerank",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "query": query,
                    "documents": [hit.text for hit in hits],
                    "top_n": top_k,
                },
            )
        if response.is_error:
            raise ExternalServiceError(f"Cohere rerank failed with HTTP {response.status_code}")
        results = response.json()["results"]
        reranked: list[RetrievalHit] = []
        for result in results:
            item = hits[int(result["index"])].model_copy(deep=True)
            item.reranker_score = float(result["relevance_score"])
            reranked.append(item)
        return reranked


def build_reranker(provider: str, model: str, cohere_api_key: str | None = None) -> Reranker:
    if provider == "local":
        return LocalCrossEncoderReranker(model)
    if provider == "cohere":
        return CohereReranker(cohere_api_key, model)
    return LexicalReranker()
