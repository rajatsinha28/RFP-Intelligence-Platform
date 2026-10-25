from __future__ import annotations

import asyncio

import pytest
from pinecone import NotFoundError

from app.core.exceptions import ExternalServiceError
from app.vectorstore.pinecone import PineconeVectorStore


class EmptyNamespaceIndex:
    def delete(self, **_: object) -> None:
        raise NotFoundError("Namespace not found")


@pytest.mark.asyncio
async def test_delete_document_ignores_unmaterialized_namespace() -> None:
    store = object.__new__(PineconeVectorStore)
    store._index = EmptyNamespaceIndex()
    await store.delete_document("document", "kb-default")


class SlowIndex:
    def query(self, **_: object) -> object:
        import time

        time.sleep(0.1)
        return object()


@pytest.mark.asyncio
async def test_query_has_a_bounded_timeout() -> None:
    store = object.__new__(PineconeVectorStore)
    store._index = SlowIndex()
    store.query_timeout_seconds = 0.01
    with pytest.raises(ExternalServiceError, match="timed out"):
        await store._query([0.0], None, 1, {}, "kb-test")
    await asyncio.sleep(0.1)
