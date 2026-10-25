from app.ingestion.parsers import clean_extracted_text, remove_repeated_page_edges
from app.rfp.classification import classify_document
from app.rfp.extraction import _enforce_field_ownership, _validate_fields
from app.rfp.schemas import FIELD_DEFINITIONS, FieldResult, FieldStatus, SourceRef


def test_exactly_twenty_assignment_fields_are_defined() -> None:
    assert len(FIELD_DEFINITIONS) == 20
    assert {"Bid Number", "Due Date", "Product Specification"} <= set(FIELD_DEFINITIONS)


def test_document_classification_is_generic_and_detects_addendum_number() -> None:
    assert classify_document("Addendum No. 3 - revised dates.pdf") == ("addendum", 3)
    assert classify_document("technical-specifications.pdf")[0] == "specs"
    assert classify_document("Mercury Affidavit.pdf")[0] == "affidavit"


def test_pdf_cleanup_repairs_hyphenation_and_repeated_edges() -> None:
    pages = [
        "County Procurement\nThis is a specifi-\ncation line.\nPage 1",
        "County Procurement\nAnother requirement.\nPage 2",
        "County Procurement\nFinal requirement.\nPage 3",
    ]
    cleaned = remove_repeated_page_edges(pages)
    assert all("County Procurement" not in page for page in cleaned)
    assert "specification" in clean_extracted_text(cleaned[0])


def test_validator_rejects_non_null_field_without_traceable_citation() -> None:
    fields = {name: FieldResult(notes="Not found") for name in FIELD_DEFINITIONS}
    fields["Bid Number"] = FieldResult(
        value="RFP-100", confidence=0.9, status=FieldStatus.SUPPORTED
    )
    validation = _validate_fields(fields)
    assert any(issue.code == "missing_citation" for issue in validation.issues)


def test_validator_accepts_supported_value_with_citation() -> None:
    fields = {name: FieldResult(notes="Not found") for name in FIELD_DEFINITIONS}
    fields["Bid Number"] = FieldResult(
        value="RFP-100",
        confidence=0.9,
        status=FieldStatus.SUPPORTED,
        sources=[
            SourceRef(
                document_id="doc-1",
                file="rfp.pdf",
                page=1,
                chunk_id="chunk-1",
                quote="Solicitation number RFP-100",
            )
        ],
    )
    validation = _validate_fields(fields)
    assert validation.passed == 1
    assert validation.failed == 0


def test_schedule_field_ownership_prevents_product_due_date_confusion() -> None:
    delivery_source = SourceRef(
        document_id="doc-1",
        file="rfp.pdf",
        page=2,
        chunk_id="delivery",
        quote="Delivery within 45 days of Award.",
    )
    fields = {
        "Delivery Date": FieldResult(
            value=(
                "Delivery within 45 days of award; the product table also lists 06/10/2024 "
                "as a due date."
            ),
            sources=[delivery_source],
            status=FieldStatus.CONFLICT,
        ),
        "Product Specification": FieldResult(
            value=[
                "30 Dell Latitude 5550 laptops.",
                "Both product lines have a required delivery date of 06/10/2024.",
                "30 Dell docks; due June 10, 2024.",
                "30 Dell laptops; due 06/10/2024.",
            ],
            sources=[delivery_source],
            status=FieldStatus.SUPPORTED,
        ),
    }

    _enforce_field_ownership(fields)

    assert fields["Delivery Date"].value == "Delivery within 45 days of award"
    assert fields["Delivery Date"].status == FieldStatus.SUPPORTED
    assert fields["Product Specification"].value == [
        "30 Dell Latitude 5550 laptops.",
        "30 Dell docks.",
        "30 Dell laptops.",
    ]
