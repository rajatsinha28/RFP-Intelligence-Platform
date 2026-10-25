from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
from sqlalchemy import select

from app.db import get_session_factory
from app.models import Bid
from evaluation.run import load_dataset


async def _stream_done(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    event_name = ""
    async with client.stream(
        "POST", "/api/v1/query/stream", json=payload, headers=headers
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ")
            elif line.startswith("data: ") and event_name == "done":
                return cast(dict[str, Any], json.loads(line.removeprefix("data: ")))
    raise RuntimeError("SSE stream ended without a done event")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Generate cited RFP Q&A and verify SSE parity")
    parser.add_argument("--dataset", default="evaluation/datasets/rfp_retrieval.json")
    parser.add_argument("--knowledge-base", default="rfp-intelligence")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--negative-only", action="store_true")
    parser.add_argument("--base-url", default="http://api:8000")
    parser.add_argument("--output", default="outputs/reports/rfp-sample-qa.json")
    args = parser.parse_args()

    samples = [
        sample
        for sample in load_dataset(Path(args.dataset))
        if bool(sample.get("negative_control")) == args.negative_only
    ][: args.limit]
    async with get_session_factory()() as session:
        rows = await session.execute(
            select(Bid).where(Bid.knowledge_base_id == args.knowledge_base)
        )
        bids = {bid.bid_key: bid for bid in rows.scalars()}

    api_key = os.environ.get("APP_API_KEY", "")
    if not api_key:
        raise RuntimeError("APP_API_KEY is required")
    headers = {"X-API-Key": api_key}
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(base_url=args.base_url, timeout=180) as client:
        for sample in samples:
            bid = bids.get(str(sample.get("bid_key", "")))
            if not bid:
                raise ValueError(f"Unknown bid {sample.get('bid_key')}")
            payload = {
                "query": sample["question"],
                "knowledge_base_id": args.knowledge_base,
                "metadata_filter": {"bid_id": {"$eq": str(bid.id)}},
            }
            sync_data: dict[str, Any] = {}
            attempts = 0
            is_negative = bool(sample.get("negative_control"))
            while attempts < args.max_attempts:
                attempts += 1
                sync_response = await client.post("/api/v1/query", json=payload, headers=headers)
                sync_response.raise_for_status()
                sync_data = sync_response.json()
                if is_negative or (
                    sync_data.get("status") == "answered" and sync_data.get("citations")
                ):
                    break
            stream_data = await _stream_done(client, payload, headers)
            citations = sync_data.get("citations", [])
            parity = all(
                sync_data.get(key) == stream_data.get(key)
                for key in ("answer", "status", "citations")
            )
            if is_negative:
                response_pass = (
                    sync_data.get("status")
                    in sample.get("expected_statuses", ["not_found", "insufficient_evidence"])
                    and not citations
                )
            else:
                response_pass = sync_data.get("status") == "answered" and bool(citations)
            results.append(
                {
                    "bid_key": sample["bid_key"],
                    "question": sample["question"],
                    "answer": sync_data.get("answer"),
                    "status": sync_data.get("status"),
                    "route": sync_data.get("route"),
                    "citations": citations,
                    "citation_count": len(citations),
                    "attempts": attempts,
                    "sync_sse_parity": parity,
                    "negative_control": is_negative,
                    "passed": response_pass and parity,
                }
            )

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "knowledge_base_id": args.knowledge_base,
        "sample_count": len(results),
        "passed": all(item["passed"] for item in results),
        "sync_sse_parity_rate": (
            sum(item["sync_sse_parity"] for item in results) / len(results) if results else 0
        ),
        "samples": results,
    }
    output = Path(args.output)
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(
        output.write_text,
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output),
                **{k: report[k] for k in ("passed", "sample_count", "sync_sse_parity_rate")},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
