"""AIAP Meeting Protocol Service — FastAPI application."""

import logging
import os

from fastapi import FastAPI

from app.config import settings
from app.models.schemas import HealthResponse
from app.routers import meetings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="AIAP Meeting Protocol Service",
    description=(
        "Сервис для автоматической расшифровки аудиозаписей встреч, "
        "генерации саммари, списка задач и PDF-отчётов."
    ),
    version="1.0.0",
)

app.include_router(meetings.router)


@app.on_event("startup")
async def startup():
    os.makedirs(settings.upload_dir, exist_ok=True)
    logger.info("AIAP Meeting Protocol Service started on %s:%s", settings.app_host, settings.app_port)


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok", version="1.0.0")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )
