from __future__ import annotations

import asyncio
from collections.abc import Sequence

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from app.core.circuit_breaker import AsyncCircuitBreaker
from app.core.exceptions import ConfigurationError, ExternalServiceError

RETRYABLE_OPENAI_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)


class OpenAIEmbedder:
    def __init__(
        self,
        api_key: str | None,
        model: str,
        dimensions: int,
        batch_size: int = 64,
        base_url: str | None = None,
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        self.batch_size = batch_size
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url) if api_key else None
        self._breaker = AsyncCircuitBreaker("OpenAI embeddings")

    def _require_client(self) -> AsyncOpenAI:
        if not self._client:
            raise ConfigurationError("OPENAI_API_KEY is required for embeddings")
        return self._client

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_random_exponential(multiplier=1, max=20),
        retry=retry_if_exception_type(RETRYABLE_OPENAI_ERRORS),
        reraise=True,
    )
    async def _request_batch(self, client: AsyncOpenAI, texts: Sequence[str]):  # type: ignore[no-untyped-def]
        return await self._breaker.call(
            lambda: client.embeddings.create(
                model=self.model,
                input=list(texts),
                dimensions=self.dimensions,
                encoding_format="float",
            )
        )

    async def _embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            response = await self._request_batch(self._require_client(), texts)
        except Exception as exc:
            raise ExternalServiceError(
                f"OpenAI embedding request failed: {type(exc).__name__}"
            ) from exc
        vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
        if any(len(vector) != self.dimensions for vector in vectors):
            raise ExternalServiceError("OpenAI returned an unexpected embedding dimension")
        return vectors

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        batches = [
            texts[index : index + self.batch_size]
            for index in range(0, len(texts), self.batch_size)
        ]
        results = await asyncio.gather(*(self._embed_batch(batch) for batch in batches))
        return [vector for batch in results for vector in batch]

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed_batch([text]))[0]

    async def health_check(self) -> bool:
        try:
            await self.embed_query("health check")
            return True
        except Exception:
            return False

    async def close(self) -> None:
        if self._client:
            await self._client.close()
