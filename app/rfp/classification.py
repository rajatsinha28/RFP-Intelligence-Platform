from __future__ import annotations

import re
from pathlib import Path

DOC_TYPES = frozenset({"bid_page", "rfp", "addendum", "specs", "affidavit", "other"})
ADDENDUM_PATTERN = re.compile(
    r"\baddendum(?:\s+(?:no\.?|number))?\s*[-_#:]?\s*(\d+)\b", re.IGNORECASE
)


def classify_document(filename: str) -> tuple[str, int | None]:
    """Classify an RFP file from portable filename signals, never bid-specific values."""

    name = Path(filename).stem.replace("_", " ").lower()
    addendum = ADDENDUM_PATTERN.search(name)
    if addendum:
        return "addendum", int(addendum.group(1))
    if "affidavit" in name:
        return "affidavit", None
    if any(token in name for token in ("specification", "specifications", " specs", "specs ")):
        return "specs", None
    if any(token in name for token in ("bid information", "bid page", "bidnet")):
        return "bid_page", None
    if any(token in name for token in ("rfp", "request for proposal", "final", "solicitation")):
        return "rfp", None
    return "other", None


def normalize_doc_type(value: str | None, filename: str) -> tuple[str, int | None]:
    detected, number = classify_document(filename)
    if value is None:
        return detected, number
    normalized = value.strip().lower()
    if normalized not in DOC_TYPES:
        raise ValueError(f"Unsupported document type: {value}")
    return normalized, number if normalized == "addendum" else None
