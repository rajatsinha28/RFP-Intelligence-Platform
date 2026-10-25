from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from uuid import UUID

from celery import Celery

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)
celery_app = Celery(
    "advance_rag",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=1800,
    task_soft_time_limit=1740,
)


async def _run_isolated(coroutine: Awaitable[None]) -> None:
    """Run one task and release event-loop-bound clients before asyncio.run exits."""

    from app.db import get_engine, get_session_factory
    from app.services import get_services

    try:
        await coroutine
    finally:
        if get_services.cache_info().currsize:
            try:
                await get_services().close()
            except Exception:
                logger.warning("Failed to close task service clients", exc_info=True)
        if get_engine.cache_info().currsize:
            try:
                await get_engine().dispose()
            except Exception:
                logger.warning("Failed to dispose task database engine", exc_info=True)
        get_services.cache_clear()
        get_session_factory.cache_clear()
        get_engine.cache_clear()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="advance_rag.ingest_document",
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    max_retries=3,
)
def ingest_document_task(job_id: str) -> None:
    from app.ingestion.pipeline import ingest_document

    asyncio.run(_run_isolated(ingest_document(UUID(job_id))))


@celery_app.task(name="advance_rag.run_evaluation")  # type: ignore[untyped-decorator]
def run_evaluation_task(evaluation_id: str) -> None:
    from evaluation.run import run_saved_evaluation

    asyncio.run(_run_isolated(run_saved_evaluation(UUID(evaluation_id))))


@celery_app.task(name="advance_rag.run_extraction")  # type: ignore[untyped-decorator]
def run_extraction_task(extraction_id: str) -> None:
    from app.rfp.extraction import run_extraction

    asyncio.run(_run_isolated(run_extraction(UUID(extraction_id))))
