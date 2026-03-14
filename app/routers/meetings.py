"""API router for meeting audio processing."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Query

from app.config import settings
from app.models.schemas import JobResponse, JobResult, JobStatus
from app.services.pipeline import jobs, process_audio

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
    return JobResult(
        job_id=job_id,
        status=job["status"],
        transcript_url=job.get("transcript_url"),
        summary_txt_url=job.get("summary_txt_url"),
        summary_pdf_url=job.get("summary_pdf_url"),
        tasks_url=job.get("tasks_url"),
        error=job.get("error"),
    )


@router.get("/jobs", response_model=list[JobResult])
async def list_jobs():
    """Получить список всех задач."""
    results = []
    for job_id, job in jobs.items():
        results.append(
            JobResult(
                job_id=job_id,
                status=job["status"],
                transcript_url=job.get("transcript_url"),
                summary_txt_url=job.get("summary_txt_url"),
                summary_pdf_url=job.get("summary_pdf_url"),
                tasks_url=job.get("tasks_url"),
                error=job.get("error"),
            )
        )
    return results
