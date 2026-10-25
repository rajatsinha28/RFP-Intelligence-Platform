from __future__ import annotations

import re

from app.schemas import RetrievalHit

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
TOKEN = re.compile(r"[A-Za-z0-9_'-]+")
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "at",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "of",
    "on",
    "the",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
}


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in TOKEN.findall(text) if token.lower() not in STOPWORDS}


def _compressed_text(text: str, query_tokens: set[str], budget: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= budget:
        return normalized

    sentences = [item.strip() for item in SENTENCE_SPLIT.split(normalized) if item.strip()]
    if not sentences:
        return normalized[:budget].rsplit(" ", 1)[0]

    ranked = sorted(
        range(len(sentences)),
        key=lambda index: len(query_tokens & _tokens(sentences[index])),
        reverse=True,
    )
    selected: set[int] = set()
    used = 0
    for anchor in ranked:
        # Keep one sentence on either side so names, pronouns, and actions stay
        # connected. Render selected sentences in source order below.
        window = range(max(0, anchor - 1), min(len(sentences), anchor + 2))
        additions = [index for index in window if index not in selected]
        extra = sum(len(sentences[index]) + 1 for index in additions)
        if additions and used + extra <= budget:
            selected.update(additions)
            used += extra
        if used >= budget:
            break
    if not selected:
        return normalized[:budget].rsplit(" ", 1)[0]
    return " ".join(sentences[index] for index in sorted(selected))


def compress_context(
    query: str, hits: list[RetrievalHit], max_characters: int
) -> list[RetrievalHit]:
    query_tokens = _tokens(query)
    seen: set[str] = set()
    result: list[RetrievalHit] = []
    used = 0
    for hit in hits:
        remaining = max_characters - used
        if remaining <= 0:
            break
        selected = _compressed_text(hit.text, query_tokens, remaining)
        normalized_key = selected.lower()
        if selected and normalized_key not in seen:
            item = hit.model_copy(deep=True)
            item.text = selected
            result.append(item)
            seen.add(normalized_key)
            used += len(selected)
    return result
