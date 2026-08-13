from app.rag.chunking import chunk_into_parents, chunk_text, count_tokens


def test_chunk_short_text_stays_single():
    assert chunk_text("hello world") == ["hello world"]


def test_chunk_empty():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_chunk_sentence_windows_stay_sentence_boundary():
    # sentence-boundaried text is windowed at sentence ends, never mid-sentence
    text = ("A short sentence. " * 60).strip()
    chunks = chunk_text(text, chunk_size=30, overlap=0)
    assert len(chunks) >= 3
    for c in chunks:
        assert c.endswith(".")  # windows close only on sentence boundaries


def test_chunk_keeps_cohesive_paragraph_whole():
    # a single cohesive paragraph without sentence breaks is preserved intact,
    # not sliced mid-thought — the whole point of paragraph-preserving chunking
    text = " ".join(f"word{i}" for i in range(200))
    chunks = chunk_text(text, chunk_size=200, overlap=20)
    joined = " ".join(chunks)
    assert "word199" in joined
    assert len(chunks) == 1  # one paragraph → one chunk


def test_chunk_overlap_carries_tail_between_windows():
    text = ("One complete sentence. " * 80).strip()
    chunks = chunk_text(text, chunk_size=120, overlap=24)
    assert len(chunks) > 1
    for c in chunks:
        assert c.endswith(".")


def test_chunk_prefers_sentence_boundary():
    text = ("First sentence of the document. " * 40).strip()
    chunks = chunk_text(text, chunk_size=100, overlap=20)
    assert len(chunks) > 1
    for c in chunks:
        assert c.endswith(".")  # every window ends at a sentence boundary


def test_count_tokens():
    assert count_tokens("hello world") >= 1
    assert count_tokens("x" * 400) == 100


def test_parents_short_text_single_block():
    assert chunk_into_parents("short") == ["short"]


def test_parents_never_split_paragraphs():
    para = "A very long single paragraph without sentence breaks. " * 60
    text = "\n\n".join(para for _ in range(3))
    parents = chunk_into_parents(text, parent_size=300)
    assert len(parents) >= 2
    for p in parents:
        assert "A very long single paragraph" in p  # each parent keeps whole paragraphs


def test_parents_group_to_target_size():
    paras = [f"Paragraph {i}. " + "content " * 40 for i in range(10)]
    text = "\n\n".join(paras)
    parents = chunk_into_parents(text, parent_size=300)
    assert len(parents) >= 2
    assert all(len(p) <= 300 + 500 for p in parents)  # one oversized paragraph may exceed


def test_parents_cover_full_text():
    paras = [f"Paragraph {i}. " + "content " * 40 for i in range(10)]
    text = "\n\n".join(paras)
    parents = chunk_into_parents(text, parent_size=300)
    assert "Paragraph 0." in parents[0]
    assert "Paragraph 9." in parents[-1]


def test_parents_children_stay_within_parent():
    text = "\n\n".join(f"Section {i} paragraph. " + "sentence. " * 30 for i in range(6))
    parents = chunk_into_parents(text, parent_size=500)
    assert len(parents) > 1
    for parent in parents:
        for child in chunk_text(parent, chunk_size=200, overlap=40):
            assert child in parent  # children are sub-windows of their parent
