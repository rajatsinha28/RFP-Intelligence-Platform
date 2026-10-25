from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import enforce_api_key, enforce_rate_limit
from app.api.documents import persist_upload
from app.config import get_settings
from app.db import get_session
from app.models import Bid, Document, ExtractionRun
from app.rfp.classification import classify_document
from app.rfp.schemas import ExtractionRunRead, ExtractionStartResponse
from app.rfp.search import rfp_query_variants
from app.schemas import (
    BidCreate,
    BidDetail,
    BidList,
    BidRead,
    FolderImportRequest,
    FolderImportResult,
    RetrievalHit,
    SearchRequest,
    SearchResponse,
    UploadResponse,
)
from app.security.files import detect_mime, safe_filename
from app.services import get_services
from app.worker import run_extraction_task

router = APIRouter(
    prefix="/bids",
    tags=["bids"],
    dependencies=[Depends(enforce_api_key), Depends(enforce_rate_limit)],
)
search_router = APIRouter(
    prefix="/search",
    tags=["search"],
    dependencies=[Depends(enforce_api_key), Depends(enforce_rate_limit)],
)
extraction_router = APIRouter(
    prefix="/extractions",
    tags=["extractions"],
    dependencies=[Depends(enforce_api_key), Depends(enforce_rate_limit)],
)

SUPPORTED_SUFFIXES = {
    ".pdf",
    ".html",
    ".htm",
    ".txt",
    ".md",
    ".docx",
    ".csv",
    ".json",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".webp",
}


async def _get_or_create_bid(
    session: AsyncSession,
    bid_key: str,
    title: str,
    knowledge_base_id: str,
    source_folder: str | None = None,
) -> Bid:
    result = await session.execute(
        select(Bid).where(Bid.bid_key == bid_key, Bid.knowledge_base_id == knowledge_base_id)
    )
    bid = result.scalar_one_or_none()
    if bid:
        return bid
    bid = Bid(
        bid_key=bid_key,
        title=title,
        knowledge_base_id=knowledge_base_id,
        source_folder=source_folder,
        status="created",
    )
    session.add(bid)
    await session.commit()
    await session.refresh(bid)
    return bid


@router.post("", response_model=BidRead, status_code=status.HTTP_201_CREATED)
async def create_bid(payload: BidCreate, session: AsyncSession = Depends(get_session)) -> Bid:
    existing = await session.scalar(
        select(Bid).where(
            Bid.bid_key == payload.bid_key,
            Bid.knowledge_base_id == payload.knowledge_base_id,
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="Bid already exists")
    return await _get_or_create_bid(
        session,
        payload.bid_key,
        payload.title,
        payload.knowledge_base_id,
        payload.source_folder,
    )


@router.get("", response_model=BidList)
async def list_bids(
    knowledge_base_id: str = "rfp-intelligence",
    session: AsyncSession = Depends(get_session),
) -> BidList:
    count = await session.scalar(
        select(func.count(Bid.id)).where(Bid.knowledge_base_id == knowledge_base_id)
    )
    result = await session.execute(
        select(Bid)
        .where(Bid.knowledge_base_id == knowledge_base_id)
        .order_by(Bid.updated_at.desc())
    )
    return BidList(items=list(result.scalars()), total=int(count or 0))


@router.get("/{bid_key}", response_model=BidDetail)
async def get_bid(bid_key: str, session: AsyncSession = Depends(get_session)) -> BidDetail:
    result = await session.execute(
        select(Bid).options(selectinload(Bid.documents)).where(Bid.bid_key == bid_key)
    )
    bid = result.scalar_one_or_none()
    if not bid:
        raise HTTPException(status_code=404, detail="Bid not found")
    return BidDetail.model_validate(bid)


@router.post(
    "/{bid_key}/extractions",
    response_model=ExtractionStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_extraction(
    bid_key: str, session: AsyncSession = Depends(get_session)
) -> ExtractionStartResponse:
    bid = await session.scalar(select(Bid).where(Bid.bid_key == bid_key))
    if not bid:
        raise HTTPException(status_code=404, detail="Bid not found")
    active_count = await session.scalar(
        select(func.count(Document.id)).where(
            Document.bid_id == bid.id, Document.status == "active"
        )
    )
    if not active_count:
        raise HTTPException(status_code=409, detail="Bid has no active indexed documents")
    run = ExtractionRun(bid_id=bid.id, status="queued", mode="extraction")
    session.add(run)
    bid.status = "queued"
    await session.commit()
    await session.refresh(run)
    try:
        task = run_extraction_task.delay(str(run.id))
        run.celery_task_id = task.id
        await session.commit()
    except Exception as exc:
        run.status = "failed"
        run.error = f"Worker dispatch failed: {type(exc).__name__}"
        await session.commit()
        raise HTTPException(status_code=503, detail="Extraction worker unavailable") from exc
    return ExtractionStartResponse(run_id=run.id, bid_key=bid.bid_key, status=run.status)


@router.get("/{bid_key}/record")
async def get_bid_record(
    bid_key: str, session: AsyncSession = Depends(get_session)
) -> JSONResponse:
    bid = await session.scalar(select(Bid).where(Bid.bid_key == bid_key))
    if not bid:
        raise HTTPException(status_code=404, detail="Bid not found")
    result = await session.execute(
        select(ExtractionRun)
        .where(
            ExtractionRun.bid_id == bid.id,
            ExtractionRun.status.in_(["completed", "completed_with_issues"]),
        )
        .order_by(ExtractionRun.created_at.desc())
        .limit(1)
    )
    run = result.scalar_one_or_none()
    if not run or not run.output_json:
        raise HTTPException(status_code=404, detail="No completed bid record")
    return JSONResponse(run.output_json)


@extraction_router.get("/{run_id}", response_model=ExtractionRunRead)
async def get_extraction_run(
    run_id: UUID, session: AsyncSession = Depends(get_session)
) -> ExtractionRun:
    run = await session.get(ExtractionRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Extraction run not found")
    return run


@router.post("/{bid_key}/documents", response_model=list[UploadResponse])
async def upload_bid_documents(
    bid_key: str,
    files: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_session),
) -> list[UploadResponse]:
    bid = await session.scalar(select(Bid).where(Bid.bid_key == bid_key))
    if not bid:
        raise HTTPException(status_code=404, detail="Bid not found")
    settings = get_settings()
    responses: list[UploadResponse] = []
    for file in files:
        data = await file.read(settings.upload_limit_bytes + 1)
        if not data or len(data) > settings.upload_limit_bytes:
            raise HTTPException(status_code=413 if data else 400, detail="Invalid upload size")
        filename = safe_filename(file.filename or "document")
        mime_type = detect_mime(filename, data, file.content_type)
        doc_type, addendum_number = classify_document(filename)
        responses.append(
            await persist_upload(
                session,
                data,
                filename,
                mime_type,
                bid.knowledge_base_id,
                bid=bid,
                doc_type=doc_type,
                addendum_number=addendum_number,
                relative_path=filename,
            )
        )
    bid.status = "processing"
    await session.commit()
    return responses


@router.post("/import-folder", response_model=FolderImportResult)
async def import_folder(
    payload: FolderImportRequest,
    session: AsyncSession = Depends(get_session),
) -> FolderImportResult:
    settings = get_settings()
    if settings.environment == "production":
        raise HTTPException(status_code=403, detail="Local folder import is disabled in production")

    def discover() -> tuple[Path, list[Path], list[str]]:
        discovered_root = Path(payload.path).expanduser().resolve()
        if not discovered_root.is_dir():
            raise ValueError("Import path is not a directory")
        directories = sorted(
            path for path in discovered_root.iterdir() if path.is_dir() and not path.is_symlink()
        )
        root_files = [str(path.name) for path in discovered_root.iterdir() if path.is_file()]
        return discovered_root, directories, root_files

    try:
        root, bid_directories, ignored = await asyncio.to_thread(discover)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Import path is not a directory") from exc
    if not bid_directories:
        raise HTTPException(status_code=400, detail="No bid subfolders were found")
    bids: list[Bid] = []
    jobs: list[UploadResponse] = []
    for directory in bid_directories:
        bid = await _get_or_create_bid(
            session,
            directory.name,
            directory.name,
            payload.knowledge_base_id,
            str(directory),
        )
        bids.append(bid)
        for path in sorted(directory.rglob("*")):
            if (
                not path.is_file()
                or path.is_symlink()
                or path.suffix.lower() not in SUPPORTED_SUFFIXES
            ):
                if path.is_file():
                    ignored.append(str(path.relative_to(root)))
                continue
            resolved = path.resolve()
            if directory.resolve() not in resolved.parents:
                ignored.append(str(path.relative_to(root)))
                continue
            data = await asyncio.to_thread(path.read_bytes)
            if not data or len(data) > settings.upload_limit_bytes:
                ignored.append(str(path.relative_to(root)))
                continue
            filename = safe_filename(path.name)
            mime_type = detect_mime(filename, data, None)
            doc_type, addendum_number = classify_document(filename)
            jobs.append(
                await persist_upload(
                    session,
                    data,
                    filename,
                    mime_type,
                    bid.knowledge_base_id,
                    bid=bid,
                    doc_type=doc_type,
                    addendum_number=addendum_number,
                    relative_path=str(path.relative_to(directory)),
                )
            )
        bid.status = "processing"
        await session.commit()
    return FolderImportResult(bids=bids, document_jobs=jobs, ignored_files=ignored)


def _combine_filter(current: dict[str, object], key: str, values: Sequence[object]) -> None:
    if values:
        current[key] = {"$in": values}


@search_router.post("", response_model=SearchResponse)
async def search_bids(
    payload: SearchRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SearchResponse:
    metadata_filter: dict[str, object] = {}
    if payload.bid_keys:
        rows = await session.execute(
            select(Bid.id).where(
                Bid.knowledge_base_id == payload.knowledge_base_id,
                Bid.bid_key.in_(payload.bid_keys),
            )
        )
        bid_ids = [str(value) for value in rows.scalars()]
        if len(bid_ids) != len(set(payload.bid_keys)):
            raise HTTPException(status_code=404, detail="One or more bids were not found")
        _combine_filter(metadata_filter, "bid_id", bid_ids)
    _combine_filter(metadata_filter, "doc_type", list(payload.doc_types))
    _combine_filter(metadata_filter, "addendum_number", list(payload.addendum_numbers))
    _combine_filter(metadata_filter, "source_name", list(payload.source_names))

    services = get_services()
    llm_variants = await services.transformer.transform(payload.query, decompose=False)
    variants = rfp_query_variants(payload.query, llm_variants)
    mode = "dense" if payload.mode == "dense" else payload.mode
    results = await asyncio.gather(
        *(
            services.retriever.retrieve(
                query=variant,
                metadata_filter=metadata_filter,
                namespace=f"kb-{payload.knowledge_base_id}",
                mode=mode,
                top_k_override=payload.top_k,
            )
            for variant in variants
        )
    )
    by_id: dict[str, RetrievalHit] = {}
    for result in results:
        for hit in result.hits:
            previous = by_id.get(hit.chunk_id)
            score = max(hit.reranker_score, hit.fusion_score)
            if previous is None or score > max(previous.reranker_score, previous.fusion_score):
                hit.vector = None
                by_id[hit.chunk_id] = hit
    hits = sorted(
        by_id.values(), key=lambda hit: max(hit.reranker_score, hit.fusion_score), reverse=True
    )[: payload.top_k]
    return SearchResponse(
        query=payload.query,
        expanded_queries=variants,
        hits=hits,
        dense_results=sum(item.dense_count for item in results),
        bm25_results=sum(item.sparse_count for item in results),
        request_id=getattr(request.state, "request_id", ""),
    )
