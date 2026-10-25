from app.ingestion.chunking import Chunker, ChunkingConfig, deduplicate_chunks
from app.ingestion.parsers import extract_pdf_page_label
from app.ingestion.types import ParsedDocument, ParsedElement


def test_structure_chunking_preserves_table_rows_and_metadata() -> None:
    parsed = ParsedDocument(
        "Policy",
        "policy.txt",
        "text",
        "text/plain",
        [
            ParsedElement("Refund Policy", content_type="heading", heading="Refund Policy"),
            ParsedElement("Refunds are available for 30 days. " * 30, section="Refund Policy"),
            ParsedElement(
                "| Item | Limit |\n| --- | --- |\n| Standard | 30 days |",
                content_type="table",
                table_id="table-1",
            ),
        ],
    )
    chunks = Chunker(ChunkingConfig(chunk_size=180, overlap=30, min_size=20, max_size=240)).chunk(
        parsed, "doc-1", 1, {"source_name": "policy.txt"}
    )
    assert len(chunks) > 2
    assert any(
        chunk.metadata.get("table_id") == "table-1" and "30 days" in chunk.text for chunk in chunks
    )
    assert all(chunk.parent_chunk_id for chunk in chunks)


def test_deduplication_uses_content_hash() -> None:
    parsed = ParsedDocument(
        "Duplicate",
        "duplicate.txt",
        "text",
        "text/plain",
        [ParsedElement("same text"), ParsedElement("same text")],
    )
    chunks = Chunker(ChunkingConfig(min_size=1)).chunk(parsed, "doc", 1, {})
    assert len(deduplicate_chunks(chunks)) == 1


def test_structure_chunking_does_not_merge_across_pdf_pages() -> None:
    parsed = ParsedDocument(
        "Pages",
        "pages.pdf",
        "pdf",
        "application/pdf",
        [
            ParsedElement("Short page one text.", page_number=1, section="Page 1"),
            ParsedElement("Short page two text.", page_number=2, section="Page 2"),
        ],
    )
    chunks = Chunker(ChunkingConfig(min_size=100, max_size=240)).chunk(parsed, "doc", 1, {})
    assert len(chunks) == 2
    assert [chunk.metadata["page_number"] for chunk in chunks] == [1, 2]


def test_pdf_page_label_prefers_visible_document_page_and_removes_marker() -> None:
    label, text = extract_pdf_page_label(
        "27\nAddendum 2 extends the proposal deadline to October 31.", 28
    )
    assert label == 27
    assert text == "Addendum 2 extends the proposal deadline to October 31."


def test_pdf_page_label_keeps_physical_page_without_isolated_marker() -> None:
    label, text = extract_pdf_page_label("Section 7\nSubmission Requirements", 42)
    assert label == 42
    assert text == "Section 7\nSubmission Requirements"
