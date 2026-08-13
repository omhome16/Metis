"""P3.2: query-metadata extraction and document filters on both retrieval arms."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select

from app.db.models import Chunk, Document
from app.db.session import async_session_factory
from app.rag.embeddings import get_embedder
from app.rag.metadata import _clean, extract_query_metadata, parse_metadata_json
from app.rag.retrieval import keyword_search, store_chunks, vector_search


class _StructuredGateway:
    """Gateway stub whose `structured` returns a fixed payload."""

    def __init__(self, payload: dict):
        self._payload = payload

    async def structured(self, task, messages, json_schema):
        return self._payload


class _BrokenGateway:
    async def structured(self, task, messages, json_schema):
        raise RuntimeError("provider down")


async def _cleanup(corpus: str) -> None:
    async with async_session_factory() as session:
        await session.execute(
            delete(Chunk).where(
                Chunk.doc_id.in_(select(Document.id).where(Document.corpus == corpus))
            )
        )
        await session.execute(delete(Document).where(Document.corpus == corpus))
        await session.commit()


async def _seed_two_docs(corpus: str) -> None:
    """doc1: tagged 'policy', dated 2024, by Adams. doc2: tagged 'code', no date."""
    embedder = get_embedder()
    async with async_session_factory() as session:
        session.add_all(
            [
                Document(
                    id=str(uuid.uuid4()),
                    title="2024 policy",
                    corpus=corpus,
                    format="txt",
                    content_hash=uuid.uuid4().hex,
                    tags=["policy", "privacy"],
                    doc_date=datetime(2024, 5, 1, tzinfo=UTC),
                    author="Jane Adams",
                    raw_text="the privacy policy text",
                ),
                Document(
                    id=str(uuid.uuid4()),
                    title="code notes",
                    corpus=corpus,
                    format="txt",
                    content_hash=uuid.uuid4().hex,
                    tags=["code", "fastapi"],
                    author="Bob",
                    raw_text="fastapi code notes",
                ),
            ]
        )
        await session.commit()
        docs = (
            (await session.execute(select(Document).where(Document.corpus == corpus)))
            .scalars()
            .all()
        )
        for doc in docs:
            embeddings = await embedder.embed_texts([doc.raw_text or ""])
            await store_chunks(session, doc.id, [doc.raw_text or ""], embeddings)


async def test_clean_filters_empty_values():
    assert _clean("q", {}) == {}
    assert _clean("q", {"tags": ["", "policy"], "date_from": "not-a-date", "author": ""}) == {
        "tags": ["policy"]
    }
    assert _clean("q", {"date_from": "2024-01-01", "date_to": "2024-12-31", "author": "Adams"}) == {
        "date_from": "2024-01-01",
        "date_to": "2024-12-31",
        "author": "Adams",
    }


def test_parse_metadata_json_lenient():
    assert parse_metadata_json('{"tags": ["x"]}') == {"tags": ["x"]}
    assert parse_metadata_json('```json\n{"tags": ["x"]}\n```') == {"tags": ["x"]}
    assert parse_metadata_json("garbage") == {}


async def test_extract_query_metadata_llm_path():
    gateway = _StructuredGateway(
        {"tags": ["policy"], "date_from": "2024-01-01", "date_to": "2024-12-31", "author": "Adams"}
    )
    meta = await extract_query_metadata(gateway, "the 2024 policy by Adams")
    assert meta == {
        "tags": ["policy"],
        "date_from": "2024-01-01",
        "date_to": "2024-12-31",
        "author": "Adams",
    }


async def test_extract_query_metadata_broken_gateway_falls_back_to_year():
    meta = await extract_query_metadata(_BrokenGateway(), "the 2024 policy on privacy")
    assert meta.get("date_from") == "2024-01-01"
    assert meta.get("date_to") == "2024-12-31"
    assert "tags" not in meta


async def test_extract_query_metadata_empty_result():
    meta = await extract_query_metadata(_StructuredGateway({}), "no hints here")
    assert meta == {}


async def test_vector_search_meta_tag_filter(require_db):
    corpus = f"test-meta-{uuid.uuid4().hex[:8]}"
    await _seed_two_docs(corpus)
    embedder = get_embedder()
    async with async_session_factory() as session:
        qvec = await embedder.embed_query("policy")
        hits = await vector_search(
            session, qvec, corpus=corpus, top_k=10, meta={"tags": ["policy"]}
        )
        assert len(hits) == 1
        assert hits[0].doc_title == "2024 policy"


async def test_vector_search_meta_date_filter(require_db):
    corpus = f"test-meta-date-{uuid.uuid4().hex[:8]}"
    await _seed_two_docs(corpus)
    embedder = get_embedder()
    async with async_session_factory() as session:
        qvec = await embedder.embed_query("notes")
        hits = await vector_search(
            session,
            qvec,
            corpus=corpus,
            top_k=10,
            meta={"date_from": "2024-01-01", "date_to": "2024-12-31"},
        )
        assert len(hits) == 1
        assert hits[0].doc_title == "2024 policy"


async def test_keyword_search_meta_author_filter(require_db):
    corpus = f"test-meta-auth-{uuid.uuid4().hex[:8]}"
    await _seed_two_docs(corpus)
    async with async_session_factory() as session:
        hits = await keyword_search(
            session, "policy", corpus=corpus, top_k=10, meta={"author": "adams"}
        )
        assert len(hits) == 1
        assert hits[0].doc_title == "2024 policy"


async def test_keyword_search_meta_tag_filter(require_db):
    corpus = f"test-meta-ktag-{uuid.uuid4().hex[:8]}"
    await _seed_two_docs(corpus)
    async with async_session_factory() as session:
        hits = await keyword_search(
            session, "policy", corpus=corpus, top_k=10, meta={"tags": ["policy"]}
        )
        assert len(hits) == 1
        assert hits[0].doc_title == "2024 policy"


async def test_meta_filters_empty_meta_passthrough(require_db):
    corpus = f"test-meta-none-{uuid.uuid4().hex[:8]}"
    await _seed_two_docs(corpus)
    embedder = get_embedder()
    async with async_session_factory() as session:
        qvec = await embedder.embed_query("policy")
        hits = await vector_search(session, qvec, corpus=corpus, top_k=10, meta={})
        assert len(hits) == 2  # no filter → both docs

    await _cleanup(corpus)
