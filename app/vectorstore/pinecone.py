from __future__ import annotations

import asyncio
from collections.abc import Sequence
from functools import partial
from typing import Any

from pinecone import NotFoundError, Pinecone, ServerlessSpec
from pinecone_text.sparse import BM25Encoder

from app.core.exceptions import ConfigurationError, ExternalServiceError
from app.ingestion.types import Chunk
from app.schemas import RetrievalHit


class PineconeVectorStore:
    def __init__(
        self,
        api_key: str | None,
        index_name: str,
        namespace: str,
        dimensions: int,
        cloud: str = "aws",
        region: str = "us-east-1",
        auto_create: bool = True,
        query_timeout_seconds: float = 20.0,
    ) -> None:
        self.index_name = index_name
        self.namespace = namespace
        self.dimensions = dimensions
        self.cloud = cloud
        self.region = region
        self.auto_create = auto_create
        self.query_timeout_seconds = query_timeout_seconds
        self._client = (
            Pinecone(api_key=api_key, timeout=query_timeout_seconds) if api_key else None
        )
        self._index: Any = None
        self._bm25 = BM25Encoder.default()

    def _require_client(self) -> Pinecone:
        if not self._client:
            raise ConfigurationError("PINECONE_API_KEY is required for vector storage")
        return self._client

    async def initialize(self) -> None:
        client = self._require_client()
        names = await asyncio.to_thread(lambda: [item.name for item in client.list_indexes()])
        if self.index_name not in names:
            if not self.auto_create:
                raise ConfigurationError(f"Pinecone index {self.index_name!r} does not exist")
            await asyncio.to_thread(
                client.create_index,
                name=self.index_name,
                dimension=self.dimensions,
                metric="dotproduct",
                spec=ServerlessSpec(cloud=self.cloud, region=self.region),
                deletion_protection="disabled",
                tags={"application": "advance-rag"},
            )
        description = await asyncio.to_thread(client.describe_index, self.index_name)
        dimension = getattr(description, "dimension", None)
        if dimension is not None and int(dimension) != self.dimensions:
            message = (
                f"Pinecone dimension {dimension} does not match "
                f"EMBEDDING_DIMENSIONS={self.dimensions}"
            )
            raise ConfigurationError(message)
        self._index = client.Index(self.index_name)

    async def _get_index(self) -> Any:
        if self._index is None:
            await self.initialize()
        return self._index

    async def upsert(
        self,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
        namespace: str | None = None,
    ) -> int:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have equal lengths")
        index = await self._get_index()
        records: list[dict[str, Any]] = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            sparse = self._bm25.encode_documents(chunk.text)
            metadata = {**chunk.metadata, "text": chunk.text[:30000]}
            records.append(
                {
                    "id": chunk.chunk_id,
                    "values": list(vector),
                    "sparse_values": sparse,
                    "metadata": metadata,
                }
            )
        try:
            for offset in range(0, len(records), 100):
                batch = records[offset : offset + 100]
                await asyncio.to_thread(
                    partial(index.upsert, vectors=batch, namespace=namespace or self.namespace)
                )
        except Exception as exc:
            raise ExternalServiceError(f"Pinecone upsert failed: {type(exc).__name__}") from exc
        return len(records)

    async def _query(
        self,
        vector: Sequence[float],
        sparse_vector: dict[str, Any] | None,
        top_k: int,
        metadata_filter: dict[str, Any],
        namespace: str,
    ) -> list[RetrievalHit]:
        index = await self._get_index()
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    partial(
                        index.query,
                        vector=list(vector),
                        sparse_vector=sparse_vector,
                        top_k=top_k,
                        include_metadata=True,
                        include_values=True,
                        filter=metadata_filter or None,
                        namespace=namespace,
                    )
                ),
                timeout=self.query_timeout_seconds,
            )
        except TimeoutError as exc:
            raise ExternalServiceError(
                f"Pinecone query timed out after {self.query_timeout_seconds:g} seconds"
            ) from exc
        except Exception as exc:
            raise ExternalServiceError(f"Pinecone query failed: {type(exc).__name__}") from exc
        hits: list[RetrievalHit] = []
        for match in response.matches:
            metadata = dict(match.metadata or {})
            hits.append(
                RetrievalHit(
                    chunk_id=str(match.id),
                    document_id=str(metadata.get("document_id", "")),
                    text=str(metadata.pop("text", "")),
                    source_name=str(metadata.get("source_name", "unknown")),
                    source_url=metadata.get("source_url"),
                    page_number=metadata.get("page_number"),
                    section=metadata.get("section"),
                    metadata=metadata,
                    fusion_score=float(match.score or 0),
                    vector=list(match.values) if getattr(match, "values", None) else None,
                )
            )
        return hits

    async def dense_search(
        self,
        query_vector: Sequence[float],
        top_k: int,
        metadata_filter: dict[str, Any] | None = None,
        namespace: str | None = None,
    ) -> list[RetrievalHit]:
        hits = await self._query(
            query_vector, None, top_k, metadata_filter or {}, namespace or self.namespace
        )
        for hit in hits:
            hit.dense_score = hit.fusion_score
        return hits

    async def sparse_search(
        self,
        query: str,
        top_k: int,
        metadata_filter: dict[str, Any] | None = None,
        namespace: str | None = None,
    ) -> list[RetrievalHit]:
        sparse = self._bm25.encode_queries(query)
        hits = await self._query(
            [0.0] * self.dimensions,
            sparse,
            top_k,
            metadata_filter or {},
            namespace or self.namespace,
        )
        for hit in hits:
            hit.sparse_score = hit.fusion_score
        return hits

    async def delete_document(self, document_id: str, namespace: str | None = None) -> None:
        index = await self._get_index()
        try:
            await asyncio.to_thread(
                partial(
                    index.delete,
                    filter={"document_id": {"$eq": document_id}},
                    namespace=namespace or self.namespace,
                )
            )
        except NotFoundError as exc:
            # Pinecone does not materialize a namespace until its first upsert.
            # Treat an empty namespace as already having no vectors to delete.
            if exc.status_code == 404:
                return
            raise ExternalServiceError(f"Pinecone delete failed: {type(exc).__name__}") from exc
        except Exception as exc:
            raise ExternalServiceError(f"Pinecone delete failed: {type(exc).__name__}") from exc

    async def health_check(self) -> bool:
        try:
            await self.initialize()
            return True
        except Exception:
            return False
