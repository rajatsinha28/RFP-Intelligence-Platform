from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from app.core.hashing import normalize_query, stable_hash
from app.db import get_session_factory
from app.models import Bid, Document, QueryLog
from app.query.graph import FALLBACK_TEXT
from app.services import get_services


def question_hash(question: str) -> str:
    """Return the same hash persisted by the public query API."""
    return hashlib.sha256(question.encode("utf-8")).hexdigest()


def load_samples(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    samples = payload.get("samples", payload) if isinstance(payload, dict) else payload
    if not isinstance(samples, list):
        raise ValueError("Audit dataset must be a list or contain a samples list")
    required = {
        "id",
        "bid_key",
        "question",
        "expected_answer",
        "expected_citations",
        "expected_statuses",
        "negative_control",
    }
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise ValueError(f"Audit sample {index} must be an object")
        missing = required - sample.keys()
        if missing:
            raise ValueError(f"Audit sample {index} is missing {sorted(missing)}")
    return samples


def validate_dataset_novelty(dataset_path: Path, dataset_dir: Path) -> dict[str, Any]:
    """Reject duplicates within this dataset and exact questions in sibling datasets."""
    samples = load_samples(dataset_path)
    normalized = [normalize_query(str(sample["question"])) for sample in samples]
    duplicates = sorted({item for item in normalized if normalized.count(item) > 1})
    if duplicates:
        raise ValueError(f"Duplicate questions in audit dataset: {duplicates}")

    sibling_matches: list[dict[str, str]] = []
    audit_questions = set(normalized)
    for candidate in sorted(dataset_dir.glob("*.json")):
        if candidate.resolve() == dataset_path.resolve():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        records = payload.get("samples", payload) if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("question"), str):
                continue
            normalized_question = normalize_query(record["question"])
            if normalized_question in audit_questions:
                sibling_matches.append({"dataset": candidate.name, "question": record["question"]})
    if sibling_matches:
        raise ValueError(f"Questions already present in other datasets: {sibling_matches}")
    return {
        "sample_count": len(samples),
        "unique_normalized_questions": len(set(normalized)),
        "unique_sha256_hashes": len({question_hash(str(item["question"])) for item in samples}),
        "sibling_dataset_matches": 0,
    }


def _source_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _citation_key(citation: dict[str, Any]) -> tuple[str, int | None]:
    return (_source_key(str(citation.get("document", ""))), citation.get("page"))


def _canonical_citations(response: dict[str, Any]) -> list[tuple[str, int | None, str]]:
    return sorted(
        (
            _source_key(str(item.get("document", ""))),
            item.get("page"),
            str(item.get("chunk_id", "")),
        )
        for item in response.get("citations", [])
    )


def _keyword_coverage(text: str, keywords: list[str]) -> float:
    if not keywords:
        return 1.0
    normalized = normalize_query(text)
    return sum(normalize_query(keyword) in normalized for keyword in keywords) / len(keywords)


def _expected_sources_present(
    expected: list[dict[str, Any]], citations: list[dict[str, Any]]
) -> bool:
    actual = {_citation_key(citation) for citation in citations}
    return all((_source_key(str(item["source"])), item.get("page")) in actual for item in expected)


def classify_failure(
    sample: dict[str, Any], response: dict[str, Any], transport_error: str | None = None
) -> str | None:
    if transport_error:
        return "transport"
    if sample["negative_control"]:
        if response.get("status") not in sample["expected_statuses"] or response.get("citations"):
            return "negative_control"
        return None
    expected_sources = {
        (_source_key(str(item["source"])), item.get("page"))
        for item in sample["expected_citations"]
    }
    top_hits = response.get("retrieval_metadata", {}).get("top_hits", [])
    retrieved = {(_source_key(str(item.get("source", ""))), item.get("page")) for item in top_hits}
    if not expected_sources.issubset(retrieved):
        return "retrieval_or_reranking"
    if response.get("status") not in sample["expected_statuses"]:
        return "answerability" if not response.get("model_used") else "generation_or_grounding"
    if not _expected_sources_present(sample["expected_citations"], response.get("citations", [])):
        return "citation"
    return None


def _parse_sse(body: str) -> dict[str, Any]:
    completed: dict[str, Any] | None = None
    for raw_event in body.replace("\r\n", "\n").split("\n\n"):
        event = ""
        data = ""
        for line in raw_event.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if event == "error":
            payload = json.loads(data or "{}")
            raise RuntimeError(str(payload.get("message", "SSE query failed")))
        if event == "done":
            completed = json.loads(data)
    if completed is None:
        raise RuntimeError("SSE stream ended without a done event")
    return completed


@dataclass(slots=True)
class AuditContext:
    bids: dict[str, Bid]
    sources_by_bid: dict[str, set[str]]


async def _load_context(knowledge_base_id: str) -> AuditContext:
    async with get_session_factory()() as session:
        result = await session.execute(
            select(Bid).where(Bid.knowledge_base_id == knowledge_base_id)
        )
        bids = {bid.bid_key: bid for bid in result.scalars()}
        sources_by_bid: dict[str, set[str]] = {}
        for key, bid in bids.items():
            source_result = await session.execute(
                select(Document.source_name).where(
                    Document.bid_id == bid.id,
                    Document.status == "active",
                )
            )
            sources_by_bid[key] = {_source_key(name) for name in source_result.scalars()}
    return AuditContext(bids=bids, sources_by_bid=sources_by_bid)


async def _assert_unseen(samples: list[dict[str, Any]], knowledge_base_id: str) -> None:
    hashes = {question_hash(str(sample["question"])) for sample in samples}
    async with get_session_factory()() as session:
        result = await session.execute(
            select(QueryLog.query_hash).where(
                QueryLog.knowledge_base_id == knowledge_base_id,
                QueryLog.query_hash.in_(hashes),
            )
        )
        seen = sorted(set(result.scalars()))
    if seen:
        raise ValueError(f"Baseline requires unseen questions; {len(seen)} hashes already exist")


async def _clear_sample_caches(
    samples: list[dict[str, Any]], context: AuditContext, knowledge_base_id: str
) -> int:
    services = get_services()
    kb_version = await services.cache.kb_version(knowledge_base_id)
    keys: set[bytes | str] = set()
    scopes: set[str] = set()
    for sample in samples:
        bid = context.bids[str(sample["bid_key"])]
        metadata_filter = {"bid_id": {"$eq": str(bid.id)}}
        scope = stable_hash(metadata_filter)[:16]
        scopes.add(scope)
        keys.add(
            services.cache.exact_key(
                str(sample["question"]), kb_version, services.engine.config_version, scope
            )
        )
    for scope in scopes:
        pattern = f"rag:semantic:{kb_version}:{services.engine.config_version}:{scope}:*"
        keys.update([key async for key in services.cache.redis.scan_iter(match=pattern, count=100)])
    return int(await services.cache.redis.delete(*keys)) if keys else 0


async def _post_query(
    client: httpx.AsyncClient, path: str, payload: dict[str, Any]
) -> dict[str, Any]:
    response = await client.post(path, json=payload)
    response.raise_for_status()
    if path.endswith("/stream"):
        return _parse_sse(response.text)
    result = response.json()
    if not isinstance(result, dict):
        raise TypeError("Query API returned a non-object response")
    return result


async def run_audit(
    dataset_path: Path,
    knowledge_base_id: str,
    base_url: str,
    require_unseen: bool,
    fresh_query_cache: bool,
) -> dict[str, Any]:
    novelty = validate_dataset_novelty(dataset_path, dataset_path.parent)
    samples = load_samples(dataset_path)
    if require_unseen:
        await _assert_unseen(samples, knowledge_base_id)
    context = await _load_context(knowledge_base_id)
    missing_bids = sorted({str(item["bid_key"]) for item in samples} - context.bids.keys())
    if missing_bids:
        raise ValueError(f"Unknown bid keys: {missing_bids}")
    cleared = (
        await _clear_sample_caches(samples, context, knowledge_base_id) if fresh_query_cache else 0
    )

    settings = get_services().settings
    headers: dict[str, str] = {}
    if settings.app_api_key:
        headers["X-API-Key"] = settings.app_api_key.get_secret_value()
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=240.0) as client:
        for sample in samples:
            bid = context.bids[str(sample["bid_key"])]
            payload = {
                "query": sample["question"],
                "knowledge_base_id": knowledge_base_id,
                "metadata_filter": {"bid_id": {"$eq": str(bid.id)}},
            }
            transport_error: str | None = None
            first: dict[str, Any] = {}
            second: dict[str, Any] = {}
            streamed: dict[str, Any] = {}
            try:
                first = await _post_query(client, "/api/v1/query", payload)
                second = await _post_query(client, "/api/v1/query", payload)
                streamed = await _post_query(client, "/api/v1/query/stream", payload)
            except Exception as exc:
                transport_error = f"{type(exc).__name__}: {exc}"

            negative = bool(sample["negative_control"])
            citations = first.get("citations", [])
            answer_coverage = _keyword_coverage(
                str(first.get("answer", "")), sample.get("expected_answer_keywords", [])
            )
            evidence_coverage = _keyword_coverage(
                " ".join(str(item.get("quote", "")) for item in citations),
                sample.get("expected_evidence_keywords", []),
            )
            expected_citations_pass = (
                not citations
                if negative
                else _expected_sources_present(sample["expected_citations"], citations)
            )
            other_sources = set().union(
                *(
                    sources
                    for key, sources in context.sources_by_bid.items()
                    if key != sample["bid_key"]
                )
            )
            cross_bid_citation = any(
                _source_key(str(item.get("document", ""))) in other_sources for item in citations
            )
            sync_consistent = (
                first.get("answer") == second.get("answer")
                and first.get("status") == second.get("status")
                and _canonical_citations(first) == _canonical_citations(second)
            )
            sse_parity = (
                first.get("answer") == streamed.get("answer")
                and first.get("status") == streamed.get("status")
                and _canonical_citations(first) == _canonical_citations(streamed)
            )
            cache_pass = (
                not bool(second.get("cache_hit")) if negative else bool(second.get("cache_hit"))
            )
            negative_pass = (
                first.get("status") in sample["expected_statuses"]
                and first.get("answer") == FALLBACK_TEXT
                and not citations
                and second.get("answer") == FALLBACK_TEXT
            )
            supported_pass = (
                first.get("status") in sample["expected_statuses"]
                and answer_coverage == 1.0
                and evidence_coverage == 1.0
                and expected_citations_pass
                and not cross_bid_citation
            )
            passed = (
                transport_error is None
                and (negative_pass if negative else supported_pass)
                and sync_consistent
                and sse_parity
                and cache_pass
            )
            failure_stage = None if passed else classify_failure(sample, first, transport_error)
            if not passed and failure_stage is None:
                if not cache_pass:
                    failure_stage = "cache"
                elif not sse_parity:
                    failure_stage = "transport_parity"
                elif answer_coverage < 1 or evidence_coverage < 1:
                    failure_stage = "generation_or_citation_content"
            results.append(
                {
                    "id": sample["id"],
                    "bid_key": sample["bid_key"],
                    "question": sample["question"],
                    "question_hash": question_hash(str(sample["question"])),
                    "negative_control": negative,
                    "expected_answer": sample["expected_answer"],
                    "expected_citations": sample["expected_citations"],
                    "first_sync": first,
                    "second_sync": second,
                    "sse": streamed,
                    "checks": {
                        "answer_keyword_coverage": answer_coverage,
                        "evidence_keyword_coverage": evidence_coverage,
                        "expected_citations": expected_citations_pass,
                        "cross_bid_citation": cross_bid_citation,
                        "sync_cache_consistency": sync_consistent,
                        "second_pass_cache": cache_pass,
                        "sync_sse_parity": sse_parity,
                    },
                    "failure_stage": failure_stage,
                    "transport_error": transport_error,
                    "passed": passed,
                }
            )

    supported = [item for item in results if not item["negative_control"]]
    negatives = [item for item in results if item["negative_control"]]
    dataset_payload = await asyncio.to_thread(
        lambda: json.loads(dataset_path.read_text(encoding="utf-8"))
    )
    return {
        "audit_id": dataset_payload.get("audit_id"),
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": str(dataset_path),
        "knowledge_base_id": knowledge_base_id,
        "mode": "require-unseen" if require_unseen else "allow-seen",
        "novelty": novelty,
        "cleared_query_cache_keys": cleared,
        "summary": {
            "passed": all(item["passed"] for item in results),
            "supported_passed": sum(item["passed"] for item in supported),
            "supported_total": len(supported),
            "negative_passed": sum(item["passed"] for item in negatives),
            "negative_total": len(negatives),
            "sync_sse_parity_rate": sum(item["checks"]["sync_sse_parity"] for item in results)
            / max(1, len(results)),
            "cache_consistency_rate": sum(item["checks"]["second_pass_cache"] for item in results)
            / max(1, len(results)),
            "failure_stages": {
                stage: sum(item["failure_stage"] == stage for item in results)
                for stage in sorted(
                    {str(item["failure_stage"]) for item in results if item["failure_stage"]}
                )
            },
        },
        "samples": results,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the bid-scoped unseen-question audit")
    parser.add_argument("--dataset", default="evaluation/datasets/rfp_unseen_audit.json")
    parser.add_argument("--knowledge-base", default="rfp-intelligence")
    parser.add_argument("--base-url", default="http://api:8000")
    parser.add_argument("--output", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--require-unseen", action="store_true")
    mode.add_argument("--allow-seen", action="store_true")
    parser.add_argument("--fresh-query-cache", action="store_true")
    args = parser.parse_args()

    output_path = Path(args.output)
    if await asyncio.to_thread(output_path.exists):
        raise FileExistsError(f"Audit reports are immutable; output already exists: {output_path}")
    report = await run_audit(
        Path(args.dataset),
        args.knowledge_base,
        args.base_url.rstrip("/"),
        args.require_unseen,
        args.fresh_query_cache,
    )
    await asyncio.to_thread(output_path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(
        output_path.write_text,
        json.dumps(report, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output_path), **report["summary"]}, indent=2))
    if not report["summary"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
