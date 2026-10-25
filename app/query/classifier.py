from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas import QueryRoute

COMPLEX_MARKERS = re.compile(
    r"\b(compare|contrast|across|difference|differences|changed|why|trade-?offs?|"
    r"relationship|synthesize|evaluate|implications|multi[- ]?step)\b",
    re.IGNORECASE,
)
DIRECT_MARKERS = re.compile(
    r"\b(page|section|document|file|policy|invoice|contract)\b.*\b(number|title|name|id)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Classification:
    route: QueryRoute
    complexity: float
    requires_transform: bool


def classify_query(query: str) -> Classification:
    words = query.split()
    clauses = len(re.findall(r"[,;:]|\band\b|\bversus\b|\bvs\.?\b", query, re.IGNORECASE))
    complex_score = min(
        1.0,
        (0.45 if COMPLEX_MARKERS.search(query) else 0)
        + min(0.25, max(0, len(words) - 16) / 80)
        + min(0.3, clauses * 0.1),
    )
    if DIRECT_MARKERS.search(query) and len(words) <= 18:
        return Classification(QueryRoute.DIRECT_DOCUMENT_LOOKUP, complex_score, False)
    if complex_score >= 0.65 or clauses >= 3:
        return Classification(QueryRoute.MULTI_HOP_RAG, complex_score, True)
    if complex_score >= 0.35:
        return Classification(QueryRoute.ADVANCED_RAG, complex_score, True)
    return Classification(QueryRoute.SIMPLE_RAG, complex_score, False)
