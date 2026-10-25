from __future__ import annotations

import re

SUSPICIOUS_PATTERNS = (
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"reveal\s+(the\s+)?system\s+prompt",
    r"print\s+(your\s+)?api\s*key",
    r"exfiltrat(e|ion)",
    r"developer\s+message",
)


def detect_prompt_injection(text: str) -> list[str]:
    lowered = text.lower()
    return [pattern for pattern in SUSPICIOUS_PATTERNS if re.search(pattern, lowered)]


def sanitize_document_text(text: str) -> str:
    # Documents remain evidence, never instructions. Injection patterns are handled
    # as document content and can be flagged separately for audit logging.
    cleaned = text.replace("\x00", "").strip()
    return cleaned[:2_000_000]
