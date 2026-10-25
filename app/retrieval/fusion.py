from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from app.schemas import RetrievalHit


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[RetrievalHit]], rrf_k: int = 60
) -> list[RetrievalHit]:
    scores: dict[str, float] = defaultdict(float)
    hits: dict[str, RetrievalHit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            hits.setdefault(hit.chunk_id, hit.model_copy(deep=True))
            scores[hit.chunk_id] += 1.0 / (rrf_k + rank)
            if hit.dense_score:
                hits[hit.chunk_id].dense_score = hit.dense_score
            if hit.sparse_score:
                hits[hit.chunk_id].sparse_score = hit.sparse_score
    for chunk_id, score in scores.items():
        hits[chunk_id].fusion_score = score
    return sorted(hits.values(), key=lambda item: item.fusion_score, reverse=True)


def weighted_fusion(
    dense: Sequence[RetrievalHit], sparse: Sequence[RetrievalHit], alpha: float
) -> list[RetrievalHit]:
    def normalize(values: Sequence[RetrievalHit], attribute: str) -> dict[str, float]:
        raw = {item.chunk_id: float(getattr(item, attribute)) for item in values}
        if not raw:
            return {}
        low, high = min(raw.values()), max(raw.values())
        span = high - low
        return {key: (value - low) / span if span else 1.0 for key, value in raw.items()}

    dense_norm = normalize(dense, "dense_score")
    sparse_norm = normalize(sparse, "sparse_score")
    by_id = {item.chunk_id: item.model_copy(deep=True) for item in [*dense, *sparse]}
    for chunk_id, hit in by_id.items():
        hit.fusion_score = alpha * dense_norm.get(chunk_id, 0) + (1 - alpha) * sparse_norm.get(
            chunk_id, 0
        )
    return sorted(by_id.values(), key=lambda item: item.fusion_score, reverse=True)
