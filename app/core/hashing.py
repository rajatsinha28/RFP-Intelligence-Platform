from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_hash(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256_text(serialized)


def normalize_query(query: str) -> str:
    value = unicodedata.normalize("NFKC", query).strip().lower()
    return re.sub(r"\s+", " ", value)
