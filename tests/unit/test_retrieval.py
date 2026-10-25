from app.retrieval.fusion import reciprocal_rank_fusion, weighted_fusion
from app.retrieval.mmr import maximal_marginal_relevance
from app.retrieval.reranking import lexical_rerank_score
from app.retrieval.service import preserve_source_coverage
from app.schemas import RetrievalHit


def hit(
    identifier: str, dense: float = 0, sparse: float = 0, vector: list[float] | None = None
) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=identifier,
        document_id="doc",
        text=f"Text {identifier}",
        source_name="source.txt",
        dense_score=dense,
        sparse_score=sparse,
        vector=vector,
    )


def test_rrf_rewards_results_present_in_both_rankings() -> None:
    fused = reciprocal_rank_fusion([[hit("a"), hit("b")], [hit("b"), hit("c")]])
    assert fused[0].chunk_id == "b"


def test_weighted_fusion_honors_dense_weight() -> None:
    fused = weighted_fusion(
        [hit("dense", dense=1), hit("both", dense=0.8)],
        [hit("sparse", sparse=1), hit("both", sparse=0.8)],
        0.8,
    )
    assert fused[0].chunk_id in {"dense", "both"}


def test_mmr_reduces_duplicate_vectors() -> None:
    selected = maximal_marginal_relevance(
        [hit("a", vector=[1, 0]), hit("b", vector=[0.99, 0.01]), hit("c", vector=[0, 1])],
        [1, 0],
        2,
        0.3,
    )
    assert selected[0].chunk_id == "a"
    assert selected[1].chunk_id == "c"


def test_lexical_reranker_prioritizes_named_entity_action_evidence() -> None:
    distractor = RetrievalHit(
        chunk_id="distractor",
        document_id="doc",
        text="The agency accepts several cooperative purchasing vehicles.",
        source_name="bid.pdf",
        fusion_score=0.03,
    )
    answer = RetrievalHit(
        chunk_id="answer",
        document_id="doc",
        text=(
            "The successful bidder must deliver all laptop computers within "
            "45 calendar days after receipt of the purchase order."
        ),
        source_name="bid.pdf",
        page_number=12,
        fusion_score=0.02,
    )
    query = "When must the successful bidder deliver the laptop computers?"
    assert lexical_rerank_score(query, answer) > lexical_rerank_score(query, distractor)


def test_reranking_preserves_source_coverage_from_hybrid_top_k() -> None:
    baseline = [
        hit("a1").model_copy(update={"source_name": "a.pdf"}),
        hit("b1").model_copy(update={"source_name": "b.pdf"}),
        hit("a2").model_copy(update={"source_name": "a.pdf"}),
    ]
    reranked = [
        hit("a2").model_copy(update={"source_name": "a.pdf"}),
        hit("a1").model_copy(update={"source_name": "a.pdf"}),
        hit("a3").model_copy(update={"source_name": "a.pdf"}),
    ]
    result = preserve_source_coverage(baseline, reranked, 3)
    assert {item.source_name for item in result} == {"a.pdf", "b.pdf"}
