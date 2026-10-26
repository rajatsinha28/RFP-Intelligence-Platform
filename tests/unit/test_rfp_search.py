from app.rfp.search import rfp_query_variants


def test_quantity_question_expands_contract_distribution_language() -> None:
    variants = rfp_query_variants(
        "Are anticipated equipment quantities a one-time purchase "
        "or distributed across the contract?"
    )
    combined = " ".join(variants).lower()
    assert "one-time buys" in combined
    assert "spread over life of the contract" in combined
