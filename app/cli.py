from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings


def _client(base_url: str) -> httpx.Client:
    settings = get_settings()
    headers: dict[str, str] = {}
    if settings.app_api_key:
        headers["X-API-Key"] = settings.app_api_key.get_secret_value()
    return httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=120)


def _show(response: httpx.Response) -> None:
    response.raise_for_status()
    if response.content:
        print(json.dumps(response.json(), indent=2, default=str))


def _ingest_folder(client: httpx.Client, args: argparse.Namespace) -> None:
    root = Path(args.path).resolve()
    if not root.is_dir():
        raise SystemExit(f"Not a directory: {root}")
    bid_folders = sorted(path for path in root.iterdir() if path.is_dir())
    if not bid_folders:
        raise SystemExit(f"No bid subfolders found: {root}")
    results: list[dict[str, Any]] = []
    for folder in bid_folders:
        nested = argparse.Namespace(
            path=str(folder),
            bid_id=folder.name,
            title=folder.name,
            knowledge_base=args.knowledge_base,
        )
        _ingest_bid(client, nested)
        results.append({"bid_id": folder.name, "source_folder": str(folder)})
    root_files = [item.name for item in root.iterdir() if item.is_file()]
    print(json.dumps({"imported_bids": results, "ignored_root_files": root_files}, indent=2))


def _ingest_bid(client: httpx.Client, args: argparse.Namespace) -> None:
    folder = Path(args.path).resolve()
    if not folder.is_dir():
        raise SystemExit(f"Not a directory: {folder}")
    create = client.post(
        "/api/v1/bids",
        json={
            "bid_key": args.bid_id,
            "title": args.title or args.bid_id,
            "knowledge_base_id": args.knowledge_base,
            "source_folder": str(folder),
        },
    )
    if create.status_code not in {201, 409}:
        _show(create)
    handles = []
    try:
        files: list[tuple[str, tuple[str, Any, str]]] = []
        for path in sorted(item for item in folder.rglob("*") if item.is_file()):
            handle = path.open("rb")
            handles.append(handle)
            files.append(("files", (path.name, handle, "application/octet-stream")))
        _show(client.post(f"/api/v1/bids/{args.bid_id}/documents", files=files))
    finally:
        for handle in handles:
            handle.close()


def _search(client: httpx.Client, args: argparse.Namespace) -> None:
    _show(
        client.post(
            "/api/v1/search",
            json={
                "query": args.query,
                "knowledge_base_id": args.knowledge_base,
                "bid_keys": [args.bid_id] if args.bid_id else [],
                "doc_types": args.doc_type or [],
                "top_k": args.top_k,
                "mode": args.mode,
            },
        )
    )


def _extract(client: httpx.Client, args: argparse.Namespace) -> None:
    _show(client.post(f"/api/v1/bids/{args.bid_id}/extractions"))


def _ask(client: httpx.Client, args: argparse.Namespace) -> None:
    metadata_filter = {"bid_id": {"$eq": args.bid_uuid}} if args.bid_uuid else {}
    _show(
        client.post(
            "/api/v1/query",
            json={
                "query": args.query,
                "knowledge_base_id": args.knowledge_base,
                "metadata_filter": metadata_filter,
            },
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RFP Intelligence Platform CLI")
    parser.add_argument("--base-url", default=os.getenv("ADVANCE_RAG_URL", "http://localhost:8000"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    folder = subparsers.add_parser("ingest-folder")
    folder.add_argument("--path", required=True)
    folder.add_argument("--knowledge-base", default="rfp-intelligence")
    folder.set_defaults(handler=_ingest_folder)

    bid = subparsers.add_parser("ingest-bid")
    bid.add_argument("--path", required=True)
    bid.add_argument("--bid-id", required=True)
    bid.add_argument("--title")
    bid.add_argument("--knowledge-base", default="rfp-intelligence")
    bid.set_defaults(handler=_ingest_bid)

    search = subparsers.add_parser("search")
    search.add_argument("--query", required=True)
    search.add_argument("--bid-id")
    search.add_argument("--doc-type", action="append")
    search.add_argument("--top-k", type=int, default=8)
    search.add_argument(
        "--mode", choices=["dense", "hybrid", "hybrid_rerank"], default="hybrid_rerank"
    )
    search.add_argument("--knowledge-base", default="rfp-intelligence")
    search.set_defaults(handler=_search)

    extract = subparsers.add_parser("extract")
    extract.add_argument("--bid-id", required=True)
    extract.set_defaults(handler=_extract)

    ask = subparsers.add_parser("ask")
    ask.add_argument("--query", required=True)
    ask.add_argument("--bid-uuid")
    ask.add_argument("--knowledge-base", default="rfp-intelligence")
    ask.set_defaults(handler=_ask)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        with _client(args.base_url) as client:
            args.handler(client, args)
    except httpx.HTTPStatusError as exc:
        print(exc.response.text, file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
