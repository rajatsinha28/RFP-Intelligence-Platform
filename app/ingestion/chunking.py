from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from app.core.hashing import sha256_text
from app.ingestion.types import Chunk, ParsedDocument, ParsedElement

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_'-]+")


@dataclass(slots=True)
class ChunkingConfig:
    strategy: str = "structure"
    chunk_size: int = 900
    overlap: int = 120
    min_size: int = 120
    max_size: int = 1600


def _window_text(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text.strip()] if text.strip() else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n", start, end), text.rfind(". ", start, end))
            if boundary > start + size // 2:
                end = boundary + 1
        part = text[start:end].strip()
        if part:
            chunks.append(part)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
        # PDF extraction and OCR frequently insert hard line breaks. Never let
        # overlap begin in the middle of a word, which degrades sparse search
        # and produces broken citation excerpts.
        while start < end and not text[start - 1].isspace() and not text[start].isspace():
            start += 1
        while start < end and text[start].isspace():
            start += 1
    return chunks


def _token_window(text: str, size: int, overlap: int) -> list[str]:
    words = text.split()
    token_size = max(20, size // 4)
    token_overlap = min(token_size - 1, max(0, overlap // 4))
    return [
        " ".join(words[index : index + token_size])
        for index in range(0, len(words), max(1, token_size - token_overlap))
        if words[index : index + token_size]
    ]


def _sentence_chunks(text: str, size: int) -> list[str]:
    sentences = SENTENCE_SPLIT.split(text.strip())
    chunks: list[str] = []
    buffer: list[str] = []
    for sentence in sentences:
        candidate = " ".join([*buffer, sentence])
        if buffer and len(candidate) > size:
            chunks.append(" ".join(buffer))
            buffer = [sentence]
        else:
            buffer.append(sentence)
    if buffer:
        chunks.append(" ".join(buffer))
    return chunks


def _cosine(left: str, right: str) -> float:
    a = Counter(token.lower() for token in TOKEN_PATTERN.findall(left))
    b = Counter(token.lower() for token in TOKEN_PATTERN.findall(right))
    if not a or not b:
        return 0.0
    numerator = sum(a[token] * b[token] for token in a.keys() & b.keys())
    denominator = math.sqrt(sum(value * value for value in a.values())) * math.sqrt(
        sum(value * value for value in b.values())
    )
    return numerator / denominator if denominator else 0.0


def _semantic_chunks(text: str, size: int) -> list[str]:
    sentences = SENTENCE_SPLIT.split(text.strip())
    if len(sentences) < 2:
        return _window_text(text, size, 0)
    chunks: list[str] = []
    buffer = [sentences[0]]
    for sentence in sentences[1:]:
        candidate = " ".join([*buffer, sentence])
        relatedness = _cosine(buffer[-1], sentence)
        if len(candidate) > size or (len(" ".join(buffer)) >= size // 3 and relatedness < 0.08):
            chunks.append(" ".join(buffer))
            buffer = [sentence]
        else:
            buffer.append(sentence)
    if buffer:
        chunks.append(" ".join(buffer))
    return chunks


class Chunker:
    def __init__(self, config: ChunkingConfig) -> None:
        if config.overlap >= config.chunk_size:
            raise ValueError("chunk overlap must be smaller than chunk size")
        self.config = config

    def _split(self, element: ParsedElement) -> list[str]:
        if element.content_type == "table":
            # Keep table headers with every row group and never split through a row.
            lines = element.text.splitlines()
            if len(element.text) <= self.config.max_size or len(lines) <= 3:
                return [element.text]
            header = lines[:2]
            rows = lines[2:]
            groups: list[str] = []
            current = header.copy()
            for row in rows:
                if len("\n".join([*current, row])) > self.config.max_size and len(current) > 2:
                    groups.append("\n".join(current))
                    current = [*header, row]
                else:
                    current.append(row)
            if len(current) > 2:
                groups.append("\n".join(current))
            return groups
        strategy = self.config.strategy
        if strategy == "token":
            return _token_window(element.text, self.config.chunk_size, self.config.overlap)
        if strategy == "sentence":
            return _sentence_chunks(element.text, self.config.chunk_size)
        if strategy == "semantic":
            return _semantic_chunks(element.text, self.config.chunk_size)
        return _window_text(element.text, self.config.chunk_size, self.config.overlap)

    def chunk(
        self,
        parsed: ParsedDocument,
        document_id: str,
        document_version: int,
        base_metadata: dict[str, object],
    ) -> list[Chunk]:
        raw: list[tuple[str, ParsedElement, str]] = []
        for element_index, element in enumerate(parsed.elements):
            parent_seed = f"{document_id}:{document_version}:parent:{element_index}"
            parent_id = sha256_text(parent_seed)[:32]
            for text in self._split(element):
                text = text.strip()
                if not text:
                    continue
                raw.append((text, element, parent_id))

        merged: list[tuple[str, ParsedElement, str]] = []
        for item in raw:
            if (
                merged
                and len(item[0]) < self.config.min_size
                and merged[-1][1].content_type != "table"
                and item[1].content_type != "table"
                and merged[-1][1].page_number == item[1].page_number
                and len(merged[-1][0]) + len(item[0]) + 1 <= self.config.max_size
            ):
                previous = merged.pop()
                merged.append((f"{previous[0]}\n{item[0]}", previous[1], previous[2]))
            else:
                merged.append(item)

        chunks: list[Chunk] = []
        for index, (text, element, parent_id) in enumerate(merged):
            digest = sha256_text(text)
            chunk_id = f"{document_id}-v{document_version}-{index}-{digest[:12]}"
            metadata = {
                **base_metadata,
                "chunk_id": chunk_id,
                "chunk_index": index,
                "parent_chunk_id": parent_id,
                "page_number": element.page_number,
                "section": element.section,
                "subsection": element.subsection,
                "heading": element.heading,
                "content_type": element.content_type,
                "table_id": element.table_id,
                "image_id": element.image_id,
                **element.metadata,
            }
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    document_version=document_version,
                    chunk_index=index,
                    text=text,
                    content_hash=digest,
                    parent_chunk_id=parent_id,
                    metadata={key: value for key, value in metadata.items() if value is not None},
                )
            )
        return chunks


def deduplicate_chunks(chunks: Iterable[Chunk]) -> list[Chunk]:
    seen: set[str] = set()
    result: list[Chunk] = []
    for chunk in chunks:
        if chunk.content_hash in seen:
            continue
        seen.add(chunk.content_hash)
        result.append(chunk)
    return result
