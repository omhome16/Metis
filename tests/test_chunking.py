from app.rag.chunking import chunk_text, count_tokens


def test_chunk_short_text_stays_single():
    assert chunk_text("hello world") == ["hello world"]


def test_chunk_empty():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_chunk_fixed_size_no_overlap():
    text = "a" * 100
    chunks = chunk_text(text, chunk_size=30, overlap=0)
    assert len(chunks) >= 3
    assert all(len(c) <= 30 for c in chunks)


def test_chunk_joins_whole_text():
    text = " ".join(f"word{i}" for i in range(200))
    chunks = chunk_text(text, chunk_size=200, overlap=20)
    joined = " ".join(chunks)
    # every word appears somewhere (overlap may repeat a couple of words)
    assert "word199" in joined
    assert len(chunks) > 1


def test_chunk_prefers_sentence_boundary():
    text = ("First sentence of the document. " * 40).strip()
    chunks = chunk_text(text, chunk_size=100, overlap=20)
    assert len(chunks) > 1
    for c in chunks:
        assert c.endswith(".")  # every window ends at a sentence boundary


def test_count_tokens():
    assert count_tokens("hello world") >= 1
    assert count_tokens("x" * 400) == 100
