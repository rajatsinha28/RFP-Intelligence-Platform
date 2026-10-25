from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.config import get_settings
from app.db import get_session_factory
from app.models import AgentStep, Bid, Document, ExtractionRun
from app.observability.logging import get_logger
from app.rfp.agents import (
    RFPAgentModel,
    SpecialistResult,
    add_usage,
    extract_specialist_fields,
)
from app.rfp.schemas import (
    COMMERCIAL_COMPLIANCE_FIELDS,
    DATES_LOGISTICS_FIELDS,
    FIELD_DEFINITIONS,
    PRODUCT_SPEC_FIELDS,
    REQUIRED_FIELDS,
    AddendumChange,
    BidRecord,
    FieldResult,
    FieldStatus,
    RunMetadata,
    SourceRef,
    ValidationIssue,
    ValidationSummary,
)
from app.schemas import RetrievalHit, TokenUsage
from app.services import get_services

logger = get_logger()


def _enforce_field_ownership(fields: dict[str, FieldResult]) -> None:
    """Keep schedule facts in their designated fields before validation/reporting.

    RFP product tables often contain a generic ``Due Date`` column that repeats the
    proposal deadline.  It is not a delivery promise unless the surrounding evidence
    explicitly labels it as one.  The dedicated Delivery Date field owns delivery
    requirements, while Product Specification owns technical requirements.
    """

    product_spec = fields.get("Product Specification")
    if product_spec and isinstance(product_spec.value, list):
        retained: list[Any] = []
        for item in product_spec.value:
            if not isinstance(item, str):
                retained.append(item)
                continue
            if re.search(r"\b(?:required\s+)?delivery\s+date\b", item, re.IGNORECASE):
                continue
            cleaned = re.sub(
                r";\s*(?:required\s+)?(?:delivery\s+)?due(?:\s+date)?\s+"
                r"(?:by\s+)?(?:[A-Za-z]+\s+\d{1,2},\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})\.?",
                ".",
                item,
                flags=re.IGNORECASE,
            )
            retained.append(cleaned)
        if retained != product_spec.value:
            product_spec.value = retained
            product_spec.notes = (
                f"{product_spec.notes} Schedule statements were removed because delivery "
                "requirements belong to the Delivery Date field."
            ).strip()

    delivery = fields.get("Delivery Date")
    if not delivery or not isinstance(delivery.value, str):
        return
    explicit_window = re.search(
        r"(?:delivery\s+)?within\s+\d+\s+(?:business\s+)?days?\s+of\s+award",
        delivery.value,
        re.IGNORECASE,
    )
    if explicit_window and ";" in delivery.value:
        delivery.value = explicit_window.group(0).strip().capitalize()
        delivery.sources = [
            source
            for source in delivery.sources
            if explicit_window.group(0).lower() in source.quote.lower()
        ] or delivery.sources[:1]
        delivery.status = FieldStatus.SUPPORTED
        delivery.notes = (
            "Used the explicit delivery-after-award requirement. A generic product-table "
            "due-date column is not treated as a delivery deadline."
        )


async def _record_step(
    run_id: UUID,
    trace: list[dict[str, Any]],
    *,
    agent_name: str,
    stage: str,
    status: str,
    attempt: int = 1,
    input_summary: dict[str, Any] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    output_summary: dict[str, Any] | None = None,
    token_usage: TokenUsage | None = None,
    latency_ms: float = 0,
    error: str | None = None,
) -> None:
    item = {
        "agent": agent_name,
        "stage": stage,
        "status": status,
        "attempt": attempt,
        "input_summary": input_summary or {},
        "tool_calls": tool_calls or [],
        "output_summary": output_summary or {},
        "token_usage": (token_usage or TokenUsage()).model_dump(),
        "latency_ms": round(latency_ms, 3),
        "error": error,
    }
    trace.append(item)
    async with get_session_factory()() as session:
        session.add(
            AgentStep(
                run_id=run_id,
                agent_name=agent_name,
                stage=stage,
                status=status,
                attempt=attempt,
                input_summary=item["input_summary"],
                tool_calls=item["tool_calls"],
                output_summary=item["output_summary"],
                token_usage=item["token_usage"],
                latency_ms=item["latency_ms"],
                error=error,
            )
        )
        await session.commit()


def _validate_fields(fields: dict[str, FieldResult]) -> ValidationSummary:
    issues: list[ValidationIssue] = []
    passed = 0
    not_found = 0
    for field_name in REQUIRED_FIELDS:
        result = fields.get(field_name)
        if result is None:
            issues.append(
                ValidationIssue(field=field_name, code="missing_field", message="Field absent")
            )
            continue
        if result.status == FieldStatus.NOT_FOUND:
            result.value = None
            result.sources = []
            result.notes = result.notes or "Not found in documents"
            not_found += 1
            continue
        if result.value is None:
            result.status = FieldStatus.INVALID
            issues.append(
                ValidationIssue(
                    field=field_name,
                    code="null_supported_value",
                    message="A supported field cannot have a null value",
                )
            )
            continue
        if not result.sources:
            result.status = FieldStatus.INVALID
            issues.append(
                ValidationIssue(
                    field=field_name,
                    code="missing_citation",
                    message="Non-null value has no valid citation",
                )
            )
            continue
        if any(not source.chunk_id or not source.file for source in result.sources):
            result.status = FieldStatus.INVALID
            issues.append(
                ValidationIssue(
                    field=field_name,
                    code="invalid_citation",
                    message="Citation is not traceable",
                )
            )
            continue
        passed += 1
    return ValidationSummary(
        passed=passed,
        failed=len(issues),
        not_found=not_found,
        issues=issues,
    )


async def _reconcile_addenda(
    *,
    bid_id: str,
    knowledge_base_id: str,
    fields: dict[str, FieldResult],
    model: RFPAgentModel,
) -> tuple[list[AddendumChange], TokenUsage, dict[str, RetrievalHit]]:
    services = get_services()
    result = await services.retriever.retrieve(
        query=(
            "List every explicit addendum change, revised requirement, extension, correction, "
            "replacement, or clarification that affects the bid fields."
        ),
        metadata_filter={
            "bid_id": {"$eq": bid_id},
            "doc_type": {"$eq": "addendum"},
        },
        namespace=f"kb-{knowledge_base_id}",
        mode="hybrid_rerank",
        top_k_override=12,
    )
    evidence = {hit.chunk_id: hit for hit in result.hits}
    if not evidence:
        return [], TokenUsage(), evidence
    draft = {
        name: {"value": value.value, "status": value.status.value}
        for name, value in fields.items()
        if name != "Bid Summary"
    }
    evidence_text = "\n\n".join(
        f'<evidence chunk_id="{hit.chunk_id}" file="{hit.source_name}" '
        f'page="{hit.page_number or ""}" '
        f'addendum_number="{hit.metadata.get("addendum_number", "")}">\n'
        f"{hit.text}\n</evidence>"
        for hit in evidence.values()
    )
    schema = {
        "type": "object",
        "properties": {
            "changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string", "enum": list(REQUIRED_FIELDS)},
                        "previous_value": {"type": ["string", "null"]},
                        "revised_value": {"type": ["string", "null"]},
                        "citation_id": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "rationale": {"type": "string"},
                    },
                    "required": [
                        "field",
                        "previous_value",
                        "revised_value",
                        "citation_id",
                        "confidence",
                        "rationale",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["changes"],
        "additionalProperties": False,
    }
    raw, usage = await model.structured_response(
        "rfp_addendum_changes",
        schema,
        (
            "You are the addendum reconciliation agent. Use only explicit changes in the supplied "
            "addendum evidence. Do not treat a restatement or unrelated mention as an override. "
            "Return no change when the evidence is ambiguous. Use exact evidence chunk IDs."
        ),
        f"CURRENT DRAFT:\n{json.dumps(draft, default=str)}\n\nADDENDUM EVIDENCE:\n{evidence_text}",
    )
    changes: list[AddendumChange] = []
    for item in raw["changes"]:
        hit = evidence.get(item["citation_id"])
        field_name = item["field"]
        revised = item["revised_value"]
        if not hit or field_name == "Bid Summary" or revised is None:
            continue
        source = SourceRef(
            document_id=hit.document_id,
            file=hit.source_name,
            page=hit.page_number,
            chunk_id=hit.chunk_id,
            quote=hit.text[:700],
            doc_type=str(hit.metadata.get("doc_type") or "") or None,
            addendum_number=hit.metadata.get("addendum_number"),
        )
        previous = fields.get(field_name, FieldResult()).value
        changes.append(
            AddendumChange(
                field=field_name,
                previous_value=previous,
                revised_value=revised,
                source=source,
                rationale=item["rationale"],
            )
        )
        fields[field_name] = FieldResult(
            value=revised,
            sources=[source],
            confidence=float(item["confidence"]),
            notes=item["rationale"],
            status=FieldStatus.SUPPORTED,
        )
    return changes, usage, evidence


async def _generate_summary(
    fields: dict[str, FieldResult],
    model: RFPAgentModel,
) -> tuple[FieldResult, TokenUsage]:
    supported = {
        name: value.value
        for name, value in fields.items()
        if value.status == FieldStatus.SUPPORTED and name != "Bid Summary"
    }
    sources = [
        source
        for value in fields.values()
        for source in value.sources
        if value.status == FieldStatus.SUPPORTED
    ]
    unique_sources = {source.chunk_id: source for source in sources}
    if not supported or not unique_sources:
        return (
            FieldResult(
                value=None,
                sources=[],
                confidence=0,
                notes="Not enough validated fields to generate a bid summary",
                status=FieldStatus.NOT_FOUND,
            ),
            TokenUsage(),
        )
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "citation_ids": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["summary", "citation_ids", "confidence"],
        "additionalProperties": False,
    }
    raw, usage = await model.structured_response(
        "rfp_bid_summary",
        schema,
        (
            "Write a concise three-to-six-sentence bid summary using only the validated field "
            "values. Cite exact chunk IDs supplied with those values. Do not add outside facts. "
            "Treat Due Date as the proposal deadline and Delivery Date as the delivery schedule. "
            "Do not reinterpret dates embedded in Product Specification as delivery dates."
        ),
        json.dumps(
            {
                "fields": supported,
                "available_citations": list(unique_sources),
            },
            default=str,
        ),
    )
    selected = [
        unique_sources[citation_id]
        for citation_id in raw["citation_ids"]
        if citation_id in unique_sources
    ]
    if not selected:
        selected = list(unique_sources.values())[:2]
    return (
        FieldResult(
            value=raw["summary"],
            sources=selected,
            confidence=float(raw["confidence"]),
            notes="Generated from validated bid fields",
            status=FieldStatus.SUPPORTED,
        ),
        usage,
    )


async def run_extraction(extraction_id: UUID) -> None:
    settings = get_settings()
    started_at = datetime.now(UTC)
    started_clock = perf_counter()
    trace: list[dict[str, Any]] = []
    total_usage = TokenUsage()
    model: RFPAgentModel | None = None
    try:
        async with get_session_factory()() as session:
            run = await session.get(ExtractionRun, extraction_id)
            if not run:
                raise ValueError(f"Unknown extraction run {extraction_id}")
            bid = await session.get(Bid, run.bid_id)
            if not bid:
                raise ValueError(f"Unknown bid {run.bid_id}")
            documents = list(
                (
                    await session.execute(
                        select(Document).where(
                            Document.bid_id == bid.id, Document.status == "active"
                        )
                    )
                ).scalars()
            )
            if not documents:
                raise ValueError("Bid has no active indexed documents")
            run.status = "running"
            bid.status = "extracting"
            await session.commit()
            bid_id = str(bid.id)
            bid_key = bid.bid_key
            knowledge_base_id = bid.knowledge_base_id

        await _record_step(
            extraction_id,
            trace,
            agent_name="orchestrator_planner",
            stage="plan",
            status="completed",
            input_summary={"bid_key": bid_key, "document_count": len(documents)},
            output_summary={"parallel_specialists": 3, "required_fields": len(REQUIRED_FIELDS)},
        )

        model = RFPAgentModel(settings)
        services = get_services()
        specialist_specs = (
            ("dates_logistics", DATES_LOGISTICS_FIELDS),
            ("commercial_compliance", COMMERCIAL_COMPLIANCE_FIELDS),
            ("product_specifications", PRODUCT_SPEC_FIELDS),
        )

        async def run_specialist(name: str, field_names: tuple[str, ...]) -> SpecialistResult:
            started = perf_counter()
            result = await extract_specialist_fields(
                agent_name=name,
                fields=field_names,
                definitions=FIELD_DEFINITIONS,
                bid_key=bid_key,
                bid_id=bid_id,
                knowledge_base_id=knowledge_base_id,
                services=services,
                model=model,
            )
            await _record_step(
                extraction_id,
                trace,
                agent_name=name,
                stage="retrieve_and_extract",
                status="completed",
                input_summary={"fields": list(field_names)},
                tool_calls=[{"tool": "hybrid_search", "calls": len(field_names)}],
                output_summary={
                    "field_count": len(result.fields),
                    "evidence_chunks": len(result.evidence),
                },
                token_usage=result.token_usage,
                latency_ms=(perf_counter() - started) * 1000,
            )
            return result

        specialist_results = await asyncio.gather(
            *(run_specialist(name, fields) for name, fields in specialist_specs),
            return_exceptions=True,
        )
        fields: dict[str, FieldResult] = {}
        evidence: dict[str, RetrievalHit] = {}
        for (name, field_names), result in zip(specialist_specs, specialist_results, strict=True):
            if isinstance(result, BaseException):
                await _record_step(
                    extraction_id,
                    trace,
                    agent_name=name,
                    stage="retrieve_and_extract",
                    status="failed",
                    input_summary={"fields": list(field_names)},
                    error=f"{type(result).__name__}: {result}"[:1000],
                )
                for field_name in field_names:
                    fields[field_name] = FieldResult(
                        notes="Specialist branch failed; validator retry will attempt recovery"
                    )
                continue
            fields.update(result.fields)
            evidence.update(result.evidence)
            total_usage = add_usage(total_usage, result.token_usage)

        reconcile_started = perf_counter()
        changes, reconcile_usage, addendum_evidence = await _reconcile_addenda(
            bid_id=bid_id,
            knowledge_base_id=knowledge_base_id,
            fields=fields,
            model=model,
        )
        evidence.update(addendum_evidence)
        total_usage = add_usage(total_usage, reconcile_usage)
        await _record_step(
            extraction_id,
            trace,
            agent_name="addendum_reconciliation",
            stage="reconcile",
            status="completed",
            output_summary={"changes": len(changes)},
            token_usage=reconcile_usage,
            latency_ms=(perf_counter() - reconcile_started) * 1000,
        )

        _enforce_field_ownership(fields)

        validation = _validate_fields(fields)
        for attempt in range(1, settings.max_field_validation_retries + 1):
            retry_fields = [
                issue.field for issue in validation.issues if issue.field != "Bid Summary"
            ]
            if not retry_fields:
                break
            retry_started = perf_counter()
            retry_result = await extract_specialist_fields(
                agent_name="validator_retry_extractor",
                fields=retry_fields,
                definitions=FIELD_DEFINITIONS,
                bid_key=bid_key,
                bid_id=bid_id,
                knowledge_base_id=knowledge_base_id,
                services=services,
                model=model,
            )
            fields.update(retry_result.fields)
            evidence.update(retry_result.evidence)
            total_usage = add_usage(total_usage, retry_result.token_usage)
            validation = _validate_fields(fields)
            await _record_step(
                extraction_id,
                trace,
                agent_name="validator_critic",
                stage="field_retry",
                status="completed",
                attempt=attempt,
                input_summary={"fields": retry_fields},
                output_summary={"remaining_failures": validation.failed},
                token_usage=retry_result.token_usage,
                latency_ms=(perf_counter() - retry_started) * 1000,
            )

        summary_started = perf_counter()
        summary, summary_usage = await _generate_summary(fields, model)
        fields["Bid Summary"] = summary
        total_usage = add_usage(total_usage, summary_usage)
        validation = _validate_fields(fields)
        await _record_step(
            extraction_id,
            trace,
            agent_name="qa_report",
            stage="assemble",
            status="completed",
            output_summary={
                "passed": validation.passed,
                "failed": validation.failed,
                "not_found": validation.not_found,
            },
            token_usage=summary_usage,
            latency_ms=(perf_counter() - summary_started) * 1000,
        )

        completed_at = datetime.now(UTC)
        total_ms = round((perf_counter() - started_clock) * 1000, 3)
        record = BidRecord(
            bid_id=bid_key,
            fields={name: fields.get(name, FieldResult()) for name in REQUIRED_FIELDS},
            addendum_changes=changes,
            validation=validation,
            run_metadata=RunMetadata(
                run_id=str(extraction_id),
                knowledge_base_id=knowledge_base_id,
                model=settings.advanced_model,
                started_at=started_at.isoformat(),
                completed_at=completed_at.isoformat(),
                token_usage=total_usage.model_dump(),
                latency_ms={"total_ms": total_ms},
            ),
        )
        output_dir = settings.bid_output_path.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = output_dir / f"{bid_key}.json"
        artifact_path.write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        trace_dir = output_dir.parent / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_path = trace_dir / f"{bid_key}-{extraction_id}-trace.json"
        trace_path.write_text(
            json.dumps(
                {
                    "run_id": str(extraction_id),
                    "bid_id": bid_key,
                    "generated_at": completed_at.isoformat(),
                    "steps": trace,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        async with get_session_factory()() as session:
            run = await session.get(ExtractionRun, extraction_id)
            bid = await session.get(Bid, run.bid_id) if run else None
            if not run:
                raise ValueError("Extraction run disappeared")
            run.status = "completed" if validation.failed == 0 else "completed_with_issues"
            run.output_json = record.model_dump(mode="json")
            run.validation_json = validation.model_dump(mode="json")
            run.trace_json = trace
            run.token_usage = total_usage.model_dump()
            run.latency_ms = {"total_ms": total_ms}
            run.artifact_path = str(artifact_path)
            if bid:
                bid.status = "ready" if validation.failed == 0 else "needs_review"
            await session.commit()
    except Exception as exc:
        logger.exception(
            "rfp_extraction_failed",
            extraction_id=str(extraction_id),
            error_type=type(exc).__name__,
        )
        async with get_session_factory()() as session:
            run = await session.get(ExtractionRun, extraction_id)
            if run:
                run.status = "failed"
                run.error = f"{type(exc).__name__}: {exc}"[:2000]
                run.trace_json = trace
                bid = await session.get(Bid, run.bid_id)
                if bid:
                    bid.status = "failed"
                await session.commit()
        raise
    finally:
        if model:
            await model.close()
