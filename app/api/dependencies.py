from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request, status

from app.config import get_settings
from app.services import Services, get_services


async def enforce_api_key(x_api_key: str | None = Header(default=None)) -> None:
    configured = get_settings().app_api_key
    if configured and not hmac.compare_digest(configured.get_secret_value(), x_api_key or ""):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


async def enforce_rate_limit(request: Request) -> None:
    settings = get_settings()
    services = get_services()
    client = request.client.host if request.client else "unknown"
    minute = int(__import__("time").time() // 60)
    key = f"rag:rate:{client}:{minute}"
    try:
        count = await services.cache.redis.incr(key)
        if count == 1:
            await services.cache.redis.expire(key, 65)
        if count > settings.rate_limit_per_minute:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
    except HTTPException:
        raise
    except Exception:
        # Redis failure must not take down document retrieval; it is surfaced by readiness.
        return


def services_dependency() -> Services:
    return get_services()
