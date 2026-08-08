from app.rag.embeddings import MockEmbedder, get_embedder


async def test_mock_embedder_shape():
    emb = MockEmbedder()
    vecs = await emb.embed_texts(["alpha", "beta"])
    assert len(vecs) == 2
    assert all(len(v) == MockEmbedder.dim for v in vecs)
    # deterministic + normalized
    assert vecs[0] == await emb.embed_query("alpha")
    norm = sum(x * x for x in vecs[0]) ** 0.5
    assert abs(norm - 1.0) < 1e-6


async def test_get_embedder_is_mock_in_tests():
    embedder = get_embedder()
    assert isinstance(embedder, MockEmbedder)
