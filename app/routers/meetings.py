"""API router for meeting audio processing."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Query
from fastapi.responses import RedirectResponse

from app.config import settings
from app.models.schemas import JobResponse, JobResult, JobStatus
from app.services.pipeline import jobs, process_audio
from app.services.s3_service import s3_service

router = APIRouter(prefix="/api/v1/meetings", tags=["meetings"])

executor = ThreadPoolExecutor(max_workers=4)

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".opus", ".webm"}


@router.post("/transcribe", response_model=JobResponse)
async def transcribe_audio(
    file: UploadFile = File(..., description="Аудиофайл встречи"),
    language: str = Query("ru-RU", description="Язык распознавания (ru-RU, en-US и т.д.)"),
):
    """
    Загрузить аудиофайл для расшифровки и анализа.

    Процесс:
    1. Аудио загружается в S3
    2. Yandex STT создаёт расшифровку
    3. Claude Sonnet создаёт саммари, список задач и чистую расшифровку
    4. Генерируются TXT и PDF файлы
    5. Все файлы загружаются в S3

    Возвращает job_id для отслеживания статуса.
    """
    # Validate file extension
    ext = Path(file.filename or "audio.ogg").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Неподдерживаемый формат файла: {ext}. Допустимые: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # Validate file size
    max_size = settings.max_audio_size_mb * 1024 * 1024
    content = await file.read()
    if len(content) > max_size:
        raise HTTPException(
            status_code=413,
            detail=f"Файл слишком большой. Максимум: {settings.max_audio_size_mb} MB",
        )

    # Save to temp file
    job_id = str(uuid.uuid4())
    upload_dir = Path(settings.upload_dir) / job_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    audio_path = str(upload_dir / f"audio{ext}")
    with open(audio_path, "wb") as f:
        f.write(content)

    # Initialize job
    jobs[job_id] = {
        "status": JobStatus.PENDING,
        "message": "Задача создана, ожидает обработки",
        "original_filename": file.filename,
    }

    # Process in background thread
    executor.submit(process_audio, job_id, audio_path, file.filename or f"audio{ext}", language)

    return JobResponse(job_id=job_id, status=JobStatus.PENDING, message="Задача создана, обработка начата")


@router.get("/status/{job_id}", response_model=JobResult)
async def get_job_status(job_id: str):
    """Получить статус и результаты обработки."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    job = jobs[job_id]
    return _job_to_result(job_id, job)


@router.get("/jobs", response_model=list[JobResult])
async def list_jobs():
    """Получить список всех задач."""
    return [_job_to_result(job_id, job) for job_id, job in jobs.items()]


VALID_FILE_TYPES = {"transcript", "summary_txt", "summary_pdf", "tasks"}


@router.get("/download/{job_id}/{file_type}")
async def download_file(job_id: str, file_type: str):
    """Generate a presigned S3 URL and redirect to it for download."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if file_type not in VALID_FILE_TYPES:
        raise HTTPException(status_code=400, detail=f"Неверный тип файла: {file_type}")

    s3_keys = jobs[job_id].get("s3_keys", {})
    s3_key = s3_keys.get(file_type)
    if not s3_key:
        raise HTTPException(status_code=404, detail="Файл ещё не готов")

    presigned_url = s3_service.generate_presigned_url(s3_key, expires_in=3600)
    return RedirectResponse(url=presigned_url, status_code=302)


def _job_to_result(job_id: str, job: dict) -> JobResult:
    base = f"/api/v1/meetings/download/{job_id}"
    s3_keys = job.get("s3_keys", {})

    return JobResult(
        job_id=job_id,
        status=job["status"],
        message=job.get("message"),
        transcript_url=f"{base}/transcript" if "transcript" in s3_keys else None,
        summary_txt_url=f"{base}/summary_txt" if "summary_txt" in s3_keys else None,
        summary_pdf_url=f"{base}/summary_pdf" if "summary_pdf" in s3_keys else None,
        tasks_url=f"{base}/tasks" if "tasks" in s3_keys else None,
        error=job.get("error"),
        logs=job.get("logs", []),
        stage_started_at=job.get("stage_started_at"),
    )
