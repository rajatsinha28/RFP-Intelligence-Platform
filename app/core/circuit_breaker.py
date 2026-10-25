from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

from app.core.exceptions import CircuitOpenError

T = TypeVar("T")


@dataclass(slots=True)
class AsyncCircuitBreaker:
    """Small per-process circuit breaker for bounded external-service failures."""

    name: str
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 30.0
    _failure_count: int = 0
    _open_until: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def call(self, operation: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            now = time.monotonic()
            if self._open_until > now:
                raise CircuitOpenError(
                    f"{self.name} circuit is open for {self._open_until - now:.1f} more seconds"
                )

        try:
            result = await operation()
        except Exception:
            async with self._lock:
                self._failure_count += 1
                if self._failure_count >= self.failure_threshold:
                    self._open_until = time.monotonic() + self.recovery_timeout_seconds
            raise

        async with self._lock:
            self._failure_count = 0
            self._open_until = 0.0
        return result
