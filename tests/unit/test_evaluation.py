from pathlib import Path

from evaluation.metrics import keyword_coverage, ndcg
from evaluation.run import load_dataset


def test_rfp_retrieval_dataset_covers_both_bid_packages() -> None:
    records = load_dataset(Path("evaluation/datasets/rfp_retrieval.json"))
    assert len(records) >= 15
    assert {record["bid_key"] for record in records} == {"Bid1", "Bid2"}
    answerable = [record for record in records if not record.get("negative_control")]
    negative_controls = [record for record in records if record.get("negative_control")]
    assert all(record["expected_sources"] for record in answerable)
    assert negative_controls and all(not record["expected_sources"] for record in negative_controls)


def test_keyword_coverage_supports_phrases() -> None:
    assert (
        keyword_coverage("Electronic bids must be submitted through eMMA.", ["through eMMA"]) == 1
    )
    assert keyword_coverage("The proposal is sealed.", ["through eMMA"]) == 0


def test_keyword_coverage_normalizes_numbers() -> None:
    assert (
        keyword_coverage("Delivery is required within 45 calendar days", ["45 calendar days"]) == 1
    )


def test_ndcg_does_not_reward_duplicate_citations_above_one() -> None:
    assert ndcg(["book.pdf", "book.pdf"], {"book.pdf"}) == 1.0
