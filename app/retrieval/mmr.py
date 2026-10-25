from __future__ import annotations

from app.cache.service import cosine_similarity
from app.schemas import RetrievalHit


def maximal_marginal_relevance(
    hits: list[RetrievalHit], query_vector: list[float], limit: int, lambda_mult: float
) -> list[RetrievalHit]:
    if not hits or not any(hit.vector for hit in hits):
        return hits[:limit]
    remaining = [hit for hit in hits if hit.vector]
    selected: list[RetrievalHit] = []
    while remaining and len(selected) < limit:
        best: RetrievalHit | None = None
        best_score = float("-inf")
        for candidate in remaining:
            relevance = cosine_similarity(query_vector, candidate.vector or [])
            redundancy = max(
                (cosine_similarity(candidate.vector or [], item.vector or []) for item in selected),
                default=0.0,
            )
            score = lambda_mult * relevance - (1 - lambda_mult) * redundancy
            if score > best_score:
                best, best_score = candidate, score
        if best is None:
            break
        selected.append(best)
        remaining.remove(best)
    return selected
