from __future__ import annotations

import math
import re
from dataclasses import dataclass

from app.schemas import RetrievalHit

TOKEN = re.compile(r"[A-Za-z0-9_'-]+")
IDENTIFIER = re.compile(r"(?i)\b(?=[a-z0-9-]*\d)[a-z0-9]+(?:-[a-z0-9]+)+\b|\b\d+\b")
# Question words and grammatical stopwords should not reduce evidence
# alignment.  Keeping this list local avoids making answerability depend on
# an optional NLP package at runtime.
STOPWORDS = {
    "a",
    "about",
    "after",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "do",
    "does",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
}


def _content_tokens(text: str) -> set[str]:
    normalized = text.lower().replace("-", " ")

    def canonical(token: str) -> str:
        if token in {"certification", "certifications", "certified", "certifies"}:
            return "certify"
        for suffix in ("ing", "ed", "es", "s"):
            if token.endswith(suffix) and len(token) > len(suffix) + 3:
                return token[: -len(suffix)]
        return token

    return {canonical(token) for token in TOKEN.findall(normalized) if token not in STOPWORDS}


@dataclass(slots=True)
class Answerability:
    confidence: float
    sufficient: bool
    should_retry: bool
    reasons: list[str]


def assess_answerability(
    query: str,
    hits: list[RetrievalHit],
    high_threshold: float,
    medium_threshold: float,
    similarity_threshold: float,
) -> Answerability:
    if not hits:
        return Answerability(0.0, False, False, ["no_results"])
    query_tokens = _content_tokens(query)
    lexical_scores = []
    for hit in hits[:5]:
        text_tokens = _content_tokens(hit.text)
        lexical_scores.append(len(query_tokens & text_tokens) / max(1, len(query_tokens)))
    lexical = max(lexical_scores, default=0)
    union_tokens = set().union(*(_content_tokens(hit.text) for hit in hits[:5]))
    aggregate_lexical = len(query_tokens & union_tokens) / max(1, len(query_tokens))
    evidence_alignment = max(lexical, aggregate_lexical)
    query_identifiers = {item.lower() for item in IDENTIFIER.findall(query)}
    evidence_identifiers = {
        item.lower() for item in IDENTIFIER.findall(" ".join(hit.text for hit in hits[:5]))
    }
    identifier_coverage = (
        len(query_identifiers & evidence_identifiers) / len(query_identifiers)
        if query_identifiers
        else 0.0
    )

    reranker_values = [hit.reranker_score for hit in hits[:5]]
    top_reranker = max(reranker_values, default=0)
    # Lexical and hosted rerankers already return a probability-like score.
    # Local cross-encoders may emit unbounded logits instead.
    reranker = top_reranker if 0 <= top_reranker <= 1 else 1 / (1 + math.exp(-top_reranker))
    # Three independently retrieved chunks are enough for full result
    # coverage. A factual answer may legitimately come from one document, so
    # require a traceable source without penalizing a single-source corpus.
    coverage = min(1.0, len(hits) / 3)
    source_support = 1.0 if any(hit.document_id for hit in hits[:5]) else 0.0
    fusion = min(1.0, max((hit.fusion_score for hit in hits[:5]), default=0) * 50)
    confidence = round(
        0.32 * reranker
        + 0.29 * evidence_alignment
        + 0.18 * coverage
        + 0.11 * source_support
        + 0.10 * fusion,
        4,
    )
    reasons: list[str] = []
    if evidence_alignment < similarity_threshold:
        reasons.append("weak_query_alignment")
    if len(hits) < 2:
        reasons.append("few_results")
    # A medium-confidence result with clear lexical alignment, a traceable source,
    # and a meaningful reranker score is safe to send to the grounded generator.
    # Post-generation claim validation remains the final guardrail. This avoids
    # deterministic false negatives for multi-clause questions whose terms are
    # spread across adjacent chunks.
    corroborated_medium = (
        confidence >= medium_threshold
        and evidence_alignment >= similarity_threshold
        and (
            reranker >= 0.45
            or (aggregate_lexical >= 0.50 and (reranker >= 0.25 or identifier_coverage >= 0.75))
        )
        and source_support == 1.0
    )
    sufficient = (
        confidence >= high_threshold and evidence_alignment >= similarity_threshold
    ) or corroborated_medium
    should_retry = not sufficient and confidence >= medium_threshold
    return Answerability(confidence, sufficient, should_retry, reasons)
