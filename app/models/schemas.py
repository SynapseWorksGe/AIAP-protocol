from pydantic import BaseModel
from enum import Enum


class JobStatus(str, Enum):
    PENDING = "pending"
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
    transcript_url: str | None = None
    summary_txt_url: str | None = None
    summary_pdf_url: str | None = None
    tasks_url: str | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
