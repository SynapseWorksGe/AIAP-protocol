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

    def transcribe_long_audio(self, audio_path: str, language_code: str = "ru-RU") -> str:
        """Transcribe a large audio file by splitting into chunks."""
        audio = AudioSegment.from_file(audio_path)
        total_ms = len(audio)

        if total_ms <= CHUNK_DURATION_MS:
            return self._recognize_file(audio_path, language_code)

        logger.info(
            "Audio duration %.1f min – splitting into %.0f chunks",
            total_ms / 60_000,
            -(-total_ms // CHUNK_DURATION_MS),  # ceil division
        )

        transcripts: list[str] = []
        chunk_index = 0

        for start_ms in range(0, total_ms, CHUNK_DURATION_MS):
            chunk = audio[start_ms : start_ms + CHUNK_DURATION_MS]
            chunk_index += 1

            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                chunk_path = tmp.name
                chunk.export(chunk_path, format="ogg", codec="libopus")

            try:
                logger.info(
                    "Transcribing chunk %d (%.1f–%.1f min)",
                    chunk_index,
                    start_ms / 60_000,
                    min(start_ms + CHUNK_DURATION_MS, total_ms) / 60_000,
                )
                text = self._recognize_file(chunk_path, language_code)
                if text.strip():
                    transcripts.append(text)
            finally:
                os.unlink(chunk_path)

        return "\n".join(transcripts)

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

    def _recognize_file(self, file_path: str, language_code: str) -> str:
        """Send a single file to longRunningRecognize as base64 content."""
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

        logger.info("Sending %s to longRunningRecognize (%.1f MB)", file_path, len(audio_content) / 1_048_576)

        with httpx.Client(timeout=120) as client:
            resp = client.post(RECOGNIZE_LONG_URL, json=body, headers=self._headers())
            resp.raise_for_status()
            operation = resp.json()

        operation_id = operation["id"]
        logger.info("Operation started: %s", operation_id)

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
