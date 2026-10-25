from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import enforce_api_key, enforce_rate_limit
from app.db import get_session
from app.models import QueryLog
from app.observability.logging import get_logger
from app.observability.metrics import QUERY_COUNT
from app.schemas import QueryRequest, QueryResponse
from app.services import get_services

router = APIRouter(
    prefix="/query",
    tags=["query"],
    dependencies=[Depends(enforce_api_key), Depends(enforce_rate_limit)],
)
logger = get_logger()


async def _log_query(session: AsyncSession, request: QueryRequest, response: QueryResponse) -> None:
    session.add(
        QueryLog(
            request_id=response.request_id,
            knowledge_base_id=request.knowledge_base_id,
            query_hash=hashlib.sha256(request.query.encode()).hexdigest(),
            route=response.route.value,
            model_used=response.model_used,
            cache_hit=response.cache_hit,
            confidence=response.confidence,
            answer_status=response.status.value,
            citation_count=len(response.citations),
            latency_ms=response.latency_ms,
            token_usage=response.token_usage.model_dump(),
            retrieval_metadata=response.retrieval_metadata.model_dump(),
        )
    )
    await session.commit()


@router.post("", response_model=QueryResponse)
async def query(
    payload: QueryRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> QueryResponse:
    request_id = getattr(request.state, "request_id", None)
    try:
        response = await get_services().engine.answer(payload, request_id)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"RAG pipeline failed: {type(exc).__name__}"
        ) from exc
    await _log_query(session, payload, response)
    QUERY_COUNT.labels(response.route.value, response.status.value).inc()
    return response


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/stream")
async def query_stream(
    payload: QueryRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    request_id = getattr(request.state, "request_id", None)

    async def events() -> AsyncIterator[str]:
        yield _sse("status", {"stage": "retrieving", "request_id": request_id})
        try:
            response = await get_services().engine.answer(payload, request_id)
            await _log_query(session, payload, response)
            QUERY_COUNT.labels(response.route.value, response.status.value).inc()
            yield _sse(
                "status",
                {
                    "stage": "validated",
                    "confidence": response.confidence,
                    "route": response.route.value,
                },
            )
            words = response.answer.split(" ")
            for index in range(0, len(words), 8):
                if await request.is_disconnected():
                    return
                text = " ".join(words[index : index + 8])
                if index + 8 < len(words):
                    text += " "
                yield _sse("answer_delta", {"text": text})
                await asyncio.sleep(0)
            for citation in response.citations:
                yield _sse("citation", citation.model_dump())
            yield _sse("done", response.model_dump())
        except Exception as exc:
            logger.error(
                "query_stream_failed",
                request_id=request_id,
                error_type=type(exc).__name__,
            )
            yield _sse("error", {"message": "Query failed", "type": type(exc).__name__})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
