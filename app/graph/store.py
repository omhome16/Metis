"""Neo4j property-graph store (blueprint §6): documents, chunks, entities, topics.

Uses the async driver. All methods degrade to safe empty results when Neo4j is down.
"""

from functools import lru_cache

from neo4j import AsyncGraphDatabase

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _different_vaults(a: dict | None, b: dict | None) -> bool:
    """True when two entity nodes belong to disjoint primary vaults."""
    if not a or not b:
        return False
    va = set(a.get("corpora") or [])
    vb = set(b.get("corpora") or [])
    if not va or not vb:
        return False
    return not (va & vb)


class GraphStore:
    def __init__(self, settings: Settings):
        self._driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )

    async def close(self) -> None:
        await self._driver.close()

    async def ping(self) -> bool:
        try:
            await self._driver.verify_connectivity()
            return True
        except Exception:
            return False

    # ── schema ──────────────────────────────────────────────────────────────
    async def init_schema(self) -> None:
        statements = [
            "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE",
            "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE",
            "CREATE CONSTRAINT entity_name IF NOT EXISTS FOR (e:Entity) REQUIRE e.name IS UNIQUE",
        ]
        async with self._driver.session() as session:
            for stmt in statements:
                await session.run(stmt)

    # ── writes ──────────────────────────────────────────────────────────────
    async def upsert_document(self, doc_id: str, title: str, corpus: str) -> None:
        async with self._driver.session() as session:
            await session.run(
                "MERGE (d:Document {id: $id}) ON CREATE SET d.title = $title, d.corpus = $corpus",
                id=doc_id, title=title, corpus=corpus,
            )

    async def upsert_chunk(self, chunk_id: str, doc_id: str, text: str, index: int) -> None:
        async with self._driver.session() as session:
            await session.run(
                "MERGE (d:Document {id: $doc_id}) "
                "MERGE (c:Chunk {id: $chunk_id}) ON CREATE SET c.text = $text, c.index = $index "
                "MERGE (d)-[:CONTAINS]->(c)",
                doc_id=doc_id, chunk_id=chunk_id, text=text[:2000], index=index,
            )

    async def add_entity(self, name: str, entity_type: str = "Concept") -> None:
        async with self._driver.session() as session:
            await session.run(
                "MERGE (e:Entity {name: $name}) ON CREATE SET e.type = $type",
                name=name, type=entity_type,
            )

    async def add_mention(self, chunk_id: str, entity_name: str) -> None:
        async with self._driver.session() as session:
            await session.run(
                "MATCH (c:Chunk {id: $chunk_id}) MERGE (e:Entity {name: $name}) "
                "MERGE (c)-[:MENTIONS]->(e)",
                chunk_id=chunk_id, name=entity_name,
            )

    async def add_doc_mention(self, doc_id: str, entity_name: str) -> None:
        """Link an entity to a document (used when the name isn't found verbatim in any chunk)."""
        async with self._driver.session() as session:
            await session.run(
                "MATCH (d:Document {id: $doc_id}) MERGE (e:Entity {name: $name}) "
                "MERGE (e)-[:MENTIONED_IN]->(d)",
                doc_id=doc_id, name=entity_name,
            )

    async def add_relation(self, source: str, target: str, rel_type: str = "RELATED_TO", evidence_chunk: str | None = None) -> None:
        async with self._driver.session() as session:
            await session.run(
                "MATCH (a:Entity {name: $source}) MATCH (b:Entity {name: $target}) "
                "MERGE (a)-[r:RELATED_TO {type: $rel_type}]->(b) "
                "ON CREATE SET r.weight = 1.0, r.evidence_chunk = $evidence "
                "ON MATCH SET r.weight = r.weight + 1.0, "
                "  r.evidence_chunk = coalesce(r.evidence_chunk, $evidence)",
                source=source, target=target, rel_type=rel_type, evidence=evidence_chunk,
            )

    async def upsert_document_graph(
        self,
        doc_id: str,
        title: str,
        corpus: str,
        chunks: list[tuple[str, str, int]],  # (chunk_id, text, index)
        entities: list[dict],  # [{name, type}]
        relations: list[dict],  # [{source, target, type}]
        max_relations_per_chunk: int = 8,
    ) -> None:
        await self.upsert_document(doc_id, title, corpus)
        for chunk_id, text, index in chunks:
            await self.upsert_chunk(chunk_id, doc_id, text, index)
        for ent in entities:
            await self.add_entity(ent["name"], ent.get("type", "Concept"))

        # MENTIONS (entity ↔ chunk that contains it) + co-occurrence RELATED_TO
        for ent in entities:
            name = ent["name"]
            mentioned_in_chunk = False
            for chunk_id, text, index in chunks:
                if name.lower() in text.lower():
                    await self.add_mention(chunk_id, name)
                    mentioned_in_chunk = True
            if not mentioned_in_chunk and name:
                # entity extracted but not verbatim in any chunk → document-level link
                await self.add_doc_mention(doc_id, name)
        for chunk_id, text, index in chunks:
            mentioned = [e["name"] for e in entities if e["name"].lower() in text.lower()]
            if len(mentioned) > 1:
                pairs = [(mentioned[i], mentioned[j]) for i in range(len(mentioned)) for j in range(i + 1, len(mentioned))]
                for source, target in pairs[:max_relations_per_chunk]:
                    await self.add_relation(source, target, "RELATED_TO", evidence_chunk=chunk_id)

        # LLM-extracted relations (typed)
        for rel in relations[:50]:
            await self.add_relation(
                rel.get("source", ""),
                rel.get("target", ""),
                rel.get("type", "RELATED_TO"),
                evidence_chunk=doc_id,
            )

    # ── retrieval ───────────────────────────────────────────────────────────
    async def neighbor_chunk_ids(self, entity_names: list[str], max_hops: int = 2, limit: int = 10) -> list[str]:
        if not entity_names:
            return []
        if max_hops >= 2:
            query = (
                "MATCH (e:Entity)-[:RELATED_TO*1..2]-(e2:Entity)-[:MENTIONS]-(c:Chunk) "
                "WHERE e.name IN $names RETURN DISTINCT c.id AS id LIMIT $limit "
                "UNION "
                "MATCH (e:Entity)-[:MENTIONED_IN]->(:Document)-[:CONTAINS]->(c:Chunk) "
                "WHERE e.name IN $names RETURN DISTINCT c.id AS id LIMIT $limit"
            )
        else:
            query = (
                "MATCH (e:Entity)-[:MENTIONS]-(c:Chunk) "
                "WHERE e.name IN $names RETURN DISTINCT c.id AS id LIMIT $limit "
                "UNION "
                "MATCH (e:Entity)-[:MENTIONED_IN]->(:Document)-[:CONTAINS]->(c:Chunk) "
                "WHERE e.name IN $names RETURN DISTINCT c.id AS id LIMIT $limit"
            )
        async with self._driver.session() as session:
            result = await session.run(query, names=list(entity_names), limit=limit)
            return [record["id"] async for record in result]

    async def connections(self, from_name: str, to_name: str, max_paths: int = 3) -> list[dict]:
        query = (
            "MATCH p = allShortestPaths((a:Entity {name: $from})-[*1..6]-(b:Entity {name: $to})) "
            "RETURN [n IN nodes(p) | coalesce(n.name, n.text)] AS path, [r IN relationships(p) | type(r)] AS rels "
            "LIMIT $max_paths"
        )
        params = {"from": from_name, "to": to_name, "max_paths": max_paths}
        async with self._driver.session() as session:
            result = await session.run(query, **params)
            return [{"path": record["path"], "relationships": record["rels"]} async for record in result]

    async def explore(self, name: str, depth: int = 2, limit: int = 50) -> list[dict]:
        depth = max(1, min(int(depth), 4))  # Cypher forbids params in path-length bounds
        query = (
            f"MATCH (e:Entity {{name: $name}})-[r*1..{depth}]-(n) "
            "RETURN labels(n)[0] AS label, coalesce(n.name, n.text) AS value, "
            "[rel IN r | type(rel)] AS rels LIMIT $limit"
        )
        async with self._driver.session() as session:
            result = await session.run(query, name=name, depth=depth, limit=limit)
            return [
                {"label": record["label"], "value": record["value"], "relationships": record["rels"]}
                async for record in result
            ]

    async def stats(self) -> dict:
        count_query = "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS count"
        top_query = (
            "MATCH (e:Entity) RETURN e.name AS name, COUNT { (e)--() } AS degree "
            "ORDER BY degree DESC LIMIT 10"
        )
        async with self._driver.session() as session:
            counts = {
                record["label"]: record["count"]
                async for record in await session.run(count_query)
            }
            top = [
                {"name": record["name"], "degree": record["degree"]}
                async for record in await session.run(top_query)
            ]
        return {"nodes": counts, "top_entities": top}

    async def upsert_image(self, doc_id: str, title: str, corpus: str, caption: str, tags: list[str]) -> None:
        """Create/merge an Image node: BELONGS_TO its document; DEPICTS caption entities."""
        async with self._driver.session() as session:
            await session.run(
                "MERGE (d:Document {id: $doc_id}) "
                "MERGE (img:Image {id: $doc_id}) ON CREATE SET img.caption = $caption, img.tags = $tags "
                "MERGE (img)-[:BELONGS_TO]->(d)",
                doc_id=doc_id, caption=caption[:500], tags=list(tags)[:10],
            )
            for name in self._caption_entities(caption):
                await session.run(
                    "MATCH (img:Image {id: $doc_id}) MERGE (e:Entity {name: $name}) "
                    "MERGE (img)-[:DEPICTS]->(e)",
                    doc_id=doc_id, name=name,
                )

    @staticmethod
    def _caption_entities(caption: str) -> list[str]:
        import re

        stop = {"the", "a", "an", "this", "that", "image", "picture", "photo", "painting"}
        words = [w for w in re.findall(r"\b[A-Z][a-zA-Z]+\b", caption or "") if w.lower() not in stop]
        return words[:8]

    async def add_contradiction(self, chunk_a: str, chunk_b: str) -> None:
        async with self._driver.session() as session:
            await session.run(
                "MERGE (a:Chunk {id: $a}) MERGE (b:Chunk {id: $b}) MERGE (a)-[:CONTRADICTS]->(b)",
                a=chunk_a, b=chunk_b,
            )

    async def has_contradiction(self, chunk_a: str, chunk_b: str) -> bool:
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (a:Chunk {id: $a})-[r:CONTRADICTS]-(b:Chunk {id: $b}) RETURN count(r) AS n",
                a=chunk_a, b=chunk_b,
            )
            record = await result.single()
            return bool(record and record["n"] > 0)

    async def entity_count(self, corpus: str) -> int:
        query = (
            "MATCH (d:Document {corpus: $corpus})-[:CONTAINS]->(:Chunk)-[:MENTIONS]->(e:Entity) "
            "RETURN DISTINCT e.name AS n "
            "UNION "
            "MATCH (e:Entity)-[:MENTIONED_IN]->(d:Document {corpus: $corpus}) "
            "RETURN DISTINCT e.name AS n"
        )
        names: set[str] = set()
        async with self._driver.session() as session:
            result = await session.run(query, corpus=corpus)
            async for rec in result:
                names.add(rec["n"])
        return len(names)


    # ── library-wide (cross-vault) ─────────────────────────────────────────
    async def library_graph(self, node_limit: int = 260, edge_limit: int = 700) -> dict:
        """Cross-vault graph: every document + entity, corpus-tagged.

        Nodes carry `corpora` (the set of vaults they belong to) so the frontend
        can color clusters per vault and render multi-vault entities as bridges.
        Edges are flagged `cross: true` when they span two different vaults.
        """
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        seen_edges: set[tuple[str, str, str]] = set()

        def add_node(nid, label, name=None, ntype=None, degree=0, corpus=None, corpora=None):
            node = nodes.setdefault(
                nid,
                {
                    "id": nid, "label": label, "name": name or nid, "type": ntype or label,
                    "degree": 0, "corpus": corpus, "corpora": list(corpora or []),
                },
            )
            node["degree"] = max(node["degree"], degree)
            for c in corpora or []:
                if c and c not in node["corpora"]:
                    node["corpora"].append(c)
            if corpus and not node.get("corpus"):
                node["corpus"] = corpus
            return node

        def add_edge(source, target, kind, label, weight=1.0, cross=False):
            key = (source, target, kind)
            if key in seen_edges:
                return
            seen_edges.add(key)
            edges.append(
                {
                    "source": source, "target": target, "kind": kind, "label": label,
                    "weight": round(float(weight), 3), "cross": bool(cross),
                }
            )

        try:
            async with self._driver.session() as session:
                # documents
                result = await session.run(
                    "MATCH (d:Document) RETURN d.id AS id, d.title AS title, d.corpus AS corpus"
                )
                docs = [(r["id"], r["title"], r["corpus"]) async for r in result]
                for did, title, corpus in docs:
                    add_node(did, "Document", name=title, degree=1, corpus=corpus)

                # entities + the set of vaults each appears in
                result = await session.run(
                    "MATCH (e:Entity) WHERE EXISTS { (e)--() } "
                    "OPTIONAL MATCH (e)-[:MENTIONS]-(:Chunk)<-[:CONTAINS]-(d:Document) "
                    "OPTIONAL MATCH (e)-[:MENTIONED_IN]->(d2:Document) "
                    "WITH e, collect(DISTINCT d.corpus) AS c1, collect(DISTINCT d2.corpus) AS c2 "
                    "RETURN e.name AS name, coalesce(e.type, 'Concept') AS type, "
                    "  COUNT { (e)-[]-() } AS degree, [x IN c1 + c2 WHERE x IS NOT NULL] AS corpora "
                    "ORDER BY degree DESC LIMIT $nl",
                    nl=node_limit,
                )
                entities = [
                    (r["name"], r["type"], r["degree"], [c for c in r["corpora"] if c])
                    async for r in result
                ]
                for name, etype, degree, corpora in entities:
                    add_node(name, "Entity", name=name, ntype=etype, degree=degree, corpora=corpora)

                names = [name for name, _, _, _ in entities]
                if names:
                    # entity-entity edges among the included set (flag cross-vault pairs)
                    edge_query = (
                        "UNWIND $names AS a "
                        "MATCH (ea:Entity {name: a})-[r:RELATED_TO]-(eb:Entity) "
                        "WHERE eb.name IN $names AND a < eb.name "
                        "RETURN a AS source, eb.name AS target, coalesce(r.type, 'RELATED_TO') AS label, "
                        "  coalesce(r.weight, 1.0) AS weight "
                        "ORDER BY weight DESC LIMIT $el"
                    )
                    result = await session.run(edge_query, names=names, el=edge_limit)
                    async for r in result:
                        cross = _different_vaults(nodes.get(r["source"]), nodes.get(r["target"]))
                        add_edge(r["source"], r["target"], "RELATED", r["label"], r["weight"], cross=cross)

                if docs:
                    de_query = (
                        "MATCH (d:Document)-[:CONTAINS]->(:Chunk)-[:MENTIONS]->(e:Entity) "
                        "RETURN d.id AS did, e.name AS name, count(*) AS w "
                        "UNION "
                        "MATCH (e:Entity)-[:MENTIONED_IN]->(d:Document) "
                        "RETURN d.id AS did, e.name AS name, 1 AS w"
                    )
                    result = await session.run(de_query)
                    async for r in result:
                        if r["name"] in nodes and r["did"] in nodes:
                            add_edge(r["did"], r["name"], "MENTIONS", "mentions", r["w"])

                img_query = "MATCH (d:Document)<-[:BELONGS_TO]-(i:Image) RETURN i.id AS id, i.caption AS caption"
                result = await session.run(img_query)
                images = [(r["id"], r["caption"]) async for r in result]
                for iid, caption in images:
                    add_node(iid, "Image", name=(caption or "image")[:60], degree=1)
        except Exception as exc:  # noqa: BLE001
            logger.warning("library_graph failed: %s", exc)
            return {"nodes": [], "edges": []}

        node_list = [n for n in nodes.values() if n["degree"] > 0 or n["label"] != "Entity"]
        return {"nodes": node_list, "edges": edges[:edge_limit]}

    async def search_entities(self, query: str, limit: int = 12) -> list[dict]:
        """Entity name search across the whole graph (for journey pickers)."""
        if not query.strip():
            return []
        try:
            async with self._driver.session() as session:
                result = await session.run(
                    "MATCH (e:Entity) WHERE toLower(e.name) CONTAINS toLower($q) "
                    "RETURN e.name AS name, coalesce(e.type, 'Concept') AS type, COUNT { (e)--() } AS degree "
                    "ORDER BY degree DESC LIMIT $limit",
                    q=query.strip(), limit=max(1, min(int(limit), 50)),
                )
                return [
                    {"name": r["name"], "type": r["type"], "degree": r["degree"]}
                    async for r in result
                ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("search_entities failed: %s", exc)
            return []

    async def journey(self, from_name: str, to_name: str, max_hops: int = 6) -> dict | None:
        """Shortest entity-to-entity path across the whole graph, corpus-tagged.

        Returns None when no path exists. Cypher forbids params in path-length
        bounds, so the hop count is inlined (clamped 1..8).
        """
        try:
            hops = max(1, min(int(max_hops), 8))
        except (TypeError, ValueError):  # noqa: BLE001 — garbage input degrades to default
            hops = 6
        query = (
            f"MATCH p = allShortestPaths((a:Entity {{name: $from}})-[*1..{hops}]-(b:Entity {{name: $to}})) "
            "RETURN [n IN nodes(p) | labels(n)[0] + '::' + coalesce(n.name, n.text)] AS node_refs, "
            "       [r IN relationships(p) | type(r)] AS rels "
            "LIMIT 1"
        )
        try:
            async with self._driver.session() as session:
                result = await session.run(query, **{"from": from_name, "to": to_name})
                records = await result.fetch(1)
                if not records:
                    return None
                node_refs = records[0]["node_refs"]
                rels = records[0]["rels"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("journey failed for %s→%s: %s", from_name, to_name, exc)
            return None

        nodes_out: list[dict] = []
        for ref in node_refs:
            label, _, name = ref.partition("::")
            nodes_out.append({"name": name, "label": label or "Entity"})
        # attach vault corpora per node
        try:
            async with self._driver.session() as session:
                result = await session.run(
                    "UNWIND $names AS n "
                    "MATCH (e:Entity {name: n}) "
                    "OPTIONAL MATCH (e)-[:MENTIONS]-(:Chunk)<-[:CONTAINS]-(d:Document) "
                    "OPTIONAL MATCH (e)-[:MENTIONED_IN]->(d2:Document) "
                    "WITH n, collect(DISTINCT d.corpus) AS c1, collect(DISTINCT d2.corpus) AS c2 "
                    "RETURN n AS name, [x IN c1 + c2 WHERE x IS NOT NULL] AS corpora",
                    names=[nd["name"] for nd in nodes_out],
                )
                corpus_map = {r["name"]: [c for c in r["corpora"] if c] async for r in result}
        except Exception:  # noqa: BLE001
            corpus_map = {}
        for nd in nodes_out:
            nd["corpora"] = corpus_map.get(nd["name"], [])

        return {"from": from_name, "to": to_name, "nodes": nodes_out, "rels": rels}

    async def library_surprises(self, limit: int = 6) -> list[dict]:
        """Mine surprising cross-vault connections deterministically.

        Two kinds of card:
          - "shared": one entity that lives in two or more vaults (a bridge concept)
          - "bridge": a RELATED_TO pair whose endpoints live in different vaults
        Ranked by degree/weight so the strongest links surface first.
        """
        cards: list[dict] = []
        try:
            async with self._driver.session() as session:
                # shared concepts
                result = await session.run(
                    "MATCH (e:Entity) "
                    "OPTIONAL MATCH (e)-[:MENTIONS]-(:Chunk)<-[:CONTAINS]-(d:Document) "
                    "OPTIONAL MATCH (e)-[:MENTIONED_IN]->(d2:Document) "
                    "WITH e, collect(DISTINCT d.corpus) AS c1, collect(DISTINCT d2.corpus) AS c2 "
                    "WITH e, [x IN c1 + c2 WHERE x IS NOT NULL] AS corpora "
                    "WHERE size(corpora) > 1 "
                    "RETURN e.name AS name, coalesce(e.type, 'Concept') AS type, "
                    "  COUNT { (e)--() } AS degree, corpora "
                    "ORDER BY degree DESC LIMIT 8"
                )
                async for r in result:
                    cards.append({"kind": "shared", "entity": r["name"], "type": r["type"], "degree": r["degree"], "vaults": r["corpora"]})

                # cross-vault pairs
                result = await session.run(
                    "MATCH (a:Entity)-[r:RELATED_TO]-(b:Entity) "
                    "OPTIONAL MATCH (a)-[:MENTIONS]-(:Chunk)<-[:CONTAINS]-(da:Document) "
                    "OPTIONAL MATCH (b)-[:MENTIONS]-(:Chunk)<-[:CONTAINS]-(db:Document) "
                    "WITH a, b, r, collect(DISTINCT da.corpus) AS ca, collect(DISTINCT db.corpus) AS cb "
                    "WITH a, b, r, [x IN ca WHERE x IS NOT NULL] AS va, [x IN cb WHERE x IS NOT NULL] AS vb "
                    "WHERE size(va) > 0 AND size(vb) > 0 AND NOT va[0] = vb[0] "
                    "RETURN a.name AS source, b.name AS target, coalesce(r.weight, 1.0) AS weight, "
                    "  va[0] AS sv, vb[0] AS tv "
                    "ORDER BY weight DESC LIMIT 8"
                )
                async for r in result:
                    cards.append({"kind": "bridge", "source": r["source"], "target": r["target"], "weight": r["weight"], "vault_a": r["sv"], "vault_b": r["tv"]})
        except Exception as exc:  # noqa: BLE001
            logger.warning("library_surprises failed: %s", exc)
            return []
        return cards[: max(1, int(limit))]

    # ── vault / document lifecycle ────────────────────────────────────────
    async def vault_graph(self, corpus: str, node_limit: int = 150, edge_limit: int = 400) -> dict:
        """Bounded graph export for one corpus: entity network + doc/image links."""
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        seen_edges: set[tuple[str, str, str]] = set()

        def add_node(nid: str, label: str, name: str | None = None, ntype: str | None = None, degree: int = 0) -> None:
            node = nodes.setdefault(
                nid, {"id": nid, "label": label, "name": name or nid, "type": ntype or label, "degree": 0}
            )
            node["degree"] = max(node["degree"], degree)

        def add_edge(source: str, target: str, kind: str, label: str, weight: float = 1.0) -> None:
            key = (source, target, kind)
            if key in seen_edges:
                return
            seen_edges.add(key)
            edges.append({"source": source, "target": target, "kind": kind, "label": label, "weight": round(float(weight), 3)})

        try:
            async with self._driver.session() as session:
                ent_query = (
                    "MATCH (d:Document {corpus: $c}) "
                    "MATCH (d)-[:CONTAINS]->(:Chunk)-[:MENTIONS]->(e:Entity) "
                    "WITH e, COUNT { (e)-[]-() } AS degree "
                    "RETURN e.name AS name, coalesce(e.type, 'Concept') AS type, degree "
                    "UNION "
                    "MATCH (e:Entity)-[:MENTIONED_IN]->(d:Document {corpus: $c}) "
                    "WITH e, COUNT { (e)-[]-() } AS degree "
                    "RETURN e.name AS name, coalesce(e.type, 'Concept') AS type, degree "
                    "ORDER BY degree DESC LIMIT $nl"
                )
                result = await session.run(ent_query, c=corpus, nl=node_limit)
                entities = [(r["name"], r["type"], r["degree"]) async for r in result]
                names = [name for name, _, _ in entities]
                for name, etype, degree in entities:
                    add_node(name, "Entity", name=name, ntype=etype, degree=degree)

                if names:
                    edge_query = (
                        "UNWIND $names AS a "
                        "MATCH (ea:Entity {name: a})-[r:RELATED_TO]-(eb:Entity) "
                        "WHERE eb.name IN $names AND a < eb.name "
                        "RETURN a AS source, eb.name AS target, coalesce(r.type, 'RELATED_TO') AS label, "
                        "  coalesce(r.weight, 1.0) AS weight "
                        "ORDER BY weight DESC LIMIT $el"
                    )
                    result = await session.run(edge_query, names=names, el=edge_limit)
                    async for r in result:
                        add_edge(r["source"], r["target"], "RELATED", r["label"], r["weight"])

                doc_query = "MATCH (d:Document {corpus: $c}) RETURN d.id AS id, d.title AS title"
                result = await session.run(doc_query, c=corpus)
                docs = [(r["id"], r["title"]) async for r in result]
                for did, title in docs:
                    add_node(did, "Document", name=title, degree=1)

                if docs:
                    de_query = (
                        "MATCH (d:Document {corpus: $c})-[:CONTAINS]->(:Chunk)-[:MENTIONS]->(e:Entity) "
                        "RETURN d.id AS did, e.name AS name, count(*) AS w "
                        "UNION "
                        "MATCH (e:Entity)-[:MENTIONED_IN]->(d:Document {corpus: $c}) "
                        "RETURN d.id AS did, e.name AS name, 1 AS w"
                    )
                    result = await session.run(de_query, c=corpus)
                    async for r in result:
                        if r["name"] in nodes and r["did"] in nodes:
                            add_edge(r["did"], r["name"], "MENTIONS", "mentions", r["w"])

                img_query = (
                    "MATCH (d:Document {corpus: $c})<-[:BELONGS_TO]-(i:Image) "
                    "RETURN i.id AS id, i.caption AS caption"
                )
                result = await session.run(img_query, c=corpus)
                images = [(r["id"], r["caption"]) async for r in result]
                for iid, caption in images:
                    add_node(iid, "Image", name=(caption or "image")[:60], degree=1)

                if images:
                    ie_query = (
                        "MATCH (i:Image)-[:BELONGS_TO]->(d:Document {corpus: $c}), "
                        "(i)-[:DEPICTS]->(e:Entity) "
                        "RETURN i.id AS iid, e.name AS name"
                    )
                    result = await session.run(ie_query, c=corpus)
                    async for r in result:
                        if r["name"] in nodes and r["iid"] in nodes:
                            add_edge(r["iid"], r["name"], "DEPICTS", "depicts", 1.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("vault_graph failed for %s: %s", corpus, exc)
            return {"nodes": [], "edges": []}

        node_list = [n for n in nodes.values() if n["degree"] > 0 or n["label"] != "Entity"]
        return {"nodes": node_list, "edges": edges[:edge_limit]}

    async def delete_document(self, doc_id: str) -> None:
        """Remove a document (and its image node, if any) from the graph; prune doc-less nodes."""
        try:
            async with self._driver.session() as session:
                await session.run("MATCH (i:Image {id: $id}) DETACH DELETE i", id=doc_id)
                await session.run("MATCH (d:Document {id: $id}) DETACH DELETE d", id=doc_id)
                await self._sweep_orphans(session)
        except Exception as exc:  # noqa: BLE001
            logger.warning("delete_document failed for %s: %s", doc_id, exc)

    async def delete_vault(self, corpus: str) -> None:
        """Remove every document + image of a corpus from the graph; prune doc-less nodes."""
        try:
            async with self._driver.session() as session:
                await session.run("MATCH (d:Document {corpus: $c}) DETACH DELETE d", c=corpus)
                await self._sweep_orphans(session)
        except Exception as exc:  # noqa: BLE001
            logger.warning("delete_vault failed for %s: %s", corpus, exc)

    @staticmethod
    async def _sweep_orphans(session) -> None:
        """Delete chunks/images whose document is gone, then entities left with no edges."""
        await session.run("MATCH (c:Chunk) WHERE NOT EXISTS { (c)<-[:CONTAINS]-(:Document) } DETACH DELETE c")
        await session.run("MATCH (i:Image) WHERE NOT EXISTS { (i)-[:BELONGS_TO]->(:Document) } DETACH DELETE i")
        await session.run("MATCH (e:Entity) WHERE NOT EXISTS { (e)--() } DELETE e")


@lru_cache
def get_graph_store() -> GraphStore:
    return GraphStore(get_settings())
