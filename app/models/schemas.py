from pydantic import BaseModel
from enum import Enum


class JobStatus(str, Enum):
    PENDING = "pending"
    COMPRESSING = "compressing"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    GENERATING_FILES = "generating_files"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: str


class JobResult(BaseModel):
    job_id: str
    status: JobStatus
    message: str | None = None
    transcript_url: str | None = None
    summary_txt_url: str | None = None
    summary_pdf_url: str | None = None
    tasks_url: str | None = None
    error: str | None = None
    logs: list[str] = []
    stage_started_at: float | None = None


class HealthResponse(BaseModel):
    status: str
    version: str


class ServiceStatus(BaseModel):
    name: str
    status: str  # "ok", "error", "unconfigured"
    message: str
    latency_ms: float | None = None


class HealthDetailResponse(BaseModel):
    status: str
    version: str
    services: list[ServiceStatus]
