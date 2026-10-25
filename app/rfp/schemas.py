from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

FIELD_DEFINITIONS: dict[str, str] = {
    "Bid Number": "Official solicitation or RFP identifier.",
    "Title": "Full title of the bid or RFP.",
    "Due Date": "Final submission deadline including time and time zone after addenda.",
    "Bid Submission Type": "Required electronic, sealed paper, email, or other method.",
    "Term of Bid": "Contract duration and renewal options.",
    "Pre Bid Meeting": "Date, time, location, mandatory status, or explicit None.",
    "Installation": (
        "All required installation, deployment, setup, imaging, asset tagging, etching, and "
        "asset-reporting services, including explicit exceptions."
    ),
    "Bid Bond Requirement": "Required security and amount/percentage or Not required.",
    "Delivery Date": "Required date or delivery window after award.",
    "Payment Terms": (
        "Payment period and all invoicing conditions. Invoicing instructions are supported "
        "payment terms even when no net-payment deadline is stated."
    ),
    "Any Additional Documentation Required": (
        "Affidavits, forms, certificates, insurance, W-9, and similar documents."
    ),
    "MFG for Registration": (
        "Manufacturer registration, authorized-reseller status, letter of authorization, or "
        "factory-authorized certification requirements."
    ),
    "Contract or Cooperative to use": "Referenced cooperative or state contract vehicle.",
    "Model_no": "Requested product model number or numbers.",
    "Part_no": "Requested part or SKU number or numbers.",
    "Product": "Product names and quantities.",
    "contact_info": "Procurement contact name, email, and phone.",
    "company_name": "Issuing organization or agency.",
    "Bid Summary": "Concise three-to-six-sentence summary grounded in validated fields.",
    "Product Specification": (
        "Key CPU, RAM, storage, display, warranty, and technical requirements."
    ),
}

DATES_LOGISTICS_FIELDS = (
    "Bid Number",
    "Title",
    "Due Date",
    "Bid Submission Type",
    "Term of Bid",
    "Pre Bid Meeting",
    "Installation",
    "Delivery Date",
)
COMMERCIAL_COMPLIANCE_FIELDS = (
    "Bid Bond Requirement",
    "Payment Terms",
    "Any Additional Documentation Required",
    "MFG for Registration",
    "Contract or Cooperative to use",
    "contact_info",
    "company_name",
)
PRODUCT_SPEC_FIELDS = ("Model_no", "Part_no", "Product", "Product Specification")
REQUIRED_FIELDS = tuple(FIELD_DEFINITIONS)


class FieldStatus(StrEnum):
    SUPPORTED = "supported"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    INVALID = "invalid"


class SourceRef(BaseModel):
    document_id: str
    file: str
    page: int | None = None
    chunk_id: str
    quote: str
    doc_type: str | None = None
    addendum_number: int | None = None


class FieldResult(BaseModel):
    value: Any = None
    sources: list[SourceRef] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    notes: str = ""
    status: FieldStatus = FieldStatus.NOT_FOUND


class AddendumChange(BaseModel):
    field: str
    previous_value: Any = None
    revised_value: Any = None
    source: SourceRef
    rationale: str


class ValidationIssue(BaseModel):
    field: str
    code: str
    message: str


class ValidationSummary(BaseModel):
    passed: int = 0
    failed: int = 0
    not_found: int = 0
    issues: list[ValidationIssue] = Field(default_factory=list)


class RunMetadata(BaseModel):
    run_id: str
    knowledge_base_id: str
    model: str | None = None
    started_at: str
    completed_at: str
    token_usage: dict[str, Any] = Field(default_factory=dict)
    latency_ms: dict[str, float] = Field(default_factory=dict)


class BidRecord(BaseModel):
    bid_id: str
    fields: dict[str, FieldResult]
    addendum_changes: list[AddendumChange] = Field(default_factory=list)
    validation: ValidationSummary
    run_metadata: RunMetadata


class ExtractionRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    bid_id: UUID
    mode: str
    status: str
    output_json: dict[str, Any]
    validation_json: dict[str, Any]
    trace_json: list[dict[str, Any]]
    token_usage: dict[str, Any]
    latency_ms: dict[str, Any]
    artifact_path: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class ExtractionStartResponse(BaseModel):
    run_id: UUID
    bid_key: str
    status: str
