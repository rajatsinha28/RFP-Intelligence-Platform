from __future__ import annotations

import asyncio
import hashlib
import shutil
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import enforce_api_key, enforce_rate_limit
from app.config import get_settings
from app.db import get_session
from app.models import Bid, Document, DocumentVersion, IngestionJob
from app.observability.logging import get_logger
from app.rfp.classification import normalize_doc_type
from app.schemas import (
    DocumentList,
    DocumentRead,
    JobRead,
    JobStatus,
    UploadResponse,
    URLIngestRequest,
)
from app.security.files import detect_mime, safe_filename
from app.security.web import fetch_public_document
from app.services import get_services
from app.worker import ingest_document_task

logger = get_logger()

router = APIRouter(
    prefix="/documents",
    tags=["documents"],
    dependencies=[Depends(enforce_api_key), Depends(enforce_rate_limit)],
)


def _remove_document_files(storage_root: Path, document_id: UUID) -> None:
    root = storage_root.resolve()
    target = (root / str(document_id)).resolve()
    if target.parent != root:
        raise ValueError("Resolved document storage path escaped the configured root")
    if target.is_dir():
        shutil.rmtree(target)


async def persist_upload(
    session: AsyncSession,
    data: bytes,
    filename: str,
    mime_type: str,
    knowledge_base_id: str,
    source_url: str | None = None,
    title: str | None = None,
    existing_document: Document | None = None,
    bid: Bid | None = None,
    doc_type: str | None = None,
    addendum_number: int | None = None,
    relative_path: str | None = None,
) -> UploadResponse:
    settings = get_settings()
    digest = hashlib.sha256(data).hexdigest()
    filename = safe_filename(filename)
    if existing_document is None:
        duplicate_result = await session.execute(
            select(Document).where(
                Document.knowledge_base_id == knowledge_base_id,
                Document.content_hash == digest,
                Document.status != "deleted",
                Document.bid_id == (bid.id if bid else None),
            )
        )
        duplicate = duplicate_result.scalar_one_or_none()
        if duplicate:
            job_result = await session.execute(
                select(IngestionJob)
                .where(IngestionJob.document_id == duplicate.id)
                .order_by(IngestionJob.created_at.desc())
                .limit(1)
            )
            job = job_result.scalar_one_or_none()
            if job:
                return UploadResponse(
                    document_id=duplicate.id, job_id=job.id, status=JobStatus(job.status)
                )

    detected_type, detected_addendum = normalize_doc_type(doc_type, filename)
    document = existing_document or Document(
        knowledge_base_id=knowledge_base_id,
        title=title or Path(filename).stem,
        source_name=filename,
        source_type="web" if source_url else Path(filename).suffix.lower().lstrip("."),
        source_url=source_url,
        mime_type=mime_type,
        status="uploaded",
        current_version=1,
        content_hash=digest,
        size_bytes=len(data),
        bid_id=bid.id if bid else None,
        doc_type=detected_type if bid else None,
        addendum_number=(addendum_number if addendum_number is not None else detected_addendum)
        if bid
        else None,
        relative_path=relative_path,
    )
    if existing_document:
        document.current_version += 1
        document.status = "uploaded"
        document.content_hash = digest
        document.size_bytes = len(data)
        document.source_name = filename
        document.mime_type = mime_type
        if bid:
            document.bid_id = bid.id
            document.doc_type = detected_type
            document.addendum_number = (
                addendum_number if addendum_number is not None else detected_addendum
            )
            document.relative_path = relative_path
    session.add(document)
    await session.flush()

    directory = (settings.storage_path / str(document.id)).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    storage_path = directory / f"v{document.current_version}-{filename}"
    storage_path.write_bytes(data)
    document.source_path = str(storage_path)
    version = DocumentVersion(
        document_id=document.id,
        version=document.current_version,
        content_hash=digest,
        storage_path=str(storage_path),
        status="uploaded",
    )
    job = IngestionJob(document_id=document.id, status="queued", progress=0, stage="queued")
    session.add_all([version, job])
    await session.commit()
    try:
        result = ingest_document_task.delay(str(job.id))
        job.celery_task_id = result.id
        await session.commit()
    except Exception as exc:
        job.status = "failed"
        job.stage = "dispatch_failed"
        job.error = f"Worker dispatch failed: {type(exc).__name__}"
        await session.commit()
        raise HTTPException(status_code=503, detail="Ingestion worker is unavailable") from exc
    return UploadResponse(document_id=document.id, job_id=job.id, status=JobStatus.QUEUED)


@router.post("/upload", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    file: UploadFile = File(...),
    knowledge_base_id: str = "default",
    session: AsyncSession = Depends(get_session),
) -> UploadResponse:
    settings = get_settings()
    data = await file.read(settings.upload_limit_bytes + 1)
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")
    if len(data) > settings.upload_limit_bytes:
        raise HTTPException(status_code=413, detail="The uploaded file exceeds the size limit")
    filename = safe_filename(file.filename or "document")
    try:
        mime_type = detect_mime(filename, data, file.content_type)
    except Exception as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    return await persist_upload(session, data, filename, mime_type, knowledge_base_id)


@router.post("/ingest", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_url(
    request: URLIngestRequest, session: AsyncSession = Depends(get_session)
) -> UploadResponse:
    settings = get_settings()
    try:
        data, content_type = await fetch_public_document(
            str(request.url), settings.web_download_limit_bytes, settings.web_fetch_timeout_seconds
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    suffix_by_mime = {
        "application/pdf": ".pdf",
        "application/json": ".json",
        "text/plain": ".txt",
        "text/csv": ".csv",
        "text/html": ".html",
        "image/png": ".png",
        "image/jpeg": ".jpg",
    }
    parsed_name = Path(urlparse(str(request.url)).path).name
    suffix = Path(parsed_name).suffix or suffix_by_mime.get(content_type, ".html")
    filename = safe_filename(parsed_name or f"web-document{suffix}")
    if not Path(filename).suffix:
        filename += suffix
    try:
        mime_type = detect_mime(filename, data, content_type)
    except Exception as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    return await persist_upload(
        session,
        data,
        filename,
        mime_type,
        request.knowledge_base_id,
        source_url=str(request.url),
        title=request.title,
    )


@router.get("", response_model=DocumentList)
async def list_documents(
    knowledge_base_id: str = "default",
    offset: int = 0,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
) -> DocumentList:
    limit = min(max(limit, 1), 200)
    count = await session.scalar(
        select(func.count(Document.id)).where(
            Document.knowledge_base_id == knowledge_base_id, Document.status != "deleted"
        )
    )
    result = await session.execute(
        select(Document)
        .where(Document.knowledge_base_id == knowledge_base_id, Document.status != "deleted")
        .order_by(Document.updated_at.desc())
        .offset(max(offset, 0))
        .limit(limit)
    )
    return DocumentList(items=list(result.scalars()), total=int(count or 0))


@router.get("/{document_id:uuid}", response_model=DocumentRead)
async def get_document(document_id: UUID, session: AsyncSession = Depends(get_session)) -> Document:
    document = await session.get(Document, document_id)
    if not document or document.status == "deleted":
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.put(
    "/{document_id:uuid}", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED
)
async def update_document(
    document_id: UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> UploadResponse:
    document = await session.get(Document, document_id)
    if not document or document.status == "deleted":
        raise HTTPException(status_code=404, detail="Document not found")
    settings = get_settings()
    data = await file.read(settings.upload_limit_bytes + 1)
    if not data or len(data) > settings.upload_limit_bytes:
        raise HTTPException(status_code=413 if data else 400, detail="Invalid upload size")
    filename = safe_filename(file.filename or document.source_name)
    mime_type = detect_mime(filename, data, file.content_type)
    return await persist_upload(
        session,
        data,
        filename,
        mime_type,
        document.knowledge_base_id,
        existing_document=document,
    )


@router.delete("/{document_id:uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: UUID, session: AsyncSession = Depends(get_session)) -> None:
    document = await session.get(Document, document_id)
    if not document or document.status == "deleted":
        raise HTTPException(status_code=404, detail="Document not found")
    services = get_services()
    await services.vector_store.delete_document(
        str(document.id), f"kb-{document.knowledge_base_id}"
    )
    document.status = "deleted"
    await session.commit()
    try:
        await services.cache.bump_kb_version(document.knowledge_base_id)
    except Exception as exc:
        logger.warning("cache_invalidation_failed", error_type=type(exc).__name__)
    await asyncio.to_thread(_remove_document_files, get_settings().storage_path, document.id)


@router.get("/jobs/{job_id}", response_model=JobRead)
async def get_job(job_id: UUID, session: AsyncSession = Depends(get_session)) -> IngestionJob:
    job = await session.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    return job
