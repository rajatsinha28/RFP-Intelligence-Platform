from __future__ import annotations

import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("POSTGRES_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("RERANKER_PROVIDER", "lexical")
os.environ.setdefault("STORAGE_PATH", ".pytest-tmp/storage")
