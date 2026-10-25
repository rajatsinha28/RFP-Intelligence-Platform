from app.retrieval.compression import compress_context
from app.schemas import RetrievalHit


def test_compression_preserves_source_order_and_pronoun_context() -> None:
    hit = RetrievalHit(
        chunk_id="answer",
        document_id="doc",
        source_name="addendum.pdf",
        text=(
            "The procurement office issued Addendum 2. "
            "It revised the schedule. The proposal deadline moved to October 31."
        ),
    )
    result = compress_context("What proposal deadline did Addendum 2 establish?", [hit], 500)
    assert len(result) == 1
    assert result[0].text.startswith("The procurement office")
    assert result[0].text.index("It revised") < result[0].text.index("The proposal deadline")


def test_compression_keeps_neighbor_hits_traceable() -> None:
    previous = RetrievalHit(
        chunk_id="previous",
        document_id="doc",
        source_name="rfp.pdf",
        text="The original proposal deadline was October 24.",
    )
    answer = RetrievalHit(
        chunk_id="answer",
        document_id="doc",
        source_name="addendum.pdf",
        text="Addendum 2 changed the proposal deadline to October 31.",
    )
    result = compress_context("What is the revised proposal deadline?", [previous, answer], 500)
    assert [item.chunk_id for item in result] == ["previous", "answer"]
