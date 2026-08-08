"""Graph endpoints: explore, connections (shortest path), stats."""

from fastapi import APIRouter, HTTPException, Query

from app.graph.store import get_graph_store

router = APIRouter(prefix="/graph", tags=["graph"])


async def _store():
    store = get_graph_store()
    if not await store.ping():
        raise HTTPException(status_code=503, detail="knowledge graph unavailable (Neo4j down?)")
    return store


@router.get("/explore")
async def explore(
    entity: str = Query(..., min_length=1),
    depth: int = Query(2, ge=1, le=4),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    store = await _store()
    return {"entity": entity, "neighbors": await store.explore(entity, depth, limit)}


@router.get("/connections")
async def connections(
    from_: str = Query(..., alias="from", min_length=1),
    to: str = Query(..., min_length=1),
    max_paths: int = Query(3, ge=1, le=5),
) -> dict:
    store = await _store()
    paths = await store.connections(from_, to, max_paths)
    return {"from": from_, "to": to, "paths": paths}


@router.get("/stats")
async def stats() -> dict:
    store = await _store()
    return await store.stats()
