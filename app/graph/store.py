"""Neo4j property-graph store (blueprint §6): documents, chunks, entities, topics.

Uses the async driver. All methods degrade to safe empty results when Neo4j is down.
"""

from functools import lru_cache

from neo4j import AsyncGraphDatabase

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


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
        for chunk_id, text, index in chunks:
            mentioned = [e["name"] for e in entities if e["name"].lower() in text.lower()]
            for name in mentioned:
                await self.add_mention(chunk_id, name)
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
                "WHERE e.name IN $names RETURN DISTINCT c.id AS id LIMIT $limit"
            )
        else:
            query = (
                "MATCH (e:Entity)-[:MENTIONS]-(c:Chunk) "
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
            "RETURN count(DISTINCT e) AS count"
        )
        async with self._driver.session() as session:
            record = await (await session.run(query, corpus=corpus)).single()
            return record["count"] if record else 0


@lru_cache
def get_graph_store() -> GraphStore:
    return GraphStore(get_settings())
