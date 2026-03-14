from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Yandex Cloud
    yandex_api_key: str = ""
    yandex_folder_id: str = ""

    # Anthropic
    anthropic_api_key: str = ""

    # S3
    s3_endpoint_url: str = "https://eu2.contabostorage.com"
    s3_region: str = "eu2"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket_name: str = ""

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    upload_dir: str = "/tmp/aiap-uploads"
    max_audio_size_mb: int = 500

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
