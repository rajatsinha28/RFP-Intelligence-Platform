from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select

from app.db import get_session_factory
from app.models import ChunkRecord
from app.schemas import RetrievalHit


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _record_to_hit(record: ChunkRecord, anchor_id: str) -> RetrievalHit:
    metadata = dict(record.metadata_json or {})
    metadata["context_neighbor_of"] = anchor_id
    return RetrievalHit(
        chunk_id=record.chunk_id,
        document_id=str(record.document_id),
        text=record.content,
        source_name=str(metadata.get("source_name", "unknown")),
        source_url=metadata.get("source_url"),
        page_number=_positive_int(metadata.get("page_number")),
        section=metadata.get("section"),
        metadata=metadata,
    )


async def expand_adjacent_chunks(hits: list[RetrievalHit], radius: int = 1) -> list[RetrievalHit]:
    """Add nearby persisted chunks while retaining each chunk's own traceable ID.

    Retrieval often finds the sentence containing an action but not the preceding
    sentence that names a pronoun's subject. Immediate neighbors restore that local
    discourse without changing retrieval scores or fabricating merged citations.
    """

    if not hits or radius <= 0:
        return hits

    anchors: list[tuple[RetrievalHit, UUID, int, int]] = []
    filters = []
    for hit in hits:
        try:
            document_id = UUID(hit.document_id)
        except (TypeError, ValueError):
            continue
        version = _positive_int(hit.metadata.get("document_version"))
        index = _positive_int(hit.metadata.get("chunk_index"))
        if version is None or index is None:
            continue
        anchors.append((hit, document_id, version, index))
        filters.append(
            and_(
                ChunkRecord.document_id == document_id,
                ChunkRecord.document_version == version,
                ChunkRecord.chunk_index.between(max(0, index - radius), index + radius),
            )
        )

    if not filters:
        return hits

    async with get_session_factory()() as session:
        records = list((await session.scalars(select(ChunkRecord).where(or_(*filters)))).all())

    by_position = {
        (record.document_id, record.document_version, record.chunk_index): record
        for record in records
    }
    originals = {hit.chunk_id: hit for hit in hits}
    expanded: list[RetrievalHit] = []
    seen: set[str] = set()

    def append(items: Iterable[RetrievalHit]) -> None:
        for item in items:
            if item.chunk_id not in seen:
                expanded.append(item)
                seen.add(item.chunk_id)

    for anchor, document_id, version, index in anchors:
        local: list[RetrievalHit] = []
        for neighbor_index in range(max(0, index - radius), index + radius + 1):
            record = by_position.get((document_id, version, neighbor_index))
            if record is None:
                continue
            local.append(originals.get(record.chunk_id) or _record_to_hit(record, anchor.chunk_id))
        append(local)

    # Preserve any hit that lacked the metadata needed for database expansion.
    append(hits)
    return expanded
