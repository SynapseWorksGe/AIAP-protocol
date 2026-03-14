"""Yandex Speech-To-Text service using the REST API (long audio recognition)."""

import base64
import logging
import os
import tempfile
import time

import httpx
from pydub import AudioSegment

from app.config import settings

logger = logging.getLogger(__name__)

RECOGNIZE_LONG_URL = "https://transcribe.api.cloud.yandex.net/speech/stt/v2/longRunningRecognize"
OPERATIONS_URL = "https://operation.api.cloud.yandex.net/operations"

# 15 minutes per chunk – keeps base64 payload well under API limits
CHUNK_DURATION_MS = 15 * 60 * 1000

# Delay between submitting chunk operations to avoid 429
SUBMIT_DELAY_SEC = 3

# Retry settings for 429 responses
MAX_RETRIES = 5
RETRY_BACKOFF_BASE = 5  # seconds


class YandexSTTService:
    """Transcribe audio using Yandex SpeechKit long-running recognition."""

    def __init__(self):
        self._api_key = settings.yandex_api_key
        self._folder_id = settings.yandex_folder_id

    def _headers(self) -> dict:
        return {"Authorization": f"Api-Key {self._api_key}"}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def transcribe_long_audio(self, audio_path: str, language_code: str = "ru-RU", on_log=None) -> str:
        """Transcribe a large audio file by splitting into chunks.

        Strategy: submit all chunks first (collecting operation IDs),
        then poll all operations until done, concatenate results.

        Args:
            on_log: Optional callback ``fn(message: str)`` for progress updates.
        """
        def _log(msg):
            logger.info(msg)
            if on_log:
                on_log(msg)
        audio = AudioSegment.from_file(audio_path)
        total_ms = len(audio)

        if total_ms <= CHUNK_DURATION_MS:
            _log(f"Длительность {total_ms / 60_000:.1f} мин — один чанк")
            return self._recognize_file(audio_path, language_code)

        num_chunks = -(-total_ms // CHUNK_DURATION_MS)
        _log(f"Длительность {total_ms / 60_000:.1f} мин — разбивка на {num_chunks} чанков по 15 мин")

        # Phase 1: submit all chunks, collect operation IDs
        operations: list[tuple[int, str]] = []  # (chunk_index, operation_id)

        for i, start_ms in enumerate(range(0, total_ms, CHUNK_DURATION_MS)):
            chunk = audio[start_ms : start_ms + CHUNK_DURATION_MS]
            chunk_index = i + 1

            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                chunk_path = tmp.name
                chunk.export(chunk_path, format="ogg", codec="libopus")

            try:
                t_from = start_ms / 60_000
                t_to = min(start_ms + CHUNK_DURATION_MS, total_ms) / 60_000
                _log(f"Отправка чанка {chunk_index}/{num_chunks} ({t_from:.1f}–{t_to:.1f} мин) в Yandex STT...")
                operation_id = self._submit_recognize(chunk_path, language_code)
                operations.append((chunk_index, operation_id))
                _log(f"Чанк {chunk_index}/{num_chunks} принят, операция {operation_id[:12]}...")

                # Delay before next submit to avoid 429
                if chunk_index < num_chunks:
                    time.sleep(SUBMIT_DELAY_SEC)
            finally:
                os.unlink(chunk_path)

        # Phase 2: poll all operations until complete
        _log(f"Все {len(operations)} чанков отправлены, ожидание результатов...")
        results: dict[int, str] = {}

        pending = dict(operations)  # {chunk_index: operation_id}
        elapsed = 0
        max_wait = 1800

        with httpx.Client(timeout=30) as client:
            while pending and elapsed < max_wait:
                time.sleep(5)
                elapsed += 5

                for chunk_idx in list(pending.keys()):
                    op_id = pending[chunk_idx]
                    try:
                        resp = client.get(f"{OPERATIONS_URL}/{op_id}", headers=self._headers())
                        resp.raise_for_status()
                        op = resp.json()

                        if op.get("done"):
                            if "error" in op:
                                raise RuntimeError(f"Yandex STT error (chunk {chunk_idx}): {op['error']}")
                            text = self._extract_transcript(op)
                            results[chunk_idx] = text
                            del pending[chunk_idx]
                            _log(f"Чанк {chunk_idx}/{num_chunks} распознан ({len(text)} символов), осталось {len(pending)}")
                    except httpx.HTTPStatusError:
                        logger.warning("Poll error for chunk %d, will retry", chunk_idx)

        if pending:
            raise TimeoutError(f"Chunks {list(pending.keys())} did not complete within {max_wait}s")

        # Concatenate in order
        transcript_parts = [results[i] for i in sorted(results.keys()) if results[i].strip()]
        return "\n".join(transcript_parts)

    def transcribe_from_bytes(self, audio_data: bytes, language_code: str = "ru-RU") -> str:
        """Transcribe short audio (< 1 MB) using the short recognition API."""
        url = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
        params = {
            "folderId": self._folder_id,
            "lang": language_code,
            "format": "oggopus",
            "sampleRateHertz": 48000,
        }

        with httpx.Client(timeout=60) as client:
            resp = client.post(
                url,
                params=params,
                headers={**self._headers(), "Content-Type": "application/octet-stream"},
                content=audio_data,
            )
            resp.raise_for_status()
            result = resp.json()

        return result.get("result", "")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _submit_recognize(self, file_path: str, language_code: str) -> str:
        """Submit a file to longRunningRecognize and return the operation ID.

        Retries with exponential backoff on 429 Too Many Requests.
        """
        with open(file_path, "rb") as f:
            audio_content = base64.b64encode(f.read()).decode("utf-8")

        body = {
            "config": {
                "specification": {
                    "languageCode": language_code,
                    "model": "general",
                    "profanityFilter": False,
                    "audioEncoding": "OGG_OPUS",
                    "sampleRateHertz": 48000,
                    "audioChannelCount": 1,
                },
                "folderId": self._folder_id,
            },
            "audio": {"content": audio_content},
        }

        logger.info("Sending %s to longRunningRecognize (%.1f MB base64)", file_path, len(audio_content) / 1_048_576)

        for attempt in range(MAX_RETRIES + 1):
            with httpx.Client(timeout=120) as client:
                resp = client.post(RECOGNIZE_LONG_URL, json=body, headers=self._headers())

                if resp.status_code == 429:
                    if attempt == MAX_RETRIES:
                        resp.raise_for_status()
                    wait = RETRY_BACKOFF_BASE * (2 ** attempt)
                    logger.warning("429 Too Many Requests, retrying in %ds (attempt %d/%d)", wait, attempt + 1, MAX_RETRIES)
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                operation = resp.json()
                return operation["id"]

        raise RuntimeError("Unreachable")

    def _recognize_file(self, file_path: str, language_code: str) -> str:
        """Submit + poll a single file (used for audio that fits in one chunk)."""
        operation_id = self._submit_recognize(file_path, language_code)
        return self._poll_operation(operation_id)

    def _poll_operation(self, operation_id: str, poll_interval: int = 5, max_wait: int = 1800) -> str:
        """Poll for operation completion and return the transcript."""
        url = f"{OPERATIONS_URL}/{operation_id}"
        elapsed = 0

        with httpx.Client(timeout=30) as client:
            while elapsed < max_wait:
                resp = client.get(url, headers=self._headers())
                resp.raise_for_status()
                op = resp.json()

                if op.get("done"):
                    if "error" in op:
                        raise RuntimeError(f"Yandex STT error: {op['error']}")
                    return self._extract_transcript(op)

                logger.info("Operation %s in progress, waiting %ds...", operation_id, poll_interval)
                time.sleep(poll_interval)
                elapsed += poll_interval

        raise TimeoutError(f"Operation {operation_id} did not complete within {max_wait}s")

    @staticmethod
    def _extract_transcript(operation: dict) -> str:
        """Extract full text from operation response."""
        chunks = operation.get("response", {}).get("chunks", [])
        lines = []
        for chunk in chunks:
            alternatives = chunk.get("alternatives", [])
            if alternatives:
                lines.append(alternatives[0].get("text", ""))
        return "\n".join(lines)


yandex_stt_service = YandexSTTService()
