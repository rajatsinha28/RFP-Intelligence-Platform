from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select

from app.db import get_session_factory
from app.ingestion.chunking import Chunker, ChunkingConfig, deduplicate_chunks
from app.ingestion.parsers import ParserRegistry
from app.models import ChunkRecord, Document, DocumentVersion, IngestionJob
from app.observability.logging import get_logger
from app.observability.metrics import INGESTION_COUNT
from app.services import get_services

logger = get_logger()


def _parsed_document_date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


async def _update_job(job_id: UUID, stage: str, progress: float, status: str = "running") -> None:
    async with get_session_factory()() as session:
        job = await session.get(IngestionJob, job_id)
        if job:
            job.stage = stage
            job.progress = progress
            job.status = status
            await session.commit()


async def ingest_document(job_id: UUID) -> None:
    services = get_services()
    settings = services.settings
    try:
        await _update_job(job_id, "loading", 0.05)
        async with get_session_factory()() as session:
            job = await session.get(IngestionJob, job_id)
            if not job:
                raise ValueError(f"Unknown ingestion job {job_id}")
            document = await session.get(Document, job.document_id)
            if not document:
                raise ValueError(f"Unknown document {job.document_id}")
            version_result = await session.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document.id)
                .order_by(DocumentVersion.version.desc())
                .limit(1)
            )
            version = version_result.scalar_one()
            document.status = "processing"
            version.status = "processing"
            await session.commit()
            document_id = str(document.id)
            knowledge_base_id = document.knowledge_base_id
            path = Path(version.storage_path)
            mime_type = document.mime_type
            source_url = document.source_url
            base_metadata = {
                "document_id": document_id,
                "document_version": version.version,
                "source_type": document.source_type,
                "source_name": document.source_name,
                "source_path": document.source_path or "",
                "source_url": document.source_url or "",
                "document_title": document.title,
                "mime_type": document.mime_type,
                "metadata_version": "1",
                "created_at": document.created_at.isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "bid_id": str(document.bid_id) if document.bid_id else "",
                "doc_type": document.doc_type or "",
                "addendum_number": document.addendum_number,
                "document_date": document.document_date.isoformat()
                if document.document_date
                else "",
                "relative_path": document.relative_path or "",
            }

        await _update_job(job_id, "parsing", 0.15)
        parsed = ParserRegistry(settings.ocr_languages).parse(path, mime_type, source_url)
        await _update_job(job_id, "chunking", 0.30)
        chunker = Chunker(
            ChunkingConfig(
                strategy=settings.chunk_strategy,
                chunk_size=settings.chunk_size,
                overlap=settings.chunk_overlap,
                min_size=settings.min_chunk_size,
                max_size=settings.max_chunk_size,
            )
        )
        chunks = deduplicate_chunks(
            chunker.chunk(parsed, document_id, version.version, base_metadata)
        )
        if not chunks:
            raise ValueError("No chunks were produced from the document")

        await _update_job(job_id, "embedding", 0.45)
        vectors: list[list[float] | None] = [None] * len(chunks)
        missing_indices: list[int] = []
        for index, chunk in enumerate(chunks):
            try:
                vectors[index] = await services.cache.get_embedding(
                    chunk.content_hash, settings.embedding_model
                )
            except Exception:
                vectors[index] = None
            if vectors[index] is None:
                missing_indices.append(index)
        if missing_indices:
            embedded = await services.embedder.embed_documents(
                [chunks[index].text for index in missing_indices]
            )
            for index, vector in zip(missing_indices, embedded, strict=True):
                vectors[index] = vector
                try:
                    await services.cache.set_embedding(
                        chunks[index].content_hash, settings.embedding_model, vector
                    )
                except Exception as exc:
                    logger.warning(
                        "embedding_cache_write_failed",
                        chunk_id=chunks[index].chunk_id,
                        error_type=type(exc).__name__,
                    )
        final_vectors = [vector for vector in vectors if vector is not None]
        if len(final_vectors) != len(chunks):
            raise RuntimeError("Embedding pipeline returned incomplete vectors")

        await _update_job(job_id, "indexing", 0.70)
        namespace = f"kb-{knowledge_base_id}"
        await services.vector_store.delete_document(document_id, namespace)
        await services.vector_store.upsert(chunks, final_vectors, namespace)

        await _update_job(job_id, "persisting", 0.88)
        async with get_session_factory()() as session:
            document = await session.get(Document, UUID(document_id))
            if not document:
                raise ValueError(f"Document {document_id} disappeared during ingestion")
            version_result = await session.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == UUID(document_id))
                .order_by(DocumentVersion.version.desc())
                .limit(1)
            )
            version = version_result.scalar_one()
            await session.execute(delete(ChunkRecord).where(ChunkRecord.version_id == version.id))
            session.add_all(
                [
                    ChunkRecord(
                        version_id=version.id,
                        document_id=document.id,
                        document_version=version.version,
                        chunk_id=chunk.chunk_id,
                        parent_chunk_id=chunk.parent_chunk_id,
                        chunk_index=chunk.chunk_index,
                        content=chunk.text,
                        content_hash=chunk.content_hash,
                        metadata_json=chunk.metadata,
                    )
                    for chunk in chunks
                ]
            )
            version.chunk_count = len(chunks)
            version.status = "active"
            document.status = "active"
            document.title = parsed.title or document.title
            if document.document_date is None:
                document.document_date = _parsed_document_date(parsed.metadata.get("document_date"))
            job = await session.get(IngestionJob, job_id)
            if not job:
                raise ValueError(f"Ingestion job {job_id} disappeared during ingestion")
            job.status = "completed"
            job.stage = "completed"
            job.progress = 1.0
            await session.commit()
        try:
            await services.cache.bump_kb_version(knowledge_base_id)
        except Exception as exc:
            logger.warning(
                "knowledge_base_cache_invalidation_failed",
                knowledge_base_id=knowledge_base_id,
                error_type=type(exc).__name__,
            )
        INGESTION_COUNT.labels("completed").inc()
        logger.info(
            "ingestion_completed",
            document_id=document_id,
            job_id=str(job_id),
            chunk_count=len(chunks),
        )
    except Exception as exc:
        INGESTION_COUNT.labels("failed").inc()
        async with get_session_factory()() as session:
            job = await session.get(IngestionJob, job_id)
            if job:
                job.status = "failed"
                job.stage = "failed"
                job.error = f"{type(exc).__name__}: {exc}"[:2000]
                document = await session.get(Document, job.document_id)
                if document:
                    document.status = "failed"
                await session.commit()
        logger.exception("ingestion_failed", job_id=str(job_id), error_type=type(exc).__name__)
        raise
