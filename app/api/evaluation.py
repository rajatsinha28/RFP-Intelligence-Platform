from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import enforce_api_key, enforce_rate_limit
from app.db import get_session
from app.models import EvaluationRun
from app.schemas import EvaluationRead, EvaluationRequest
from app.worker import run_evaluation_task

router = APIRouter(
    prefix="/evaluation",
    tags=["evaluation"],
    dependencies=[Depends(enforce_api_key), Depends(enforce_rate_limit)],
)


def _validated_dataset_path(value: str) -> tuple[Path, Path] | None:
    path = Path(value).resolve()
    project = Path.cwd().resolve()
    if project not in path.parents or not path.is_file():
        return None
    return path, project


@router.post("/run", response_model=EvaluationRead, status_code=status.HTTP_202_ACCEPTED)
async def start_evaluation(
    request: EvaluationRequest, session: AsyncSession = Depends(get_session)
) -> EvaluationRun:
    validated = await asyncio.to_thread(_validated_dataset_path, request.dataset_path)
    if validated is None:
        raise HTTPException(status_code=400, detail="Evaluation dataset path is invalid")
    path, project = validated
    run = EvaluationRun(
        status="queued",
        dataset_name=str(path.relative_to(project)),
        knowledge_base_id=request.knowledge_base_id,
        metrics={},
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    try:
        run_evaluation_task.delay(str(run.id))
    except Exception as exc:
        run.status = "failed"
        run.error = "Evaluation worker is unavailable"
        await session.commit()
        raise HTTPException(status_code=503, detail=run.error) from exc
    return run


@router.get("/{evaluation_id}", response_model=EvaluationRead)
async def get_evaluation(
    evaluation_id: UUID, session: AsyncSession = Depends(get_session)
) -> EvaluationRun:
    run = await session.get(EvaluationRun, evaluation_id)
    if not run:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    return run
