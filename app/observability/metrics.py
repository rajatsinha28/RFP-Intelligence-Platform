from prometheus_client import Counter, Histogram

QUERY_COUNT = Counter("advance_rag_queries_total", "Queries processed", ["route", "status"])
CACHE_COUNT = Counter("advance_rag_cache_total", "Cache lookups", ["kind", "result"])
INGESTION_COUNT = Counter("advance_rag_ingestions_total", "Document ingestions", ["status"])
STAGE_LATENCY = Histogram(
    "advance_rag_stage_latency_seconds",
    "RAG pipeline stage latency",
    ["stage"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)
