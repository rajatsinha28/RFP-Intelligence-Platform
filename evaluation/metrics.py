from __future__ import annotations

import math
import re
from statistics import mean
from typing import Any

TOKEN = re.compile(r"[A-Za-z0-9_'-]+")


def normalize_for_match(text: str) -> str:
    normalized = " ".join(text.lower().replace("-", " ").split())
    # Preserve semantic equivalence between common printed and spoken platform
    # notation without making evaluation depend on a particular answer style.
    normalized = re.sub(r"\bnine and three quarters\b", "9¾", normalized)
    return normalized


def keyword_coverage(text: str, keywords: list[str]) -> float:
    if not keywords:
        return 1.0
    normalized = normalize_for_match(text)
    matched = sum(normalize_for_match(keyword) in normalized for keyword in keywords)
    return matched / len(keywords)


def token_f1(actual: str, expected: str) -> float:
    actual_tokens = [item.lower() for item in TOKEN.findall(actual)]
    expected_tokens = [item.lower() for item in TOKEN.findall(expected)]
    if not actual_tokens or not expected_tokens:
        return float(actual_tokens == expected_tokens)
    common = set(actual_tokens) & set(expected_tokens)
    precision = len(common) / len(set(actual_tokens))
    recall = len(common) / len(set(expected_tokens))
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def reciprocal_rank(retrieved: list[str], expected: set[str]) -> float:
    for rank, source in enumerate(retrieved, start=1):
        if source in expected:
            return 1.0 / rank
    return 0.0


def ndcg(retrieved: list[str], expected: set[str]) -> float:
    seen_relevant: set[str] = set()
    dcg = 0.0
    for rank, source in enumerate(retrieved, 1):
        if source in expected and source not in seen_relevant:
            dcg += 1.0 / math.log2(rank + 1)
            seen_relevant.add(source)
    ideal_hits = min(len(expected), len(retrieved))
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / ideal if ideal else 0.0


def aggregate_native(results: list[dict[str, Any]]) -> dict[str, float]:
    if not results:
        return {}
    answerable = [item for item in results if not item.get("negative_control")]
    return {
        "answer_token_f1": mean(item["answer_token_f1"] for item in results),
        "answer_keyword_coverage": mean(item["answer_keyword_coverage"] for item in results),
        "evidence_keyword_coverage": mean(item["evidence_keyword_coverage"] for item in results),
        "hit_rate": mean(item["hit_rate"] for item in results),
        "status_pass_rate": mean(item["status_pass"] for item in results),
        "citation_pass_rate": mean(item["citation_pass"] for item in results),
        "page_pass_rate": mean(item["page_pass"] for item in results),
        "mrr": mean(item["mrr"] for item in results),
        "ndcg": mean(item["ndcg"] for item in results),
        "fallback_rate": mean(item["fallback"] for item in results),
        "first_pass_cache_hit_rate": mean(item["cache_hit"] for item in results),
        "cache_hit_rate": mean(item["second_pass_cache_hit"] for item in answerable)
        if answerable
        else 0.0,
        "mean_latency_ms": mean(item["latency_ms"] for item in results),
        "p50_latency_ms": percentile([item["latency_ms"] for item in results], 50),
        "p95_latency_ms": percentile([item["latency_ms"] for item in results], 95),
        "mean_tokens": mean(item["total_tokens"] for item in results),
        "cache_consistency_rate": mean(item["cache_consistency_pass"] for item in results),
    }


def percentile(values: list[float], percentile_value: int) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * percentile_value / 100
    lower, upper = math.floor(index), math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - index) + ordered[upper] * (index - lower)
