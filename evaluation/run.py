from __future__ import annotations

import argparse
import asyncio
import html
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.db import get_session_factory
from app.models import Bid, EvaluationRun
from app.schemas import AnswerStatus, QueryRequest
from app.services import get_services
from evaluation.metrics import aggregate_native, keyword_coverage, ndcg, reciprocal_rank, token_f1


def load_dataset(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data if isinstance(data, list) else data.get("samples", [])
    required = {"question", "expected_answer", "expected_sources"}
    for index, record in enumerate(records):
        missing = required - record.keys()
        if missing:
            raise ValueError(f"Evaluation sample {index} is missing: {sorted(missing)}")
        if not isinstance(record.get("expected_sources"), list):
            raise ValueError(f"Evaluation sample {index} expected_sources must be a list")
        if record.get("expected_pages") and not all(
            isinstance(page, int) and page > 0 for page in record["expected_pages"]
        ):
            raise ValueError(
                f"Evaluation sample {index} expected_pages must contain positive integers"
            )
    return records


def _source_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


async def evaluate_dataset(path: Path, knowledge_base_id: str) -> dict[str, Any]:
    samples = load_dataset(path)
    engine = get_services().engine
    async with get_session_factory()() as session:
        bid_rows = await session.execute(
            select(Bid).where(Bid.knowledge_base_id == knowledge_base_id)
        )
        bids = {bid.bid_key: bid for bid in bid_rows.scalars()}
    detailed: list[dict[str, Any]] = []
    for sample in samples:
        metadata_filter: dict[str, Any] = {}
        if bid_key := sample.get("bid_key"):
            bid = bids.get(str(bid_key))
            if not bid:
                raise ValueError(f"Unknown bid in evaluation dataset: {bid_key}")
            metadata_filter = {"bid_id": {"$eq": str(bid.id)}}
        started = perf_counter()
        response = await engine.answer(
            QueryRequest(
                query=sample["question"],
                knowledge_base_id=knowledge_base_id,
                metadata_filter=metadata_filter,
            )
        )
        cached_response = await engine.answer(
            QueryRequest(
                query=sample["question"],
                knowledge_base_id=knowledge_base_id,
                metadata_filter=metadata_filter,
            )
        )
        latency = (perf_counter() - started) * 1000
        sources = [citation.document for citation in response.citations]
        expected = set(sample["expected_sources"])
        source_keys = {_source_key(source) for source in sources}
        expected_keys = {_source_key(source) for source in expected}
        negative_control = bool(sample.get("negative_control", False))
        expected_statuses = set(sample.get("expected_statuses", ["answered"]))
        citation_text = " ".join(citation.quote or "" for citation in response.citations)
        expected_pages = set(sample.get("expected_pages", []))
        cited_pages = {
            citation.page for citation in response.citations if citation.page is not None
        }
        status_pass = response.status.value in expected_statuses
        answer_keyword_pass = keyword_coverage(
            response.answer, sample.get("expected_answer_keywords", [])
        )
        evidence_keyword_pass = keyword_coverage(
            citation_text, sample.get("expected_evidence_keywords", [])
        )
        citation_pass = (
            not response.citations
            if negative_control
            else bool(response.citations) and bool(expected_keys & source_keys)
        )
        page_pass = not expected_pages or expected_pages.issubset(cited_pages)
        cache_consistency_pass = (
            cached_response.cache_hit == (not negative_control)
            and cached_response.answer == response.answer
            and cached_response.status == response.status
        )
        if negative_control:
            answer_keyword_pass = float(status_pass and not response.citations)
            evidence_keyword_pass = float(not response.citations)
        else:
            answer_keyword_pass = float(answer_keyword_pass)
            evidence_keyword_pass = float(evidence_keyword_pass)
        sample_result = {
            "question": sample["question"],
            "answer": response.answer,
            "expected_answer": sample["expected_answer"],
            "sources": sources,
            "evidence": [citation.quote or "" for citation in response.citations],
            "expected_sources": sorted(expected),
            "answer_token_f1": token_f1(response.answer, sample["expected_answer"]),
            "answer_keyword_coverage": answer_keyword_pass,
            "evidence_keyword_coverage": evidence_keyword_pass,
            "hit_rate": float(
                (not sources) if negative_control else bool(expected_keys & source_keys)
            ),
            "status": response.status.value,
            "expected_statuses": sorted(expected_statuses),
            "negative_control": negative_control,
            "status_pass": float(status_pass),
            "citation_pass": float(citation_pass),
            "pages": sorted(cited_pages),
            "expected_pages": sorted(expected_pages),
            "page_pass": float(page_pass),
            "route": response.route.value,
            "retrieval_metadata": response.retrieval_metadata.model_dump(),
            "mrr": (
                1.0
                if negative_control and not sources
                else reciprocal_rank([_source_key(source) for source in sources], expected_keys)
            ),
            "ndcg": (
                1.0
                if negative_control and not sources
                else ndcg([_source_key(source) for source in sources], expected_keys)
            ),
            "fallback": float(
                response.status in {AnswerStatus.NOT_FOUND, AnswerStatus.INSUFFICIENT_EVIDENCE}
            ),
            "cache_hit": float(response.cache_hit),
            "latency_ms": latency,
            "total_tokens": response.token_usage.total_tokens,
            "cache_consistency_pass": float(cache_consistency_pass),
            "second_pass_cache_hit": cached_response.cache_hit,
        }
        sample_result["quality_gate_pass"] = float(
            bool(sample_result["status_pass"])
            and bool(sample_result["citation_pass"])
            and bool(sample_result["page_pass"])
            and sample_result["answer_keyword_coverage"] == 1.0
            and sample_result["evidence_keyword_coverage"] == 1.0
            and bool(sample_result["cache_consistency_pass"])
        )
        detailed.append(sample_result)
    native = aggregate_native(detailed)
    external = await run_external_metrics(detailed)
    failed_questions = [item["question"] for item in detailed if not item["quality_gate_pass"]]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": str(path),
        "sample_count": len(samples),
        "native_metrics": native,
        "framework_metrics": external,
        "quality_gate": {
            "passed": not failed_questions,
            "failed_count": len(failed_questions),
            "failed_questions": failed_questions,
        },
        "failure_cases": [item for item in detailed if not item["quality_gate_pass"]],
        "samples": detailed,
    }


async def run_external_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    try:
        from deepeval import evaluate as deepeval_evaluate
        from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric
        from deepeval.test_case import LLMTestCase

        test_cases = [
            LLMTestCase(
                input=item["question"],
                actual_output=item["answer"],
                expected_output=item["expected_answer"],
                retrieval_context=item["evidence"],
            )
            for item in results
        ]
        deepeval_result = await asyncio.to_thread(
            deepeval_evaluate,
            test_cases=test_cases,
            metrics=[AnswerRelevancyMetric(), FaithfulnessMetric()],
        )
        output["deepeval"] = {"status": "completed", "result": str(deepeval_result)}
    except Exception as exc:
        output["deepeval"] = {"status": "unavailable", "reason": type(exc).__name__}

    try:
        from datasets import Dataset
        from ragas import evaluate as ragas_evaluate
        from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

        dataset = Dataset.from_dict(
            {
                "question": [item["question"] for item in results],
                "answer": [item["answer"] for item in results],
                "contexts": [item["evidence"] for item in results],
                "ground_truth": [item["expected_answer"] for item in results],
            }
        )
        ragas_result = await asyncio.to_thread(
            ragas_evaluate,
            dataset,
            metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        )
        output["ragas"] = {
            "status": "completed",
            "result": ragas_result.to_pandas().mean(numeric_only=True).to_dict(),
        }
    except Exception as exc:
        output["ragas"] = {"status": "unavailable", "reason": type(exc).__name__}
    return output


def write_reports(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"evaluation-{stamp}.json"
    html_path = output_dir / f"evaluation-{stamp}.html"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    rows = "".join(
        f"<tr><td>{html.escape(key)}</td><td>{value:.4f}</td></tr>"
        for key, value in report["native_metrics"].items()
    )
    generated_at = html.escape(report["generated_at"])
    framework_metrics = html.escape(json.dumps(report["framework_metrics"], indent=2, default=str))
    failure_cases = html.escape(json.dumps(report["failure_cases"], indent=2, default=str))
    html_path.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>Advance RAG Evaluation</title>"
        "<style>body{font-family:system-ui;max-width:900px;margin:40px auto;color:#172033}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #d8deea;padding:10px}"
        "th{background:#eef3ff}</style></head><body><h1>Advance RAG Evaluation</h1>"
        f"<p>Samples: {report['sample_count']} · Generated: {generated_at}</p>"
        f"<table><tr><th>Metric</th><th>Value</th></tr>{rows}</table>"
        f"<h2>Framework metrics</h2><pre>{framework_metrics}</pre>"
        f"<h2>Failure cases</h2><pre>{failure_cases}</pre>"
        "</body></html>",
        encoding="utf-8",
    )
    return json_path, html_path


async def run_saved_evaluation(evaluation_id: UUID) -> None:
    async with get_session_factory()() as session:
        run = await session.get(EvaluationRun, evaluation_id)
        if not run:
            raise ValueError("Evaluation run not found")
        run.status = "running"
        await session.commit()
        try:
            report = await evaluate_dataset(Path(run.dataset_name), run.knowledge_base_id)
            json_path, _ = write_reports(report, Path("reports"))
            run.status = "completed"
            run.metrics = report["native_metrics"]
            run.report_path = str(json_path)
        except Exception as exc:
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"[:2000]
        await session.commit()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run Advance RAG evaluation")
    parser.add_argument("--dataset", default="evaluation/datasets/example.json")
    parser.add_argument("--knowledge-base", default="default")
    parser.add_argument("--output", default="reports")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help=(
            "Clear exact and semantic response caches while preserving embeddings before evaluation"
        ),
    )
    args = parser.parse_args()
    engine = get_services().engine
    if args.fresh:
        kb_version = await engine.cache.kb_version(args.knowledge_base)
        deleted = await engine.cache.clear_query_cache(kb_version, engine.config_version)
        print(json.dumps({"cleared_query_cache_keys": deleted}))
    report = await evaluate_dataset(Path(args.dataset), args.knowledge_base)
    json_path, html_path = write_reports(report, Path(args.output))
    print(
        json.dumps(
            {
                "json_report": str(json_path),
                "html_report": str(html_path),
                "metrics": report["native_metrics"],
                "quality_gate": report["quality_gate"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
