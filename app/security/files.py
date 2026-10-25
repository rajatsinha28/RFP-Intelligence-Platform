from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from app.core.exceptions import UnsupportedDocumentError

ALLOWED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".csv": "text/csv",
    ".json": "application/json",
    ".html": "text/html",
    ".htm": "text/html",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}


def safe_filename(filename: str) -> str:
    base = Path(filename).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    if not cleaned:
        raise UnsupportedDocumentError("Invalid filename")
    return cleaned[:240]


def detect_mime(filename: str, data: bytes, declared: str | None = None) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise UnsupportedDocumentError(f"Unsupported file extension: {suffix or 'none'}")

    signatures = {
        b"%PDF-": "application/pdf",
        b"PK\x03\x04": "application/zip",
        b"\x89PNG\r\n\x1a\n": "image/png",
        b"\xff\xd8\xff": "image/jpeg",
        b"II*\x00": "image/tiff",
        b"MM\x00*": "image/tiff",
    }
    sniffed = next(
        (mime for signature, mime in signatures.items() if data.startswith(signature)), None
    )
    expected = ALLOWED_EXTENSIONS[suffix]
    if suffix == ".docx":
        if sniffed != "application/zip":
            raise UnsupportedDocumentError("DOCX upload is not a valid ZIP-based Office document")
        return expected
    if (
        expected in {"application/pdf", "image/png", "image/jpeg", "image/tiff"}
        and sniffed != expected
    ):
        raise UnsupportedDocumentError("File content does not match its binary file type")
    if sniffed and sniffed != expected:
        raise UnsupportedDocumentError("File content does not match its extension")
    guessed = mimetypes.guess_type(filename)[0]
    candidate = sniffed or declared or guessed or expected
    if candidate.split(";", 1)[0] not in {expected, "application/octet-stream"}:
        raise UnsupportedDocumentError("Declared MIME type does not match the file type")
    return expected
