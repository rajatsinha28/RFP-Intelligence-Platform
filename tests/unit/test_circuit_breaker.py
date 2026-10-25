from __future__ import annotations

import pytest

from app.core.circuit_breaker import AsyncCircuitBreaker
from app.core.exceptions import CircuitOpenError


@pytest.mark.asyncio
async def test_circuit_opens_after_bounded_failures() -> None:
    breaker = AsyncCircuitBreaker("test service", failure_threshold=2, recovery_timeout_seconds=60)
    calls = 0

    async def failing_operation() -> None:
        nonlocal calls
        calls += 1
        raise ConnectionError("offline")

    with pytest.raises(ConnectionError):
        await breaker.call(failing_operation)
    with pytest.raises(ConnectionError):
        await breaker.call(failing_operation)
    with pytest.raises(CircuitOpenError):
        await breaker.call(failing_operation)
    assert calls == 2


@pytest.mark.asyncio
async def test_success_resets_failure_count() -> None:
    breaker = AsyncCircuitBreaker("test service", failure_threshold=2)

    async def failing_operation() -> None:
        raise ConnectionError("offline")

    async def successful_operation() -> str:
        return "ok"

    with pytest.raises(ConnectionError):
        await breaker.call(failing_operation)
    assert await breaker.call(successful_operation) == "ok"
    with pytest.raises(ConnectionError):
        await breaker.call(failing_operation)
