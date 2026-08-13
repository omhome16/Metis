"""SQLAlchemy ORM models for METIS (Postgres + pgvector).

Schema mirrors `metis-blueprint.md` §6. Embedding columns use the variable-length
`vector` type so the model dimension is config-driven (METIS_EMBED_DIM) at runtime.
"""

import uuid
from datetime import datetime, timezone

try:
    from pgvector.sqlalchemy import HALFVEC as HalfVectorType
except ImportError:  # older pgvector package
    from pgvector.sqlalchemy import HalfVector as HalfVectorType  # type: ignore[no-redef]

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(512))
    corpus: Mapped[str] = mapped_column(String(128), index=True)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    format: Mapped[str] = mapped_column(String(16))  # pdf | md | txt | image
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # sha256 → idempotent ingest
    ingest_job_id: Mapped[str | None] = mapped_column(String(36), index=True)
    raw_text: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(String(1024))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(128)), default=list)
    doc_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    author: Mapped[str | None] = mapped_column(String(256))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    chunks: Mapped[list["Chunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    doc_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column(Integer)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[list[float] | None] = mapped_column(HalfVectorType)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("parent_chunks.id", ondelete="CASCADE"), index=True
    )

    document: Mapped[Document] = relationship(back_populates="chunks")
    parent: Mapped["ParentChunk | None"] = relationship(back_populates="children")


class ParentChunk(Base):
    """P3.1 parent-child (small-to-big): un-embedded parent block for context."""

    __tablename__ = "parent_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    doc_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    start_chunk_idx: Mapped[int] = mapped_column(Integer, default=0)

    children: Mapped[list[Chunk]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )


class ImageRecord(Base):
    __tablename__ = "images"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    doc_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    file_path: Mapped[str] = mapped_column(String(1024))
    caption: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(128)), default=list)
    embedding: Mapped[list[float] | None] = mapped_column(HalfVectorType)


class GoldenQuestion(Base):
    __tablename__ = "golden_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    corpus: Mapped[str] = mapped_column(String(128), index=True)
    question: Mapped[str] = mapped_column(Text)
    ground_truth: Mapped[str] = mapped_column(Text)
    source_hint: Mapped[str | None] = mapped_column(Text)


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)


class Vault(Base):
    """A named library of documents (frontend vaults layer)."""

    __tablename__ = "vaults"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IngestJob(Base):
    __tablename__ = "ingest_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    corpus: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    per_file_errors: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CorpusVersion(Base):
    """Monotonic version per corpus, bumped on every successful ingest/delete."""

    __tablename__ = "corpus_versions"

    corpus: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CacheEntry(Base):
    """Semantic cache v2 (P2.3): grounded answers, indexed by question embedding.

    Lookup is a single `ORDER BY question_embedding <=> :q LIMIT 1` query gated
    by corpus_version (staleness) and expires_at (TTL). Redis is queue-only now.
    """

    __tablename__ = "cache_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    corpus: Mapped[str] = mapped_column(String(128), index=True)
    question: Mapped[str] = mapped_column(Text)
    question_embedding: Mapped[list[float] | None] = mapped_column(HalfVectorType)
    answer: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[dict] = mapped_column(JSON, default=dict)
    citations: Mapped[dict] = mapped_column(JSON, default=dict)
    done: Mapped[dict] = mapped_column(JSON, default=dict)
    model: Mapped[str] = mapped_column(String(128), default="")
    embed_model: Mapped[str] = mapped_column(String(256), default="")
    corpus_version: Mapped[int] = mapped_column(Integer, default=0)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CacheMetric(Base):
    """Coarse lookup/miss counters for /cache/stats (no Redis dependency)."""

    __tablename__ = "cache_metrics"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


class Conversation(Base):
    """A server-side chat session scoped to one vault (conversation history)."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    vault_name: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(512), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[dict | None] = mapped_column(JSON)
    citations: Mapped[dict | None] = mapped_column(JSON)
    usage: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    cached: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
