from app.generation.citations import (
    build_citations,
    complete_claim_citations,
    relevant_quote,
)
from app.generation.grounding import validate_grounding
from app.query.answerability import assess_answerability
from app.query.classifier import classify_query
from app.query.graph import CACHEABLE_ANSWER_STATUSES
from app.schemas import AnswerStatus, Claim, QueryRoute, RetrievalHit


def evidence(text: str = "The refund period is 30 calendar days.") -> RetrievalHit:
    return RetrievalHit(
        chunk_id="chunk-1",
        document_id="doc-1",
        text=text,
        source_name="policy.pdf",
        page_number=4,
        section="Refunds",
        dense_score=0.8,
        fusion_score=0.03,
        reranker_score=3.0,
    )


def test_classifier_routes_comparison_to_advanced() -> None:
    result = classify_query("Compare the 2024 and 2025 security policies and explain what changed")
    assert result.route in {QueryRoute.ADVANCED_RAG, QueryRoute.MULTI_HOP_RAG}
    assert result.requires_transform


def test_classifier_keeps_short_question_simple() -> None:
    assert classify_query("What is the refund period?").route == QueryRoute.SIMPLE_RAG


def test_grounding_rejects_unknown_citation() -> None:
    result = validate_grounding(
        [Claim(text="The period is 30 days.", citation_ids=["missing"])], [evidence()]
    )
    assert not result.valid
    assert result.unknown_citations == ["missing"]


def test_grounding_accepts_supported_claim_and_builds_traceable_citation() -> None:
    claim = Claim(text="The refund period is 30 calendar days.", citation_ids=["chunk-1"])
    assert validate_grounding([claim], [evidence()]).valid
    citation = build_citations([claim], [evidence()])[0]
    assert citation.page == 4
    assert citation.chunk_id == "chunk-1"


def test_citation_quote_centers_the_claim_support_in_long_context() -> None:
    prefix = "Unrelated travel details. " * 40
    text = prefix + "The final submission deadline is July 9, 2024 at 2:00 PM CST."
    quote = relevant_quote(text, "The bid is due July 9, 2024 at 2:00 PM CST.", 180)
    assert "July 9, 2024" in quote
    assert len(quote) <= 180


def test_citation_completion_adds_chunk_for_missing_named_entity() -> None:
    ownership = evidence("The County issued solicitation 24-101.").model_copy(
        update={"chunk_id": "ownership"}
    )
    action = evidence("Addendum 2 changed the due date.").model_copy(update={"chunk_id": "action"})
    claim = Claim(
        text="Addendum 2 changed County solicitation 24-101's due date.",
        citation_ids=["action"],
    )
    completed = complete_claim_citations([claim], [action, ownership])
    assert completed[0].citation_ids == ["action", "ownership"]


def test_citation_completion_does_not_add_unrelated_chunk() -> None:
    action = evidence("Addendum 2 changed the due date.").model_copy(update={"chunk_id": "action"})
    unrelated = evidence("The office serves lunch.").model_copy(update={"chunk_id": "unrelated"})
    claim = Claim(
        text="Addendum 2 changed the due date.",
        citation_ids=["action"],
    )
    completed = complete_claim_citations([claim], [action, unrelated])
    assert completed[0].citation_ids == ["action"]


def test_citation_completion_uses_question_entities_missing_from_claim() -> None:
    ownership = evidence("The County issued solicitation 24-101.").model_copy(
        update={"chunk_id": "ownership"}
    )
    action = evidence("Addendum 2 changed the due date.").model_copy(update={"chunk_id": "action"})
    claim = Claim(text="Addendum 2 changed it.", citation_ids=["action"])
    completed = complete_claim_citations(
        [claim],
        [action, ownership],
        required_context="What changed for County solicitation 24-101?",
    )
    assert completed[0].citation_ids == ["action", "ownership"]


def test_answerability_falls_back_without_results() -> None:
    result = assess_answerability("Unknown?", [], 0.72, 0.48, 0.22)
    assert not result.sufficient
    assert not result.should_retry


def test_answerability_ignores_question_stopwords() -> None:
    result = assess_answerability("What is the refund period?", [evidence()], 0.72, 0.48, 0.22)
    assert result.sufficient


def test_answerability_accepts_realistic_single_document_semantic_match() -> None:
    hits = [
        evidence("Customers may request a refund within 30 calendar days of purchase.").model_copy(
            update={
                "chunk_id": f"chunk-{index}",
                "fusion_score": 0.03,
                "reranker_score": 0.60,
            }
        )
        for index in range(3)
    ]
    result = assess_answerability("What is the refund period?", hits, 0.72, 0.48, 0.22)
    assert result.sufficient


def test_answerability_rejects_unrelated_results_even_with_high_scores() -> None:
    hits = [
        evidence("Office locations and opening hours.").model_copy(
            update={
                "chunk_id": f"chunk-{index}",
                "fusion_score": 0.03,
                "reranker_score": 0.9,
            }
        )
        for index in range(3)
    ]
    result = assess_answerability("What is the refund period?", hits, 0.72, 0.48, 0.22)
    assert not result.sufficient
    assert "weak_query_alignment" in result.reasons


def test_answerability_accepts_corroborated_medium_multiclause_evidence() -> None:
    hit = evidence().model_copy(
        update={
            "text": "The initial contract term is three years with two one-year renewals.",
            "reranker_score": 0.48,
            "fusion_score": 0.02,
        }
    )
    result = assess_answerability(
        "What is the initial contract term and maximum duration with renewals?",
        [hit, hit.model_copy(update={"chunk_id": "chunk-2"})],
        0.72,
        0.48,
        0.22,
    )
    assert result.sufficient


def test_answerability_uses_aggregate_coverage_across_top_evidence() -> None:
    first = evidence("Equipment must be supplied new and unused.").model_copy(
        update={"reranker_score": 0.36, "fusion_score": 0.02}
    )
    second = evidence("All equipment must carry an energy-efficiency certification.").model_copy(
        update={"chunk_id": "chunk-2", "reranker_score": 0.36, "fusion_score": 0.02}
    )
    result = assess_answerability(
        "What condition and energy-efficiency certification are required for equipment?",
        [first, second],
        0.72,
        0.48,
        0.22,
    )
    assert result.sufficient


def test_answerability_accepts_explicit_certification_evidence_without_retry() -> None:
    first = evidence("All equipment supplied must be new and unused.").model_copy(
        update={"reranker_score": 0.2917, "fusion_score": 0.03}
    )
    second = evidence("The equipment must be ENERGY STAR certified.").model_copy(
        update={"chunk_id": "chunk-2", "reranker_score": 0.2917, "fusion_score": 0.03}
    )
    result = assess_answerability(
        "What condition and energy-efficiency certification are required "
        "for the supplied equipment?",
        [first, second],
        0.72,
        0.48,
        0.22,
    )
    assert result.sufficient
    assert not result.should_retry


def test_answerability_aggregate_coverage_does_not_accept_unrelated_evidence() -> None:
    hits = [
        evidence("The office opens on weekdays."),
        evidence("Invoices are sent electronically.").model_copy(update={"chunk_id": "chunk-2"}),
    ]
    result = assess_answerability("Which planet has rings?", hits, 0.72, 0.48, 0.22)
    assert not result.sufficient


def test_only_validated_answers_are_response_cacheable() -> None:
    assert AnswerStatus.ANSWERED in CACHEABLE_ANSWER_STATUSES
    assert AnswerStatus.PARTIALLY_ANSWERED in CACHEABLE_ANSWER_STATUSES
    assert AnswerStatus.NOT_FOUND not in CACHEABLE_ANSWER_STATUSES
    assert AnswerStatus.INSUFFICIENT_EVIDENCE not in CACHEABLE_ANSWER_STATUSES
