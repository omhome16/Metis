"""Session GUCs (hnsw.ef_search / hnsw.iterative_scan) are applied per connection."""

from sqlalchemy import text


async def test_gucs_applied(client, require_db):
    from app.db.session import engine

    async with engine.connect() as conn:
        ef = (await conn.execute(text("SHOW hnsw.ef_search"))).scalar()
        scan = (await conn.execute(text("SHOW hnsw.iterative_scan"))).scalar()
    assert int(ef) >= 100
    assert scan == "relaxed_order"