import logging
from pathlib import Path

import boto3
from botocore.config import Config

from app.config import settings

logger = logging.getLogger(__name__)


class S3Service:
    def __init__(self):
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=Config(signature_version="s3v4"),
        )
        self._bucket = settings.s3_bucket_name

    def upload_file(self, local_path: str, s3_key: str, content_type: str | None = None) -> str:
        """Upload a file to S3 and return its URL."""
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type

        self._client.upload_file(local_path, self._bucket, s3_key, ExtraArgs=extra_args)
        url = f"{settings.s3_endpoint_url}/{self._bucket}/{s3_key}"
        logger.info("Uploaded %s -> %s", local_path, url)
        return url

    def upload_bytes(self, data: bytes, s3_key: str, content_type: str = "application/octet-stream") -> str:
        """Upload bytes directly to S3."""
        self._client.put_object(
            Bucket=self._bucket,
            Key=s3_key,
            Body=data,
            ContentType=content_type,
        )
        url = f"{settings.s3_endpoint_url}/{self._bucket}/{s3_key}"
        logger.info("Uploaded bytes -> %s", url)
        return url

    def generate_presigned_url(self, s3_key: str, expires_in: int = 3600) -> str:
        """Generate a presigned URL for downloading."""
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": s3_key},
            ExpiresIn=expires_in,
        )

    def download_file(self, s3_key: str, local_path: str) -> str:
        """Download a file from S3."""
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self._bucket, s3_key, local_path)
        logger.info("Downloaded %s -> %s", s3_key, local_path)
        return local_path


s3_service = S3Service()
