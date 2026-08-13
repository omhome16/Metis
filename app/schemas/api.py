"""API request/response schemas."""

from datetime import datetime

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


class VaultCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(None, max_length=2000)
    color: str | None = Field(None, max_length=32)


class VaultUpdate(BaseModel):
    description: str | None = Field(None, max_length=2000)
    color: str | None = Field(None, max_length=32)


class VaultSummary(BaseModel):
    name: str
    description: str | None = None
    color: str | None = None
    doc_count: int = 0
    chunk_count: int = 0
    image_count: int = 0
    entity_count: int = 0
    created_at: datetime | None = None


class DocumentSummary(BaseModel):
    id: str
    title: str
    corpus: str
    format: str
    size: int = 0
    chunk_count: int = 0
    image_count: int = 0
    status: str = "pending"  # indexed | pending | error
    ingested_at: datetime | None = None


class DocumentChunkOut(BaseModel):
    id: str
    index: int
    text: str
    tokens: int = 0


class DocumentMetaUpdate(BaseModel):
    tags: list[str] | None = None
    doc_date: datetime | None = None
    author: str | None = Field(None, max_length=256)


class DocumentMetaOut(BaseModel):
    id: str
    tags: list[str] = Field(default_factory=list)
    doc_date: datetime | None = None
    author: str | None = None


class ConversationCreate(BaseModel):
    title: str | None = Field(None, max_length=512)


class ConversationOut(BaseModel):
    id: str
    vault_name: str
    title: str
    message_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MessageOut(BaseModel):
    id: str
    role: str
    content: str = ""
    sources: dict | None = None
    citations: dict | None = None
    usage: dict | None = None
    error: str | None = None
    cached: bool = False
    created_at: datetime | None = None


class ConversationDetail(BaseModel):
    id: str
    vault_name: str
    title: str
    messages: list[MessageOut] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
