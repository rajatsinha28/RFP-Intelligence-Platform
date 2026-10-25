import os

import pytest

from app.services import get_services

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_openai_embedding_and_pinecone_index() -> None:
    if not os.getenv("OPENAI_API_KEY") or not os.getenv("PINECONE_API_KEY"):
        pytest.skip("OPENAI_API_KEY and PINECONE_API_KEY are required")
    services = get_services()
    vector = await services.embedder.embed_query("Advance RAG live integration check")
    assert len(vector) == services.settings.embedding_dimensions
    await services.vector_store.initialize()
    assert await services.vector_store.health_check()
