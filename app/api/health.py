from __future__ import annotations

import asyncio

from fastapi import APIRouter, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app import __version__
from app.config import get_settings
from app.db import get_engine
from app.schemas import HealthResponse
from app.services import get_services

router = APIRouter(tags=["operations"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/ready", response_model=HealthResponse)
async def ready(response: Response) -> HealthResponse:
    settings = get_settings()
    try:
        services = get_services()
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="not_ready",
            version=__version__,
            dependencies={"service_initialization": "unavailable"},
        )

    async def database() -> tuple[bool, bool]:
        try:
            async with get_engine().connect() as connection:
                await connection.execute(text("SELECT 1"))
                migration = await connection.execute(
                    text("SELECT version_num FROM alembic_version LIMIT 1")
                )
                migration_ok = migration.scalar_one_or_none() is not None
            return True, migration_ok
        except Exception:
            return False, False

    database_state, redis_ok, pinecone_ok, embeddings_ok, models_ok = await asyncio.gather(
        database(),
        services.cache.health_check(),
        services.vector_store.health_check(),
        services.embedder.health_check(),
        services.generator.health_check(),
    )
    db_ok, migration_ok = database_state
    dependencies = {
        "postgres": "ok" if db_ok else "unavailable",
        "migrations": "ok" if migration_ok else "unavailable",
        "redis": "ok" if redis_ok else "unavailable",
        "pinecone": (
            "ok"
            if pinecone_ok
            else "missing_key"
            if not settings.pinecone_api_key
            else "unavailable"
        ),
        "openai_embeddings": (
            "ok" if embeddings_ok else "missing_key" if not settings.openai_key else "unavailable"
        ),
        "openai_models": (
            "ok" if models_ok else "missing_key" if not settings.openai_key else "unavailable"
        ),
    }
    ready_state = all(value == "ok" for value in dependencies.values())
    if not ready_state:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ready" if ready_state else "not_ready",
        version=__version__,
        dependencies=dependencies,
    )


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
