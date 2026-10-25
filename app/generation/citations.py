from __future__ import annotations

import re

from app.schemas import Citation, Claim, RetrievalHit

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
TOKEN = re.compile(r"[A-Za-z0-9_'-]+")
ENTITY_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "from",
    "how",
    "in",
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
}


def _tokens(text: str) -> set[str]:
    return {token.lower().removesuffix("'s") for token in TOKEN.findall(text) if len(token) > 2}


def _entity_tokens(text: str) -> set[str]:
    return {
        token.lower().removesuffix("'s")
        for token in TOKEN.findall(text)
        if (token[0].isupper() or any(character.isdigit() for character in token))
        and token.lower() not in ENTITY_STOPWORDS
    }


def complete_claim_citations(
    claims: list[Claim],
    hits: list[RetrievalHit],
    required_context: str = "",
    max_extra_per_claim: int = 2,
) -> list[Claim]:
    """Add retrieved evidence only when a named claim entity is otherwise uncited."""

    by_id = {hit.chunk_id: hit for hit in hits}
    completed: list[Claim] = []
    for claim in claims:
        item = claim.model_copy(deep=True)
        valid_ids = [chunk_id for chunk_id in item.citation_ids if chunk_id in by_id]
        item.citation_ids = list(dict.fromkeys(valid_ids))
        supporting_text = f"{item.text} {required_context}"
        claim_entities = _entity_tokens(supporting_text)
        claim_tokens = _tokens(supporting_text)
        evidence_text = " ".join(by_id[chunk_id].text for chunk_id in item.citation_ids)
        missing = claim_entities - _tokens(evidence_text)
        for _ in range(max_extra_per_claim):
            if not missing:
                break
            candidates = [hit for hit in hits if hit.chunk_id not in item.citation_ids]
            if not candidates:
                break
            best = max(
                candidates,
                key=lambda hit: (
                    len(missing & _tokens(hit.text)),
                    len(claim_tokens & _tokens(hit.text)),
                    hit.reranker_score,
                ),
            )
            best_tokens = _tokens(best.text)
            # Require the candidate to supply a missing entity and at least one
            # additional claim term. This prevents decorative or broad citations.
            if not (missing & best_tokens) or len(claim_tokens & best_tokens) < 2:
                break
            item.citation_ids.append(best.chunk_id)
            missing -= best_tokens
        completed.append(item)
    return completed


def relevant_quote(text: str, claim_text: str, max_characters: int = 600) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_characters:
        return normalized
    sentences = [item.strip() for item in SENTENCE_SPLIT.split(normalized) if item.strip()]
    if not sentences:
        return normalized[:max_characters].rsplit(" ", 1)[0]
    claim_tokens = _tokens(claim_text)
    best = max(
        range(len(sentences)),
        key=lambda index: len(claim_tokens & _tokens(sentences[index])),
    )
    selected = [best]
    for distance in range(1, len(sentences)):
        for candidate in (best - distance, best + distance):
            if candidate < 0 or candidate >= len(sentences):
                continue
            proposed = sorted([*selected, candidate])
            if len(" ".join(sentences[index] for index in proposed)) <= max_characters:
                selected.append(candidate)
        if len(" ".join(sentences[index] for index in selected)) >= max_characters * 0.8:
            break
    return " ".join(sentences[index] for index in sorted(selected))


def build_citations(claims: list[Claim], hits: list[RetrievalHit]) -> list[Citation]:
    by_id = {hit.chunk_id: hit for hit in hits}
    ordered_ids: list[str] = []
    claim_text_by_id: dict[str, list[str]] = {}
    for claim in claims:
        for chunk_id in claim.citation_ids:
            if chunk_id not in ordered_ids:
                ordered_ids.append(chunk_id)
            claim_text_by_id.setdefault(chunk_id, []).append(claim.text)
    citations: list[Citation] = []
    for chunk_id in ordered_ids:
        hit = by_id.get(chunk_id)
        if not hit:
            continue
        citations.append(
            Citation(
                document_id=hit.document_id,
                document=hit.source_name,
                chunk_id=hit.chunk_id,
                page=hit.page_number,
                section=hit.section,
                source_url=hit.source_url,
                quote=relevant_quote(
                    hit.text,
                    " ".join(claim_text_by_id.get(chunk_id, [])),
                ),
            )
        )
    return citations
