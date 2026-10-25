from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db import get_session_factory
from app.models import Bid
from app.rfp.search import rfp_query_variants
from app.services import get_services
from evaluation.metrics import ndcg, reciprocal_rank
from evaluation.run import load_dataset

MODES = ("dense", "hybrid", "hybrid_rerank")


def _source_key(value: str) -> str:
    """Match a managed safe filename to its original assignment filename."""

    return re.sub(r"[^a-z0-9]", "", value.lower())


async def run_benchmark(
    dataset: Path,
    knowledge_base_id: str,
    top_k: int = 5,
) -> dict[str, Any]:
    samples = load_dataset(dataset)
    answerable_samples = [sample for sample in samples if not sample.get("negative_control")]
    services = get_services()
    async with get_session_factory()() as session:
        rows = await session.execute(select(Bid).where(Bid.knowledge_base_id == knowledge_base_id))
        bids = {bid.bid_key: bid for bid in rows.scalars()}

    configurations: dict[str, Any] = {}
    for mode in MODES:
        details: list[dict[str, Any]] = []
        for sample in answerable_samples:
            bid = bids.get(str(sample.get("bid_key", "")))
            if not bid:
                raise ValueError(f"Unknown bid in evaluation dataset: {sample.get('bid_key')}")
            variants = rfp_query_variants(sample["question"])
            result = await services.retriever.retrieve(
                query=variants[0],
                metadata_filter={"bid_id": {"$eq": str(bid.id)}},
                namespace=f"kb-{knowledge_base_id}",
                mode=mode,
                top_k_override=top_k,
            )
            sources = [hit.source_name for hit in result.hits]
            expected = set(sample["expected_sources"])
            source_keys = [_source_key(source) for source in sources]
            expected_keys = {_source_key(source) for source in expected}
            details.append(
                {
                    "bid_key": bid.bid_key,
                    "question": sample["question"],
                    "expected_sources": sorted(expected),
                    "retrieved_sources": sources,
                    "recall_at_k": float(bool(expected_keys & set(source_keys))),
                    "mrr": reciprocal_rank(source_keys, expected_keys),
                    "ndcg": ndcg(source_keys, expected_keys),
                }
            )
        count = len(details) or 1
        configurations[mode] = {
            "recall_at_k": sum(item["recall_at_k"] for item in details) / count,
            "mrr": sum(item["mrr"] for item in details) / count,
            "ndcg": sum(item["ndcg"] for item in details) / count,
            "samples": details,
        }
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": str(dataset),
        "sample_count": len(answerable_samples),
        "top_k": top_k,
        "configurations": configurations,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="evaluation/datasets/rfp_retrieval.json")
    parser.add_argument("--knowledge-base", default="rfp-intelligence")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", default="reports/rfp-retrieval-benchmark.json")
    args = parser.parse_args()
    report = await run_benchmark(Path(args.dataset), args.knowledge_base, top_k=args.top_k)
    output = Path(args.output)
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(output.write_text, json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "configurations": report["configurations"]}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
