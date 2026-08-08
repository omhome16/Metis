"""API request/response schemas."""

from pydantic import BaseModel, Field


class IngestResponse(BaseModel):
    job_id: str
    status: str = "queued"
    files_added: int = 0


class JobStatus(BaseModel):
    job_id: str
    corpus: str
    status: str
    progress: float = 0.0
    per_file_errors: dict[str, str] = Field(default_factory=dict)


class CorpusSummary(BaseModel):
    corpus: str
    doc_count: int = 0
    chunk_count: int = 0
    image_count: int = 0
    entity_count: int = 0
