"""AIAP Meeting Protocol Service — FastAPI application."""

import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.models.schemas import HealthDetailResponse, HealthResponse, ServiceStatus
from app.routers import meetings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="AIAP Meeting Protocol Service",
    description=(
        "Сервис для автоматической расшифровки аудиозаписей встреч, "
        "генерации саммари, списка задач и PDF-отчётов."
    ),
    version="1.0.0",
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(meetings.router)


@app.on_event("startup")
async def startup():
    os.makedirs(settings.upload_dir, exist_ok=True)
    logger.info("AIAP Meeting Protocol Service started on %s:%s", settings.app_host, settings.app_port)


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = BASE_DIR / "templates" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok", version="1.0.0")


@app.get("/health/details", response_model=HealthDetailResponse)
async def health_details():
    """Check connectivity to all external services."""
    services = []

    # S3
    services.append(_check_s3())

    # Yandex STT
    services.append(_check_yandex())

    # Claude / Anthropic
    services.append(_check_anthropic())

    overall = "ok" if all(s.status == "ok" for s in services) else "degraded"
    return HealthDetailResponse(status=overall, version="1.0.0", services=services)


def _check_s3() -> ServiceStatus:
    if not settings.s3_access_key or not settings.s3_bucket_name:
        return ServiceStatus(name="S3 Storage", status="unconfigured", message="S3 credentials not set")
    try:
        import boto3
        from botocore.config import Config

        t0 = time.monotonic()
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=Config(signature_version="s3v4", connect_timeout=5, read_timeout=5),
        )
        client.head_bucket(Bucket=settings.s3_bucket_name)
        latency = round((time.monotonic() - t0) * 1000, 1)
        return ServiceStatus(name="S3 Storage", status="ok", message=f"Bucket '{settings.s3_bucket_name}' accessible", latency_ms=latency)
    except Exception as e:
        return ServiceStatus(name="S3 Storage", status="error", message=str(e))


def _check_yandex() -> ServiceStatus:
    if not settings.yandex_api_key or not settings.yandex_folder_id:
        return ServiceStatus(name="Yandex STT", status="unconfigured", message="Yandex API key or folder ID not set")
    try:
        import httpx

        t0 = time.monotonic()
        # Send a minimal recognize request — valid auth returns 400 (bad audio), invalid returns 401/403
        with httpx.Client(timeout=5) as client:
            resp = client.post(
                "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize",
                params={"folderId": settings.yandex_folder_id},
                headers={
                    "Authorization": f"Api-Key {settings.yandex_api_key}",
                    "Content-Type": "application/octet-stream",
                },
                content=b"",
            )
        latency = round((time.monotonic() - t0) * 1000, 1)
        # 401/403 = bad credentials, anything else = service reachable & auth ok
        if resp.status_code in (401, 403):
            return ServiceStatus(name="Yandex STT", status="error", message="Invalid API key or folder ID")
        return ServiceStatus(name="Yandex STT", status="ok", message="API key valid, service reachable", latency_ms=latency)
    except Exception as e:
        return ServiceStatus(name="Yandex STT", status="error", message=str(e))


def _check_anthropic() -> ServiceStatus:
    if not settings.anthropic_api_key:
        return ServiceStatus(name="Claude AI", status="unconfigured", message="Anthropic API key not set")
    try:
        import anthropic

        t0 = time.monotonic()
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        # Minimal API call to validate the key
        client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
        latency = round((time.monotonic() - t0) * 1000, 1)
        return ServiceStatus(name="Claude AI", status="ok", message="API key valid, model accessible", latency_ms=latency)
    except anthropic.AuthenticationError:
        return ServiceStatus(name="Claude AI", status="error", message="Invalid API key")
    except Exception as e:
        return ServiceStatus(name="Claude AI", status="error", message=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )
