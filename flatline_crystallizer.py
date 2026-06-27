import hashlib
import json
import requests
from datetime import datetime

from flatline_l3_query import upsert_chunk, ensure_collection

CRYSTALLIZER_URL = "http://localhost:1238/v1/chat/completions"
CRYSTALLIZER_MODEL = "qwen3.6-27b@q3_k_s"
MIN_CONFIDENCE = 0.3

SYSTEM_PROMPT = """You are a knowledge crystallizer. Your job is to read raw session
observations and extract structured facts for permanent storage in a
knowledge graph.

You are precise, conservative, and schema-disciplined.

You do not invent. You do not infer beyond what observations support.

When uncertain: lower confidence. Do not omit.

Output: valid JSON only. No prose. No explanation. No markdown."""


def get_l1_observations(db_path, session_id):
    """Reads all observations for the session from SQLite.
    Returns them formatted as a plain text block.
    """
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """SELECT content, status, decay_class, recorded_at
               FROM observations
               WHERE session_id = ?
               ORDER BY recorded_at""",
            (session_id,),
        )
        lines = []
        for row in cursor:
            lines.append(
                f"[{row[1]}] ({row[2]}) — {row[0]}  (recorded: {row[3]})"
            )
        return "\n".join(lines)
    finally:
        conn.close()


def get_l2_subgraph(neo4j_session, session_id):
    """Queries Neo4j for facts and entities related to this session.
    Returns formatted as JSON string.
    """
    result = neo4j_session.run(
        """
        MATCH (e:Entity)<-[:ASSERTS]-(f:Fact)-[:SOURCED_FROM]->(s:Session)
        WHERE s.id = $sid
        RETURN {
          entities: collect(DISTINCT {
            id: e.id,
            label: e.label,
            type: e.type,
            confidence: e.confidence,
            session_ids: e.session_ids
          }),
          facts: collect(DISTINCT {
            id: f.id,
            statement: f.statement,
            subject: f.subject,
            predicate: f.predicate,
            object: f.object,
            confidence: f.confidence,
            status: f.status,
            decay_class: f.decay_class,
            source_sessions: f.source_sessions
          })
        }
        """,
        sid=session_id,
    )
    record = result.single()
    if record is None:
        return json.dumps({"entities": [], "facts": []})
    data = record.data()
    return json.dumps(data)


def call_crystallizer(l1_content, l2_context, user_annotation=None, url=None):
    """Builds the prompt, POSTs to the model, returns raw parsed dict.

    *url* defaults to CRYSTALLIZER_URL (legacy). Pass a different URL
    (e.g. port 1235) when calling via the MCP sign_off path.
    """
    target_url = url or CRYSTALLIZER_URL
    annotation = user_annotation if user_annotation is not None else "null"
    user_prompt = (
        "<graph_context>\n"
        f"  {l2_context}\n"
        "</graph_context>\n"
        "<observations>\n"
        f"  {l1_content}\n"
        "</observations>\n"
        "<session_annotation>\n"
        f"  {annotation}\n"
        "</session_annotation>"
    )
    payload = {
        "model": CRYSTALLIZER_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
    }
    resp = requests.post(target_url, json=payload)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Crystallizer HTTP error: {resp.status_code} {resp.text}"
        )
    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    except (KeyError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Crystallizer response parse failure: {e}")


def write_entities(neo4j_session, entities):
    """Upserts entities into Neo4j. Returns count written."""
    count = 0
    for entity in entities:
        neo4j_session.run(
            """
            MERGE (e:Entity {id: $eid})
            ON CREATE SET e.label = $label, e.type = $type,
                          e.first_seen = timestamp(), e.confidence = $confidence
            ON MATCH SET e.label = $label, e.type = $type,
                          e.confidence = CASE WHEN $confidence > e.confidence THEN $confidence ELSE e.confidence END,
                          e.aliases = coalesce(e.aliases, []) + $alias_list,
                          e.session_ids = coalesce(e.session_ids, []) + $sid_list
            """,
            eid=entity.get("id", ""),
            label=entity["label"],
            type=entity["type"],
            confidence=entity.get("confidence", 0.5),
            alias_list=[entity["label"]] + entity.get("aliases", []),
            sid_list=entity.get("session_ids", []),
        )
        count += 1
    return count


def write_facts(neo4j_session, facts, session_id):
    """Filters by MIN_CONFIDENCE, upserts facts, returns count written."""
    count = 0
    now = datetime.utcnow().isoformat()
    for fact in facts:
        if fact.get("confidence", 0) < MIN_CONFIDENCE:
            continue
        subject = fact.get("subject", "")
        obj = fact.get("object", "")
        corroboration_ids = fact.get("corroborates", [])
        contradiction_flag = fact.get("contradiction_flag")

        neo4j_session.run(
            """
            MERGE (f:Fact {id: $fid})
            ON CREATE SET
              f.statement = $statement,
              f.subject = $subject,
              f.predicate = $predicate,
              f.object = $object,
              f.valid_from = $now,
              f.confidence = $confidence,
              f.corroboration_count = 0,
              f.decay_class = $decay_class,
              f.status = 'CANDIDATE',
              f.source_type = 'SESSION',
              f.source_sessions = [$sid]
           ON MATCH SET
               f.statement = $statement,
               f.confidence = CASE WHEN $confidence > f.confidence THEN $confidence ELSE f.confidence END,
               f.corroboration_count = f.corroboration_count + 1,
               f.source_sessions = coalesce(f.source_sessions, []) + $sid
            """,
            fid=fact.get("id", ""),
            statement=fact["statement"],
            subject=subject,
            predicate=fact["predicate"],
            object=obj,
            confidence=fact["confidence"],
            decay_class=fact.get("decay_class", "TRANSIENT"),
            now=now,
            sid=session_id,
        )

        if corroboration_ids:
            for corr_id in corroboration_ids:
                neo4j_session.run(
                    "MATCH (a:Fact {id: $a}) MATCH (b:Fact {id: $b}) "
                    "MERGE (a)-[:CORROBORATES]->(b)",
                    a=corr_id, b=fact.get("id", ""),
                )

        if contradiction_flag:
            neo4j_session.run(
                """
                MATCH (f:Fact {id: $fid})
                MATCH (existing:Fact)
                WHERE existing.statement CONTAINS $keyword
                  AND existing.id <> $fid
                  AND existing.status IN ['ACTIVE', 'VALIDATED', 'CANDIDATE']
                MERGE (f)-[:CONTRADICTS]->(existing)
                """,
                fid=fact.get("id", ""),
                keyword=contradiction_flag[:40],
            )

        count += 1
    return count


def embed_facts(facts, session_id):
    """Embeds facts into L3 Qdrant. Returns count embedded."""
    ensure_collection()
    count = 0
    for fact in facts:
        if fact.get("confidence", 0) < MIN_CONFIDENCE:
            continue
        fact_id = fact.get("id", "")
        hex_str = hashlib.md5(fact_id.encode()).hexdigest()[:8]
        chunk_id = int(hex_str, 16)
        metadata = {
            "fact_id": fact_id,
            "node_type": "SESSION",
            "source_type": fact.get("source_type", "SESSION"),
            "status": fact.get("status", "CANDIDATE"),
            "decay_class": fact.get("decay_class", "TRANSIENT"),
            "confidence": fact.get("confidence", 0),
            "session_id": session_id,
        }
        upsert_chunk(chunk_id, fact["statement"], metadata)
        count += 1
    return count


def crystallize_session(db_path, neo4j_session, session_id, user_annotation=None, url=None):
    """Orchestrates the full crystallization pipeline."""
    l1_content = get_l1_observations(db_path, session_id)
    l2_context = get_l2_subgraph(neo4j_session, session_id)
    result = call_crystallizer(l1_content, l2_context, user_annotation, url=url)

    entities = result.get("entities", [])
    facts = result.get("facts", [])

    n_entities = write_entities(neo4j_session, entities)
    n_facts = write_facts(neo4j_session, facts, session_id)
    n_embedded = embed_facts(facts, session_id)

    return {
        "session_id": session_id,
        "entities": n_entities,
        "facts": n_facts,
        "embedded": n_embedded,
    }
