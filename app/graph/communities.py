"""Community detection + summaries over the entity graph (P5).

Runs GDS Leiden/Louvain when the plugin is present, otherwise falls back to a
deterministic label-propagation algorithm so the feature works on Neo4j
Community Edition (and in tests). Assignments are stored on Entity nodes as
`community_id` / `community_rank` (rank = degree order within the community);
per-community LLM summaries live on `:Community {id, summary, entity_count}`
nodes and are generated idempotently (existing summaries are kept).
"""

from collections import defaultdict
from typing import Any

from app.core.logging import get_logger
from app.gateway.base import ChatResult
from app.gateway.gateway import LLMGateway
from app.graph.store import GraphStore

logger = get_logger(__name__)

_MAX_LPA_ITERATIONS = 20
_BATCH = 200
_SUMMARY_MAX_MEMBERS = 16
_SUMMARY_SAMPLE_CHUNKS = 3
_SUMMARY_SNIPPET_CHARS = 220

_SUMMARY_SYSTEM = (
    "You are analyzing a knowledge graph of a library. Each community is a "
    "cluster of related concepts. Write a 2-3 sentence summary of the shared "
    "topic the members cover, naming the key concepts."
)


async def _gds_available(session) -> bool:
    try:
        result = await session.run("CALL gds.version() YIELD version RETURN version")
        return bool(await result.single())
    except Exception:  # noqa: BLE001 — GDS simply isn't installed
        return False


async def _load_edges(store: GraphStore) -> list[tuple[str, str, float]]:
    """Undirected, weight-aggregated entity-entity edges (deterministic order)."""
    agg: dict[tuple[str, str], float] = defaultdict(float)
    async with store._driver.session() as session:
        result = await session.run(
            "MATCH (a:Entity)-[r:RELATED_TO]->(b:Entity) "
            "RETURN a.canonical AS src, b.canonical AS tgt, coalesce(r.weight, 1.0) AS w"
        )
        async for rec in result:
            src, tgt, w = rec["src"], rec["tgt"], float(rec["w"])
            if not src or not tgt or src == tgt:
                continue
            key = (min(src, tgt), max(src, tgt))
            agg[key] += w
    return [(s, t, w) for (s, t), w in sorted(agg.items())]


def _label_propagation(nodes: list[str], edges: list[tuple[str, str, float]]) -> dict[str, str]:
    """Deterministic LPA: fixed node order, weighted neighbor votes, tie → smallest label.

    Labels are compacted to `c0, c1, …` in first-seen node order, so identical
    graphs produce identical community ids across runs.
    """
    index = {n: i for i, n in enumerate(nodes)}
    labels = list(range(len(nodes)))
    neighbors: list[list[tuple[int, float]]] = [[] for _ in nodes]
    for a, b, w in edges:
        i, j = index[a], index[b]
        neighbors[i].append((j, w))
        neighbors[j].append((i, w))
    for _ in range(_MAX_LPA_ITERATIONS):
        changed = 0
        for i in range(len(nodes)):
            votes: dict[int, float] = defaultdict(float)
            votes[labels[i]] += 1.0  # keep the node's own label as a tie-breaker
            for j, w in neighbors[i]:
                votes[labels[j]] += w
            best = min(votes, key=lambda k: (-votes[k], k))
            if best != labels[i]:
                labels[i] = best
                changed += 1
        if changed == 0:
            break
    compact: dict[int, str] = {}
    out: dict[str, str] = {}
    for i, n in enumerate(nodes):
        lab = labels[i]
        if lab not in compact:
            compact[lab] = f"c{len(compact)}"
        out[n] = compact[lab]
    return out


async def _write_assignments(store: GraphStore, rows: list[dict[str, Any]]) -> None:
    async with store._driver.session() as session:
        for start in range(0, len(rows), _BATCH):
            await session.run(
                "UNWIND $rows AS r "
                "MATCH (e:Entity {canonical: r.name}) "
                "SET e.community_id = r.cid, e.community_rank = r.rank",
                rows=rows[start : start + _BATCH],
            )


async def _detect_with_lpa(store: GraphStore) -> dict:
    edges = await _load_edges(store)
    all_names: set[str] = set()
    async with store._driver.session() as session:
        result = await session.run("MATCH (e:Entity) RETURN e.canonical AS name")
        async for rec in result:
            if rec["name"]:
                all_names.add(rec["name"])
    nodes = sorted(all_names)
    if not nodes:
        return {"engine": "lpa", "communities": 0, "entities": 0}
    degree: dict[str, int] = defaultdict(int)
    for a, b, _ in edges:
        degree[a] += 1
        degree[b] += 1
    assignment = _label_propagation(nodes, edges)
    by_community: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        by_community[assignment[node]].append(node)
    rows = []
    for cid, members in by_community.items():
        ranked = sorted(members, key=lambda m: (-degree[m], m))
        for pos, name in enumerate(ranked, start=1):
            rows.append({"name": name, "cid": cid, "rank": pos})
    await _write_assignments(store, rows)
    return {"engine": "lpa", "communities": len(by_community), "entities": len(nodes)}


async def _detect_with_gds(store: GraphStore, session) -> dict:
    """GDS Louvain over RELATED_TO (weighted, undirected) when the plugin exists."""
    try:
        await session.run(
            "CALL gds.graph.drop('metis-entities', false) YIELD graphName RETURN graphName"
        )
    except Exception:  # noqa: BLE001 — graph may not exist yet
        pass
    await session.run(
        "CALL gds.graph.project('metis-entities', 'Entity', "
        "{RELATED_TO: {orientation: 'UNDIRECTED', properties: "
        "{weight: {property: 'weight', aggregation: 'MAX'}}}})"
    )
    result = await session.run(
        "CALL gds.louvain.stream('metis-entities', {relationshipWeightProperty: 'weight'}) "
        "YIELD nodeId, communityId "
        "WITH gds.util.asNode(nodeId) AS e, toString(communityId) AS cid "
        "RETURN e.canonical AS name, cid"
    )
    rows = [(rec["name"], rec["cid"]) async for rec in result]
    if not rows:
        return {"engine": "gds-louvain", "communities": 0, "entities": 0}
    degree: dict[str, int] = defaultdict(int)
    by_community: dict[str, list[str]] = defaultdict(list)
    for name, cid in rows:
        degree[name] += 1
        by_community[cid].append(name)
    write_rows = []
    for cid, members in by_community.items():
        ranked = sorted(members, key=lambda m: (-degree[m], m))
        for pos, name in enumerate(ranked, start=1):
            write_rows.append({"name": name, "cid": cid, "rank": pos})
    await _write_assignments(store, write_rows)
    return {"engine": "gds-louvain", "communities": len(by_community), "entities": len(rows)}


async def detect_communities(store: GraphStore) -> dict:
    """Assign community_id + community_rank to every Entity node. Never raises."""
    try:
        async with store._driver.session() as session:
            if await _gds_available(session):
                try:
                    return await _detect_with_gds(store, session)
                except Exception as exc:  # noqa: BLE001 — plugin present but the run failed
                    logger.warning("GDS community detection failed: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("GDS availability probe failed: %s", exc)
    try:
        return await _detect_with_lpa(store)
    except Exception as exc:  # noqa: BLE001 — must never break the endpoint
        logger.warning("LPA community detection failed: %s", exc)
        return {"engine": "none", "communities": 0, "entities": 0, "error": str(exc)}


async def _community_has_summary(store: GraphStore, community_id: str) -> bool:
    async with store._driver.session() as session:
        result = await session.run(
            "MATCH (c:Community {id: $id}) WHERE c.summary IS NOT NULL RETURN count(c) AS n",
            id=community_id,
        )
        record = await result.single()
        return bool(record and record["n"] > 0)


async def _evidence_snippets(store: GraphStore, members: list[str]) -> list[str]:
    """A few chunk snippets where top members are mentioned (deterministic order)."""
    snippets: list[str] = []
    async with store._driver.session() as session:
        result = await session.run(
            "UNWIND $names AS name "
            "MATCH (e:Entity {canonical: name})-[:MENTIONS]-(c:Chunk) "
            "RETURN name, c.text AS text ORDER BY name, c.index LIMIT $limit",
            names=members[:_SUMMARY_SAMPLE_CHUNKS],
            limit=_SUMMARY_SAMPLE_CHUNKS * len(members[:_SUMMARY_SAMPLE_CHUNKS]),
        )
        async for rec in result:
            text = (rec["text"] or "").strip()
            if text:
                snippets.append(text[:_SUMMARY_SNIPPET_CHARS])
    return snippets


async def _summarize_one(gateway: LLMGateway, store: GraphStore, community: dict) -> str:
    members = community["members"]
    snippets = await _evidence_snippets(store, members)
    result: ChatResult = await gateway.chat(
        "generation",
        [
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Community members: {', '.join(members[:_SUMMARY_MAX_MEMBERS])}"
                    f"\n\nEvidence snippets:\n" + "\n".join(f"- {s}" for s in snippets)
                ),
            },
        ],
        temperature=0.3,
    )
    return (result.text or "").strip()[:400]


async def summarize_communities(gateway: LLMGateway, store: GraphStore) -> dict:
    """One LLM call per community; MERGE :Community nodes. Idempotent per community."""
    async with store._driver.session() as session:
        result = await session.run(
            "MATCH (e:Entity) WHERE e.community_id IS NOT NULL "
            "WITH e.community_id AS cid, collect(e.canonical) AS names, "
            "  collect(e.community_rank) AS ranks, count(e) AS n "
            "RETURN cid, n, names, ranks ORDER BY n DESC"
        )
        communities = [
            {
                "id": rec["cid"],
                "entity_count": rec["n"],
                "members": [
                    m
                    for _, m in sorted(
                        zip(rec["ranks"], rec["names"], strict=True),
                        key=lambda pair: pair[0] or 0,
                    )
                ],
            }
            async for rec in result
        ]
    made = 0
    skipped = 0
    for community in communities:
        if await _community_has_summary(store, community["id"]):
            skipped += 1
            continue
        try:
            summary = await _summarize_one(gateway, store, community)
        except Exception as exc:  # noqa: BLE001 — one bad community must not kill the job
            logger.warning("community summary failed for %s: %s", community["id"], exc)
            continue
        async with store._driver.session() as session:
            await session.run(
                "MERGE (c:Community {id: $id}) "
                "SET c.summary = $summary, c.entity_count = $count, c.members = $members",
                id=community["id"],
                summary=summary,
                count=community["entity_count"],
                members=community["members"][:_SUMMARY_MAX_MEMBERS],
            )
        made += 1
    return {"summaries": made, "skipped": skipped, "total": len(communities)}
