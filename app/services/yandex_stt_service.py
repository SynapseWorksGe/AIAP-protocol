"""Yandex Speech-To-Text service using the REST API (long audio recognition)."""

import base64
import logging
import time
import uuid

import boto3
import httpx
from botocore.config import Config

from app.config import settings

logger = logging.getLogger(__name__)

RECOGNIZE_LONG_URL = "https://transcribe.api.cloud.yandex.net/speech/stt/v2/longRunningRecognize"
OPERATIONS_URL = "https://operation.api.cloud.yandex.net/operations"
YC_S3_ENDPOINT = "https://storage.yandexcloud.net"

# Retry settings for 429 responses
MAX_RETRIES = 5
RETRY_BACKOFF_BASE = 5  # seconds

# Files larger than this (in MB, after compression) use Object Storage URI
URI_THRESHOLD_MB = 10


class YandexSTTService:
    """Transcribe audio using Yandex SpeechKit long-running recognition."""

    def __init__(self):
        self._api_key = settings.yandex_api_key
        self._folder_id = settings.yandex_folder_id
        self._yc_s3 = None

    def _headers(self) -> dict:
        return {"Authorization": f"Api-Key {self._api_key}"}

    def _get_yc_s3(self):
        """Lazy-init Yandex Object Storage client."""
        if self._yc_s3 is None:
            self._yc_s3 = boto3.client(
                "s3",
                endpoint_url=YC_S3_ENDPOINT,
                region_name="ru-central1",
                aws_access_key_id=settings.yc_s3_access_key,
                aws_secret_access_key=settings.yc_s3_secret_key,
                config=Config(signature_version="s3v4"),
            )
        return self._yc_s3

    @staticmethod
    def yc_s3_configured() -> bool:
        """Check if Yandex Object Storage credentials are set."""
        return bool(settings.yc_s3_access_key and settings.yc_s3_secret_key and settings.yc_s3_bucket)

    # ------------------------------------------------------------------
    # Yandex Object Storage helpers
    # ------------------------------------------------------------------

    def upload_to_yc_s3(self, local_path: str, _log) -> str:
        """Upload file to Yandex Object Storage and return the S3 URI."""
        s3_key = f"stt-tmp/{uuid.uuid4().hex}.ogg"
        bucket = settings.yc_s3_bucket
        _log(f"Загрузка аудио в Yandex Object Storage ({bucket}/{s3_key})...")
        self._get_yc_s3().upload_file(local_path, bucket, s3_key)
        uri = f"https://{bucket}.storage.yandexcloud.net/{s3_key}"
        _log(f"Файл загружен: {uri}")
        return uri, s3_key

    def delete_from_yc_s3(self, s3_key: str):
        """Delete temporary file from Yandex Object Storage."""
        try:
            self._get_yc_s3().delete_object(Bucket=settings.yc_s3_bucket, Key=s3_key)
            logger.info("Deleted from YC S3: %s", s3_key)
        except Exception as e:
            logger.warning("Failed to delete from YC S3: %s", e)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def transcribe_long_audio(
        self,
        audio_path: str,
        language_code: str = "ru-RU",
        on_log=None,
        sample_rate: int = 48000,
    ) -> str:
        """Transcribe an audio file via longRunningRecognize.

        For large files (> URI_THRESHOLD_MB) with Yandex Object Storage configured,
        uploads to Object Storage and uses URI mode. Otherwise sends inline base64.

        Args:
            on_log: Optional callback ``fn(message: str)`` for progress updates.
            sample_rate: Audio sample rate in Hz (default 48000, use 16000 for compressed audio).
        """
        def _log(msg):
            logger.info(msg)
            if on_log:
                on_log(msg)

        import os
        file_size_mb = os.path.getsize(audio_path) / 1_048_576
        s3_ok = self.yc_s3_configured()
        use_uri = file_size_mb >= URI_THRESHOLD_MB and s3_ok
        _log(f"Размер файла: {file_size_mb:.1f} MB, порог URI: {URI_THRESHOLD_MB} MB, "
             f"YC S3 настроен: {s3_ok} → режим: {'URI' if use_uri else 'inline'}")

        spec = {
            "languageCode": language_code,
            "model": "general",
            "profanityFilter": False,
            "audioEncoding": "OGG_OPUS",
            "sampleRateHertz": sample_rate,
            "audioChannelCount": 1,
        }

        yc_s3_key = None
        try:
            if use_uri:
                # Upload to Yandex Object Storage and use URI
                uri, yc_s3_key = self.upload_to_yc_s3(audio_path, _log)
                _log(f"Отправка запроса в Yandex STT (URI mode, {file_size_mb:.1f} MB)...")
                body = {
                    "config": {"specification": spec, "folderId": self._folder_id},
                    "audio": {"uri": uri},
                }
            else:
                # Inline base64 content
                with open(audio_path, "rb") as f:
                    raw = f.read()
                _log(f"Отправка файла в Yandex STT inline ({file_size_mb:.1f} MB)...")
                audio_content = base64.b64encode(raw).decode("utf-8")
                body = {
                    "config": {"specification": spec, "folderId": self._folder_id},
                    "audio": {"content": audio_content},
                }

            # Submit with retry on 429
            operation_id = self._submit_with_retry(body, _log)
            _log(f"Операция создана: {operation_id}, ожидание результата...")

            # Poll until done
            return self._poll_operation(operation_id, _log)
        finally:
            # Cleanup temporary file from Yandex Object Storage
            if yc_s3_key:
                self.delete_from_yc_s3(yc_s3_key)

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
