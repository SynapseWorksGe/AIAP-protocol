"""Yandex Speech-To-Text service using the REST API (long audio recognition)."""

import base64
import logging
import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

RECOGNIZE_LONG_URL = "https://transcribe.api.cloud.yandex.net/speech/stt/v2/longRunningRecognize"
OPERATIONS_URL = "https://operation.api.cloud.yandex.net/operations"

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
        """Transcribe an audio file via longRunningRecognize (single request, no chunking).

        Args:
            on_log: Optional callback ``fn(message: str)`` for progress updates.
        """
        def _log(msg):
            logger.info(msg)
            if on_log:
                on_log(msg)

        with open(audio_path, "rb") as f:
            raw = f.read()

        size_mb = len(raw) / 1_048_576
        _log(f"Отправка файла в Yandex STT ({size_mb:.1f} MB)...")

        audio_content = base64.b64encode(raw).decode("utf-8")

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

        # Submit with retry on 429
        operation_id = self._submit_with_retry(body, _log)
        _log(f"Операция создана: {operation_id}, ожидание результата...")

        # Poll until done
        return self._poll_operation(operation_id, _log)

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

    def _submit_with_retry(self, body: dict, _log) -> str:
        """Submit request to longRunningRecognize with retry on 429."""
        for attempt in range(MAX_RETRIES + 1):
            with httpx.Client(timeout=300) as client:
                resp = client.post(RECOGNIZE_LONG_URL, json=body, headers=self._headers())

                if resp.status_code == 429:
                    if attempt == MAX_RETRIES:
                        resp.raise_for_status()
                    wait = RETRY_BACKOFF_BASE * (2 ** attempt)
                    _log(f"429 Too Many Requests — повтор через {wait}с (попытка {attempt + 1}/{MAX_RETRIES})")
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                return resp.json()["id"]

        raise RuntimeError("Unreachable")

    def _poll_operation(self, operation_id: str, _log, poll_interval: int = 5, max_wait: int = 1800) -> str:
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
                    text = self._extract_transcript(op)
                    _log(f"Распознавание завершено ({len(text)} символов)")
                    return text

                if elapsed % 30 == 0 and elapsed > 0:
                    _log(f"Ожидание распознавания... ({elapsed}с)")
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
