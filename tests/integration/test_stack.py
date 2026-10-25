import os

import httpx
import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_running_stack_liveness() -> None:
    if os.getenv("RUN_STACK_TESTS") != "1":
        pytest.skip("Set RUN_STACK_TESTS=1 after docker compose up")
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
