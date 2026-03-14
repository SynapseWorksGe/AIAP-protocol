"""Main processing pipeline: audio -> transcription -> analysis -> S3."""

import logging
import os
import subprocess
import time
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


def _start_stage_timer(job_id: str):
    """Record when the current stage started."""
    jobs[job_id]["stage_started_at"] = time.time()


COMPRESS_THRESHOLD_MB = 10  # Compress audio files larger than this (MB)


def _compress_audio(audio_path: str, job_id: str) -> str | None:
    """Compress audio to low-bitrate OGG/Opus for STT (24 kbps, 16 kHz, mono).

    Returns path to compressed file, or None if compression failed/not needed.
    """
    out_path = audio_path + ".compressed.ogg"
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", audio_path,
                "-c:a", "libopus", "-b:a", "24k",
                "-ac", "1", "-ar", "16000",
                "-application", "voip",
                out_path,
            ],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            logger.warning("ffmpeg compression failed: %s", result.stderr[:500])
            return None
        return out_path
    except Exception as e:
        logger.warning("Audio compression error: %s", e)
        return None


def process_audio(job_id: str, audio_path: str, original_filename: str, language: str = "ru-RU"):
    """Full pipeline executed in a background thread."""
    compressed_path = None
    try:
        jobs[job_id].setdefault("logs", [])
        file_size = os.path.getsize(audio_path)
        _log(job_id, f"Получен файл: {original_filename} ({_fmt_size(file_size)})")

        # 1. Upload original audio to S3
        _update_status(job_id, JobStatus.PENDING, "Загрузка аудио в S3...")
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

            # Compress large audio to reduce payload size (39 MB → ~3-4 MB)
            stt_path = audio_path
            size_mb = file_size / 1_048_576
            if size_mb >= COMPRESS_THRESHOLD_MB:
                _update_status(job_id, JobStatus.COMPRESSING, "Сжатие аудио...")
                _log(job_id, f"Сжатие аудио ({size_mb:.1f} MB) для Yandex STT (24kbps OGG/Opus)...")
                compressed_path = _compress_audio(audio_path, job_id)
                if compressed_path:
                    comp_size = os.path.getsize(compressed_path) / 1_048_576
                    _log(job_id, f"Аудио сжато: {size_mb:.1f} MB → {comp_size:.1f} MB")
                    stt_path = compressed_path
                else:
                    _log(job_id, "Сжатие не удалось, отправка оригинального файла")

            _update_status(job_id, JobStatus.TRANSCRIBING, "Распознавание речи (Yandex STT)...")
            stt_sample_rate = 16000 if compressed_path else 48000
            raw_transcript = yandex_stt_service.transcribe_long_audio(
                stt_path, language,
                on_log=lambda msg: _log(job_id, msg),
                sample_rate=stt_sample_rate,
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

        transcript_s3_key = _s3_key(job_id, "transcript.txt")
        summary_s3_key = _s3_key(job_id, "summary.txt")
        tasks_s3_key = _s3_key(job_id, "tasks.txt")
        pdf_s3_key = _s3_key(job_id, "meeting_report.pdf")

        _log(job_id, "Загрузка transcript.txt в S3...")
        s3_service.upload_file(transcript_path, transcript_s3_key, "text/plain; charset=utf-8")

        _log(job_id, "Загрузка summary.txt в S3...")
        s3_service.upload_file(summary_txt_path, summary_s3_key, "text/plain; charset=utf-8")

        _log(job_id, "Загрузка tasks.txt в S3...")
        s3_service.upload_file(tasks_txt_path, tasks_s3_key, "text/plain; charset=utf-8")

        _log(job_id, "Загрузка meeting_report.pdf в S3...")
        s3_service.upload_file(pdf_path, pdf_s3_key, "application/pdf")

        _log(job_id, "Все файлы загружены в S3")

        # 6. Done — store S3 keys (download endpoint generates presigned URLs)
        jobs[job_id].update(
            {
                "status": JobStatus.COMPLETED,
                "message": "Обработка завершена",
                "s3_keys": {
                    "transcript": transcript_s3_key,
                    "summary_txt": summary_s3_key,
                    "summary_pdf": pdf_s3_key,
                    "tasks": tasks_s3_key,
                },
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
        if compressed_path and os.path.exists(compressed_path):
            os.remove(compressed_path)
        _log(job_id, "Временные файлы удалены")


def _update_status(job_id: str, status: JobStatus, message: str):
    jobs[job_id]["status"] = status
    jobs[job_id]["message"] = message
    _start_stage_timer(job_id)
    logger.info("Job %s: %s - %s", job_id, status.value, message)
