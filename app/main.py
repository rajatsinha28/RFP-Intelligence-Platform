from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

from app import __version__
from app.api.bids import extraction_router, search_router
from app.api.bids import router as bids_router
from app.api.documents import router as documents_router
from app.api.evaluation import router as evaluation_router
from app.api.health import router as health_router
from app.api.query import router as query_router
from app.config import get_settings
from app.observability.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    logger.info("application_started", version=__version__, environment=settings.environment)
    yield
    from app.services import get_services

    if get_services.cache_info().currsize:
        try:
            await get_services().close()
        except Exception as exc:
            logger.warning("cache_close_failed", error_type=type(exc).__name__)
    logger.info("application_stopped")


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "X-Request-ID"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))[:80]
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response


app.include_router(health_router)
app.include_router(documents_router, prefix="/api/v1")
app.include_router(query_router, prefix="/api/v1")
app.include_router(evaluation_router, prefix="/api/v1")
app.include_router(bids_router, prefix="/api/v1")
app.include_router(search_router, prefix="/api/v1")
app.include_router(extraction_router, prefix="/api/v1")

static_dir = Path(__file__).parent / "static"
assets_dir = static_dir / "assets"
if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str) -> FileResponse:
    index = static_dir / "index.html"
    if not index.exists():
        raise RuntimeError("Frontend assets are not built; run the Vite build or Docker Compose")
    return FileResponse(index)
