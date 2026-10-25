from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ParsedElement:
    text: str
    content_type: str = "text"
    page_number: int | None = None
    section: str | None = None
    subsection: str | None = None
    heading: str | None = None
    table_id: str | None = None
    image_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedDocument:
    title: str
    source_name: str
    source_type: str
    mime_type: str
    elements: list[ParsedElement]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Chunk:
    chunk_id: str
    document_id: str
    document_version: int
    chunk_index: int
    text: str
    content_hash: str
    parent_chunk_id: str | None
    metadata: dict[str, Any]
