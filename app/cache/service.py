from __future__ import annotations

import json
import math
import struct

from redis.asyncio import Redis

from app.core.hashing import normalize_query, stable_hash
from app.observability.metrics import CACHE_COUNT
from app.schemas import QueryResponse


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    return numerator / denominator if denominator else 0.0


class CacheService:
    def __init__(
        self,
        redis_url: str,
        ttl_seconds: int,
        embedding_ttl_seconds: int,
        semantic_threshold: float,
        semantic_max_candidates: int,
    ) -> None:
        self.redis = Redis.from_url(redis_url, decode_responses=False)
        self.ttl_seconds = ttl_seconds
        self.embedding_ttl_seconds = embedding_ttl_seconds
        self.semantic_threshold = semantic_threshold
        self.semantic_max_candidates = semantic_max_candidates

    @staticmethod
    def exact_key(query: str, kb_version: str, config_version: str, scope: str = "global") -> str:
        digest = stable_hash(
            {
                "query": normalize_query(query),
                "kb": kb_version,
                "config": config_version,
                "scope": scope,
            }
        )
        return f"rag:exact:{digest}"

    async def get_exact(
        self, query: str, kb_version: str, config_version: str, scope: str = "global"
    ) -> QueryResponse | None:
        raw = await self.redis.get(self.exact_key(query, kb_version, config_version, scope))
        CACHE_COUNT.labels("exact", "hit" if raw else "miss").inc()
        return QueryResponse.model_validate_json(raw) if raw else None

    async def set_exact(
        self,
        query: str,
        kb_version: str,
        config_version: str,
        response: QueryResponse,
        scope: str = "global",
    ) -> None:
        await self.redis.setex(
            self.exact_key(query, kb_version, config_version, scope),
            self.ttl_seconds,
            response.model_dump_json(),
        )

    async def get_semantic(
        self,
        query_vector: list[float],
        kb_version: str,
        config_version: str,
        scope: str = "global",
    ) -> QueryResponse | None:
        pattern = f"rag:semantic:{kb_version}:{config_version}:{scope}:*"
        best: tuple[float, QueryResponse] | None = None
        count = 0
        async for key in self.redis.scan_iter(match=pattern, count=100):
            values = await self.redis.hmget(key, "embedding", "response")
            if not values[0] or not values[1]:
                continue
            vector = list(struct.unpack(f"<{len(values[0]) // 4}f", values[0]))
            score = cosine_similarity(query_vector, vector)
            if score >= self.semantic_threshold and (best is None or score > best[0]):
                best = (score, QueryResponse.model_validate_json(values[1]))
            count += 1
            if count >= self.semantic_max_candidates:
                break
        CACHE_COUNT.labels("semantic", "hit" if best else "miss").inc()
        return best[1] if best else None

    async def set_semantic(
        self,
        query: str,
        query_vector: list[float],
        kb_version: str,
        config_version: str,
        response: QueryResponse,
        scope: str = "global",
    ) -> None:
        key = (
            f"rag:semantic:{kb_version}:{config_version}:{scope}:"
            f"{stable_hash(normalize_query(query))}"
        )
        packed = struct.pack(f"<{len(query_vector)}f", *query_vector)
        await self.redis.hset(
            key,
            mapping={"embedding": packed, "response": response.model_dump_json().encode()},
        )
        await self.redis.expire(key, self.ttl_seconds)

    async def clear_query_cache(self, kb_version: str, config_version: str) -> int:
        """Remove response caches while preserving embeddings and KB metadata."""
        patterns = [
            "rag:exact:*",
            f"rag:semantic:{kb_version}:{config_version}:*",
        ]
        deleted = 0
        for pattern in patterns:
            keys = [key async for key in self.redis.scan_iter(match=pattern, count=100)]
            if keys:
                deleted += int(await self.redis.delete(*keys))
        return deleted

    async def get_embedding(self, content_hash: str, model_version: str) -> list[float] | None:
        raw = await self.redis.get(f"rag:embedding:{model_version}:{content_hash}")
        if not raw:
            return None
        decoded = json.loads(raw)
        if not isinstance(decoded, list):
            return None
        return [float(value) for value in decoded]

    async def set_embedding(
        self, content_hash: str, model_version: str, vector: list[float]
    ) -> None:
        await self.redis.setex(
            f"rag:embedding:{model_version}:{content_hash}",
            self.embedding_ttl_seconds,
            json.dumps(vector),
        )

    async def kb_version(self, knowledge_base_id: str) -> str:
        key = f"rag:kb-version:{knowledge_base_id}"
        value = await self.redis.get(key)
        if value is None:
            await self.redis.set(key, b"1", nx=True)
            return "1"
        return value.decode() if isinstance(value, bytes) else str(value)

    async def bump_kb_version(self, knowledge_base_id: str) -> str:
        return str(await self.redis.incr(f"rag:kb-version:{knowledge_base_id}"))

    async def health_check(self) -> bool:
        try:
            return bool(await self.redis.ping())
        except Exception:
            return False

    async def close(self) -> None:
        await self.redis.aclose()
