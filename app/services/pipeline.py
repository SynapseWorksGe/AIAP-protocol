"""Main processing pipeline: audio -> transcription -> analysis -> S3."""

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.models.schemas import JobStatus
from app.services.claude_service import claude_service
from app.services.pdf_service import pdf_service
from app.services.s3_service import s3_service
from app.services.yandex_stt_service import yandex_stt_service

logger = logging.getLogger(__name__)

# In-memory job store (for production consider Redis / DB)
jobs: dict[str, dict] = {}


def _s3_key(job_id: str, filename: str) -> str:
    date_prefix = datetime.utcnow().strftime("%Y/%m/%d")
    return f"meetings/{date_prefix}/{job_id}/{filename}"


def _fmt_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1_048_576:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / 1_048_576:.1f} MB"


def _log(job_id: str, message: str):
    """Append a timestamped log entry to the job."""
    ts = datetime.utcnow().strftime("%H:%M:%S")
    entry = f"[{ts}] {message}"
    jobs[job_id].setdefault("logs", []).append(entry)
    logger.info("Job %s: %s", job_id, message)


def process_audio(job_id: str, audio_path: str, original_filename: str, language: str = "ru-RU"):
    """Full pipeline executed in a background thread."""
    try:
        jobs[job_id].setdefault("logs", [])
        file_size = os.path.getsize(audio_path)
        _log(job_id, f"Получен файл: {original_filename} ({_fmt_size(file_size)})")

        # 1. Upload original audio to S3
        _update_status(job_id, JobStatus.TRANSCRIBING, "Загрузка аудио в S3...")
        audio_s3_key = _s3_key(job_id, original_filename)
        _log(job_id, f"Загрузка аудио в S3: {audio_s3_key}")
        audio_url = s3_service.upload_file(audio_path, audio_s3_key)
        jobs[job_id]["audio_url"] = audio_url
        _log(job_id, f"Аудио загружено в S3: {audio_url}")

        # 2. Transcribe via Yandex STT
        if file_size < 1_000_000:
            _log(job_id, f"Файл < 1 MB — используется короткое распознавание (Yandex STT short)")
            _update_status(job_id, JobStatus.TRANSCRIBING, "Распознавание речи (короткое аудио)...")
            with open(audio_path, "rb") as f:
                raw_transcript = yandex_stt_service.transcribe_from_bytes(f.read(), language)
            _log(job_id, f"Распознавание завершено, получено {len(raw_transcript)} символов")
        else:
            _log(job_id, f"Файл >= 1 MB — используется longRunningRecognize (Yandex STT)")
            _update_status(job_id, JobStatus.TRANSCRIBING, "Распознавание речи (Yandex STT)...")
            raw_transcript = yandex_stt_service.transcribe_long_audio(
                audio_path, language, on_log=lambda msg: _log(job_id, msg)
            )
            _log(job_id, f"Распознавание завершено, получено {len(raw_transcript)} символов")

        if not raw_transcript.strip():
            raise ValueError("Yandex STT returned empty transcript")

        # 3. Analyze with Claude
        _update_status(job_id, JobStatus.ANALYZING, "Анализ расшифровки с помощью Claude...")
        _log(job_id, "Отправка расшифровки в Claude для анализа...")
        analysis = claude_service.full_analysis(raw_transcript)
        _log(job_id, f"Анализ завершён: саммари {len(analysis['summary'])} симв., задачи {len(analysis['tasks'])} симв.")

        # 4. Generate files
        _update_status(job_id, JobStatus.GENERATING_FILES, "Генерация файлов...")

        tmp_dir = Path(settings.upload_dir) / job_id
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # Save transcript TXT
        transcript_path = str(tmp_dir / "transcript.txt")
        with open(transcript_path, "w", encoding="utf-8") as f:
            f.write(analysis["transcript"])
        _log(job_id, "Сохранён файл: transcript.txt")

        # Save summary TXT
        summary_txt_path = str(tmp_dir / "summary.txt")
        with open(summary_txt_path, "w", encoding="utf-8") as f:
            f.write(analysis["summary"])
            f.write("\n\n" + "=" * 60 + "\n\n")
            f.write("СПИСОК ЗАДАЧ\n\n")
            f.write(analysis["tasks"])
        _log(job_id, "Сохранён файл: summary.txt")

        # Save tasks TXT
        tasks_txt_path = str(tmp_dir / "tasks.txt")
        with open(tasks_txt_path, "w", encoding="utf-8") as f:
            f.write(analysis["tasks"])
        _log(job_id, "Сохранён файл: tasks.txt")

        # Generate PDF
        pdf_path = str(tmp_dir / "meeting_report.pdf")
        _log(job_id, "Генерация PDF-отчёта...")
        pdf_service.generate_summary_pdf(
            summary=analysis["summary"],
            tasks=analysis["tasks"],
            transcript=analysis["transcript"],
            output_path=pdf_path,
        )
        _log(job_id, "Сохранён файл: meeting_report.pdf")

        # 5. Upload all to S3
        _update_status(job_id, JobStatus.UPLOADING, "Загрузка результатов в S3...")

        _log(job_id, "Загрузка transcript.txt в S3...")
        transcript_url = s3_service.upload_file(transcript_path, _s3_key(job_id, "transcript.txt"), "text/plain; charset=utf-8")

        _log(job_id, "Загрузка summary.txt в S3...")
        summary_txt_url = s3_service.upload_file(summary_txt_path, _s3_key(job_id, "summary.txt"), "text/plain; charset=utf-8")

        _log(job_id, "Загрузка tasks.txt в S3...")
        tasks_url = s3_service.upload_file(tasks_txt_path, _s3_key(job_id, "tasks.txt"), "text/plain; charset=utf-8")

        _log(job_id, "Загрузка meeting_report.pdf в S3...")
        summary_pdf_url = s3_service.upload_file(pdf_path, _s3_key(job_id, "meeting_report.pdf"), "application/pdf")

        _log(job_id, "Все файлы загружены в S3")

        # 6. Done
        jobs[job_id].update(
            {
                "status": JobStatus.COMPLETED,
                "message": "Обработка завершена",
                "transcript_url": transcript_url,
                "summary_txt_url": summary_txt_url,
                "summary_pdf_url": summary_pdf_url,
                "tasks_url": tasks_url,
            }
        )
        _log(job_id, "Обработка завершена успешно")

    except Exception as e:
        logger.exception("Job %s failed", job_id)
        _log(job_id, f"ОШИБКА: {e}")
        jobs[job_id].update({"status": JobStatus.FAILED, "message": str(e), "error": str(e)})

    finally:
        # Cleanup local temp files
        tmp_dir = Path(settings.upload_dir) / job_id
        if tmp_dir.exists():
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        if os.path.exists(audio_path):
            os.remove(audio_path)
        _log(job_id, "Временные файлы удалены")


def _update_status(job_id: str, status: JobStatus, message: str):
    jobs[job_id]["status"] = status
    jobs[job_id]["message"] = message
    logger.info("Job %s: %s - %s", job_id, status.value, message)
