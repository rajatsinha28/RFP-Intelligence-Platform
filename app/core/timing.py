from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter


@contextmanager
def record_latency(target: dict[str, float], name: str) -> Iterator[None]:
    started = perf_counter()
    try:
        yield
    finally:
        target[name] = round((perf_counter() - started) * 1000, 3)
