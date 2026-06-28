"""
flatline_kb_ingest.py — Phase 1: Obsidian knowledge base ingestion.

Ingests durable knowledge from Obsidian markdown files (Kitchen + Clippings),
deduplicates via Qdrant embedding similarity, and merges cross-source
matches into existing KnowledgeNode entries (corroboration_count, sources,
confidence) rather than creating duplicate nodes.

Does NOT modify existing modules. Standalone.
"""

import hashlib
import logging
import os
import re
import time
import uuid
from pathlib import Path

import requests
from neo4j import GraphDatabase, Auth

from flatline_l3_query import embed, ensure_collection

logger = logging.getLogger(__name__)

# ---------- Qdrant / embedding config ----------

QDRANT_URL = "http://192.168.1.44:6333"
COLLECTION_NAME = "flatline"
EMBEDDING_URL = "http://192.168.1.112:1236/v1/embeddings"
EMBEDDING_MODEL = "granite-embed-97m"

DEDUP_THRESHOLD = 0.92
MIN_CHUNK_LENGTH = 40  # skip sub-threshold chunks

# ---------- Neo4j config ----------

NEO4J_URI = "bolt://192.168.1.53:7687"
NEO4J_USER = "neo4j"
NEO4J_PASS = "neo4j_password"

# ---------- Obsidian vault config ----------

VAULT_PATHS = ("Kitchen", "Clippings")  # subdirectories to ingest


# ── Frontmatter parsing ──────────────────────────────────────────────────

def parse_frontmatter(text):
    """Extract YAML frontmatter from Obsidian markdown.

    Returns (metadata_dict, body_text).  If no frontmatter, metadata is {}
    and body is the full text.
    """
    body = text
    metadata = {}

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]
            fm_text = parts[1].strip()
            metadata = _parse_yaml_block(fm_text)

    return metadata, body


def _parse_yaml_block(fm_text):
    """Minimal YAML parser for Obsidian frontmatter.

    Handles:
      - key: "value"  (scalar)
      - key:\n  - "item"  (list)
      - key: value  (scalar no quotes)
    Does NOT handle nested dicts.
    """
    metadata = {}
    current_key = None
    current_list = None

    for line in fm_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped == "---":
            continue

        # List item: "  - \"value\""
        if stripped.startswith("- ") and current_key:
            val = stripped[2:].strip().strip('"').strip("'")
            current_list.append(val)
            continue

        # Key: value
        m = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*):\s*(.*)', stripped)
        if m:
            key = m.group(1)
            val = m.group(2).strip().strip('"').strip("'")

            if val == "" or val is None:
                # Start of a list (items on following lines)
                current_key = key
                current_list = []
                metadata[key] = current_list
            else:
                current_key = None
                current_list = None
                metadata[key] = val

    return metadata


# ── Durable chunking ─────────────────────────────────────────────────────

# Patterns that mark content to skip entirely.
_SKIP_PATTERNS = [
    r'^By\s+',                            # bylines
    r'^Reader\s+Rating',                  # recipe card headers
    r'^Total\s+Time',                     # recipe card metadata
    r'^Servings\s',                      # recipe card metadata
    r'^This\s+post\s+may\s+contain',     # affiliate disclaimers
    r'^Add\s+more\s+freshness',          # "like this? leave a comment"
    r'^Side\s+Dishes',                   # recipe suggestions
    r'^Salads$',                         # recipe suggestions
    r'^Recipe\s+Card',                   # recipe card header
    r'^###\s*Video$',                    # video section
    r'^###\s*Ingredients$',              # ingredient lists (h3)
    r'^###\s*Equipment$',                # equipment lists (h3)
    r'^###\s*Instructions$',             # instructions (h3)
    r'^###\s*Notes$',                    # notes (h3)
    r'^###\s*Nutrition$',                # nutrition (h3)
    r'^Like\s+this\?',                   # engagement prompts
    r'^Delicious\s+',                    # tagline/footer
    r'^\s*$',                            # blank lines
    r'^Serving:',                        # nutrition line
    r'^-\s*\[Asian|Pickled|Korean|Crispy|Steamed|Din',  # recipe suggestion links
    r'^Jump\s+to\s',                    # "Jump to Recipe" link text
    r'not\s+a\s+canning\s+recipe',     # "This is not a canning recipe"
    r"i'm\s+not\s+an\s+expert",         # personal disclaimer
]

# Patterns that mark procedural / step content.
_STEP_PATTERNS = [
    r'^#\s*\d+\.\s',              # "1. ", "2. " numbered steps
    r'^(Blanch|Simmer|Transfer|Cool|Pack|Store|Add|Use|Boil|Drain|Pat|Pour|Let|Place|Bring|Combine|Remove)\b',  # imperative verbs
]

# Patterns that indicate a paragraph is a pure link list (no real content).
_LINK_LIST_PATTERN = re.compile(
    r'^-\s*\[.*?\]\(.*?\)\s*$',  # markdown links as bullet items
    re.MULTILINE
)


def _is_skip_paragraph(para):
    """Check if a paragraph should be entirely skipped."""
    for pat in _SKIP_PATTERNS:
        if re.search(pat, para, re.IGNORECASE):
            return True
    # Pure link list: every non-empty line is a markdown link bullet
    lines = [l.strip() for l in para.splitlines() if l.strip()]
    if lines and all(_LINK_LIST_PATTERN.match(l) for l in lines):
        return True
    # Bare URL lines
    if re.match(r'^https?://', para):
        return True
    # Ingredient list: mostly lines matching "number unit ingredient" pattern
    list_like_lines = [l for l in lines if l.startswith('-') or l[0].isdigit()]
    if list_like_lines and all(re.match(r'^-?\s*\d', l) for l in list_like_lines):
        return True
    return False


def is_procedural_paragraph(para):
    """Heuristic: does this paragraph read like a procedural step?"""
    lines = [l.strip() for l in para.splitlines() if l.strip()]
    if not lines:
        return False
    # If most lines start with imperative procedural verbs or numbered steps
    procedural_count = 0
    for line in lines:
        for pat in _STEP_PATTERNS:
            if re.match(pat, line):
                procedural_count += 1
                break
    # If >70% of content lines are procedural, skip the whole paragraph
    if len(lines) > 1 and procedural_count / len(lines) > 0.7:
        return True
    # Single-line procedural
    if len(lines) == 1:
        for pat in _STEP_PATTERNS:
            if re.match(pat, lines[0]):
                return True
    return False


def _is_purely_first_person(para):
    """Check if paragraph is mostly personal opinion, not durable knowledge."""
    words = para.split()
    personal_markers = ['I ', 'my ', 'me ', 'my ', 'I\'m', 'I ', 'my experience', 'I think', 'I like', 'I used', 'I was', 'I found']
    personal_count = sum(1 for m in personal_markers if m in para)
    return personal_count > 2


def extract_durable_chunks(body, source_title=""):
    """Extract durable, technique-level knowledge from Obsidian markdown body.

    Strategy:
      1. Split body into paragraphs (blank-line separated).
      2. Skip bylines, affiliate disclaimers, recipe cards, pure link lists.
      3. Skip procedural steps (numbered instructions, imperative verbs).
      4. Skip pure first-person opinion paragraphs.
      5. Keep paragraphs that explain *why*, describe properties, give tips,
         state facts about ingredients/techniques, or summarize.
      6. Merge adjacent durable paragraphs into chunks (max ~250 words).
      7. Each chunk gets a source_chunk_ref pointing back to its section.

    Returns list of dicts: {content, source_chunk_ref, decay_class}
    """
    paragraphs = re.split(r'\n{2,}', body)
    sections = _split_into_sections(paragraphs)

    chunks = []
    section_ref = ""
    durable_buf = []
    buf_words = 0
    buf_section = ""

    def _flush(buf, ref, section):
        """Flush buffer into a chunk if non-empty."""
        if not buf:
            return
        chunk_text = _clean_chunk(" ".join(buf))
        if len(chunk_text) >= MIN_CHUNK_LENGTH:
            chunks.append({
                "content": chunk_text,
                "source_chunk_ref": ref,
                "decay_class": _infer_decay_class(chunk_text),
            })

    for section_title, para_list in sections:
        section_ref = section_title or source_title

        # If section changed, flush previous buffer
        if buf_section and section_ref != buf_section:
            _flush(durable_buf, buf_section, buf_section)
            durable_buf = []
            buf_words = 0

        for para in para_list:
            para = para.strip()
            if not para or len(para) < MIN_CHUNK_LENGTH:
                continue

            # Strip image markdown: ![](url) or ![alt](url)
            para = re.sub(r'!\[.*?\]\(.*?\)', '', para).strip()

            # Strip image alt text descriptions: "Image shows..."
            para = re.sub(r'Image\s+shows\s+[^.!?]+[.!?]\s*', '', para).strip()

            # Skip patterns
            if _is_skip_paragraph(para):
                continue

            # Skip procedural paragraphs
            if is_procedural_paragraph(para):
                continue

            # Skip purely personal paragraphs
            if _is_purely_first_person(para):
                continue

            # Strip markdown links but keep the text: [text](url) -> text
            para = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', para)

            # Durable paragraph — accumulate
            durable_buf.append(para)
            buf_words += len(para.split())

            if buf_words >= 250:
                _flush(durable_buf, section_ref, section_ref)
                durable_buf = []
                buf_words = 0

        buf_section = section_ref

    # Flush remaining
    _flush(durable_buf, section_ref, buf_section)

    return chunks


def _clean_chunk(text):
    """Clean up whitespace and redundant formatting in a chunk."""
    # Strip leading bullet markers from list items
    text = re.sub(r'^-\s+', '', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _split_into_sections(paragraphs):
    """Group paragraphs into sections by markdown heading markers.

    Returns list of (heading_title, [paragraphs]) tuples.
    """
    sections = []
    current_heading = ""
    current_paras = []

    for para in paragraphs:
        heading_match = re.match(r'^#{1,6}\s+(.+)', para.strip())
        if heading_match:
            if current_paras:
                sections.append((current_heading, current_paras))
            current_heading = heading_match.group(1).strip()
            current_paras = []
        else:
            current_paras.append(para)

    if current_paras:
        sections.append((current_heading, current_paras))

    return sections


# ── Decay class inference ────────────────────────────────────────────────

_DECAY_KEYWORDS = {
    "ARCHITECTURAL": {"architecture", "system", "infrastructure", "configuration", "protocol", "design", "framework", "database", "graph", "pipeline"},
    "OPERATIONAL": {"technique", "method", "process", "operation", "treatment", "ingredient", "chemical", "reaction", "preservation", "fermentation", "pickling", "brine", "sulfur", "enzyme", "acidic", "microbial", "spoilage", "moisture", "temperature", "shelf life", "storage", "shelf", "canning", "recipe", "cooking", "heat", "salt", "concentration", "osmotic", "pathogenic", "bacteria", "flavor", "flavors", "flavoring", "umami", "sauce", "dressing", "marinade", "vegetable", "vegetables", "fruit", "fruits", "grain", "grains", "protein", "carb", "fat", "calorie"},
    "PERSONAL": {"my experience", "in my opinion", "I believe", "personal preference", "my favorite", "I prefer"},
}


def _infer_decay_class(text):
    """Best-effort decay class from text content."""
    lower = text.lower()
    # Check PERSONAL first (most specific)
    if any(kw in lower for kw in _DECAY_KEYWORDS["PERSONAL"]):
        return "PERSONAL"
    # Check ARCHITECTURAL next
    if any(kw in lower for kw in _DECAY_KEYWORDS["ARCHITECTURAL"]):
        return "ARCHITECTURAL"
    # Check OPERATIONAL (broadest knowledge domain)
    if any(kw in lower for kw in _DECAY_KEYWORDS["OPERATIONAL"]):
        return "OPERATIONAL"
    return "OPERATIONAL"  # default for knowledge-base content


# ── Dedup (Qdrant) ───────────────────────────────────────────────────────

def _embed(text):
    """Embed text via the existing embedding endpoint."""
    return embed(text)


def search_existing_knowledge_nodes(query_vector, top_k=5):
    """Search Qdrant for existing KnowledgeNode entries.

    Filters on node_type: KNOWLEDGE_NODE payload field.
    Returns list of {id, score, content} for hits above threshold.
    """
    filter_payload = {
        "must": [
            {
                "key": "node_type",
                "match": {"value": "KNOWLEDGE_NODE"}
            }
        ]
    }
    resp = requests.post(
        f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points/search",
        json={
            "vector": query_vector,
            "limit": top_k,
            "with_payload": True,
            "filter": filter_payload,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        logger.warning("Qdrant search failed: %s", resp.status_code)
        return []

    results = []
    for hit in resp.json().get("result", []):
        payload = hit.get("payload", {})
        results.append({
            "id": hit.get("id"),
            "score": hit.get("score", 0.0),
            "content": payload.get("content", ""),
            "node_id": payload.get("node_id"),
        })
    return results


def find_duplicate_node(content, vector, top_k=3):
    """Check if content is a near-duplicate of an existing KnowledgeNode.

    Returns the matched node's id if any hit scores >= DEDUP_THRESHOLD,
    otherwise None. Returning the id (not just True/False) is what lets
    the caller corroborate the existing node instead of silently
    discarding the match.
    """
    hits = search_existing_knowledge_nodes(vector, top_k=top_k)
    for hit in hits:
        if hit["score"] >= DEDUP_THRESHOLD and hit.get("node_id"):
            return hit["node_id"]
    return None


# ── Neo4j write ──────────────────────────────────────────────────────────

def create_knowledge_node(session, node_id, content, source_title,
                           source_chunk_ref, decay_class, confidence,
                           embedding_id):
    """Create a brand-new KnowledgeNode.

    node_id is a freshly generated UUID (the schema's `id` field — not a
    content hash). Identity for dedup purposes lives in Qdrant embedding
    similarity (find_duplicate_node), not in this id, so the id can be a
    plain random UUID per the locked schema instead of a deterministic
    hash. `sources` starts as a one-element list; corroborate_knowledge_node
    appends to it on every later cross-source match.
    """
    now = int(time.time())
    session.run(
        """
        CREATE (n:KnowledgeNode {
          id: $node_id,
          content: $content,
          source_title: $source_title,
          source_chunk_ref: $source_chunk_ref,
          decay_class: $decay_class,
          confidence: $confidence,
          status: 'CANDIDATE',
          ingested_at: $now,
          embedding_id: $embedding_id,
          corroboration_count: 1,
          sources: [$source_descriptor]
        })
        """,
        node_id=node_id,
        content=content,
        source_title=source_title,
        source_chunk_ref=source_chunk_ref,
        decay_class=decay_class,
        confidence=confidence,
        now=now,
        embedding_id=embedding_id,
        source_descriptor=f"{source_title}::{source_chunk_ref}",
    )


def corroborate_knowledge_node(session, node_id, source_title, source_chunk_ref):
    """A new chunk matched an existing KnowledgeNode at/above DEDUP_THRESHOLD.

    Per spec: "do not create a new node; increment corroboration_count on
    the existing node, add the new source to its source list, and let
    confidence rise accordingly." This is that path — the one the old
    code never actually took.

    Confidence rises asymptotically toward 1.0 (diminishing return per
    corroborating hit) rather than the old `CASE WHEN $confidence >
    n.confidence` comparison, which could never fire because every
    ingest call passed the same hardcoded 0.8.

    Note: this updates the existing node's properties only. It does not
    create a graph relationship for the match. The spec's CORROBORATES
    relationship is described as pointing "from a source/chunk" to the
    node — there's no Source/Chunk node type in the locked schema to be
    the other endpoint of that edge. Introducing one is a real design
    decision (new label, new relationship semantics), not a bug fix —
    flagging for naima.md, not deciding it here.
    """
    session.run(
        """
        MATCH (n:KnowledgeNode {id: $node_id})
        SET n.corroboration_count = n.corroboration_count + 1,
            n.sources = coalesce(n.sources, []) + [$source_descriptor],
            n.confidence = n.confidence + (1.0 - n.confidence) * 0.1
        """,
        node_id=node_id,
        source_descriptor=f"{source_title}::{source_chunk_ref}",
    )


def link_to_facts(session, node_id):
    """Link KnowledgeNode -> Fact nodes that are semantically similar.

    Searches the Qdrant collection for high-similarity Fact entries
    and creates KNOWLEDGE_CORROBORATES relationships.
    """
    # This is a soft link — not critical for Phase 1
    pass


# ── File discovery ───────────────────────────────────────────────────────

def discover_vault_files(vault_path, subdirs=None):
    """Find all .md files in the specified vault subdirectories.

    Returns list of Path objects.
    """
    if subdirs is None:
        subdirs = VAULT_PATHS

    vault = Path(vault_path)
    files = []
    for subdir in subdirs:
        dir_path = vault / subdir
        if dir_path.is_dir():
            for md_file in sorted(dir_path.rglob("*.md")):
                files.append(md_file)
        else:
            logger.warning("Vault subdirectory not found: %s", dir_path)
    return files


# ── Qdrant payload upsert (for KnowledgeNode vectors) ────────────────────

def upsert_knowledge_node_vector(chunk_id, content, vector, metadata):
    """Upsert a single point to the flatline collection with
    node_type: KNOWLEDGE_NODE payload.

    Takes the embedding vector as a parameter instead of re-embedding —
    the caller already computed it for the dedup check in find_duplicate_node.
    Re-embedding here was a redundant network round-trip per chunk and
    opened a (small, theoretical) window for the dedup vector and the
    stored vector to diverge.
    """
    payload = {
        "collection_name": COLLECTION_NAME,
        "operations": [
            {
                "type": "upsert",
                "upsert": {
                    "points": [
                        {
                            "id": chunk_id,
                            "vector": vector,
                            "payload": {**metadata, "content": content, "node_type": "KNOWLEDGE_NODE"},
                        }
                    ]
                }
            }
        ]
    }
    resp = requests.post(
        f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points/batch",
        json=payload,
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Qdrant upsert failed: {resp.status_code} {resp.text}")


# ── Core ingestion pipeline ──────────────────────────────────────────────

def ingest_file(file_path, neo4j_session):
    """Ingest a single Obsidian markdown file.

    Returns dict with stats: {ingested, skipped, deduped, source}.
    """
    source_path = str(file_path)
    source_name = file_path.stem
    source_title = source_name

    # Read file
    text = Path(file_path).read_text(encoding="utf-8")

    # Parse frontmatter
    metadata, body = parse_frontmatter(text)
    if metadata.get("title"):
        source_title = metadata["title"]

    # Extract durable chunks
    chunks = extract_durable_chunks(body, source_title)
    if not chunks:
        return {"ingested": 0, "skipped": 0, "deduped": 0, "source": source_title}

    stats = {"ingested": 0, "skipped": 0, "deduped": 0, "source": source_title}

    for chunk in chunks:
        # Dedup check — this is the only identity check that matters.
        # (No more content-hash node_id: cross-source paraphrases need to
        # match here via embedding similarity, which a hash could never do.)
        try:
            vector = _embed(chunk["content"])
        except Exception as e:
            logger.warning("Embed failed for %s: %s", source_title, e)
            stats["skipped"] += 1
            continue

        matched_node_id = find_duplicate_node(chunk["content"], vector)

        if matched_node_id:
            try:
                corroborate_knowledge_node(
                    neo4j_session, matched_node_id,
                    source_title, chunk["source_chunk_ref"],
                )
                stats["deduped"] += 1
            except Exception as e:
                logger.error("Corroboration update failed for %s: %s", matched_node_id, e)
                stats["skipped"] += 1
            continue

        # No match — new node. id is a real UUID per the locked schema.
        node_id = str(uuid.uuid4())
        chunk_id = int(hashlib.md5(node_id.encode()).hexdigest()[:8], 16)

        try:
            create_knowledge_node(
                neo4j_session,
                node_id=node_id,
                content=chunk["content"],
                source_title=source_title,
                source_chunk_ref=chunk["source_chunk_ref"],
                decay_class=chunk["decay_class"],
                confidence=0.8,  # Phase 1 default for Obsidian content
                embedding_id=str(chunk_id),
            )

            # Upsert vector into Qdrant — same vector already used for
            # the dedup check above, not re-embedded.
            upsert_knowledge_node_vector(
                chunk_id,
                chunk["content"],
                vector,
                {
                    "node_id": node_id,
                    "source_title": source_title,
                    "source_chunk_ref": chunk["source_chunk_ref"],
                    "decay_class": chunk["decay_class"],
                    "confidence": 0.8,
                },
            )
            stats["ingested"] += 1
        except Exception as e:
            logger.error("Neo4j/Qdrant write failed for %s: %s", node_id, e)
            stats["skipped"] += 1

    return stats


def ingest_vault(vault_path, subdirs=None):
    """Ingest all Obsidian markdown files in the vault.

    Returns aggregated stats dict.
    """
    if subdirs is None:
        subdirs = VAULT_PATHS

    files = discover_vault_files(vault_path, subdirs)
    if not files:
        return {"error": "No files found", "sources": []}

    driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=Auth('basic', NEO4J_USER, NEO4J_PASS),
    )

    ensure_collection()

    total_stats = {"ingested": 0, "skipped": 0, "deduped": 0, "sources": []}

    try:
        with driver.session() as session:
            for file_path in files:
                stats = ingest_file(file_path, session)
                total_stats["ingested"] += stats["ingested"]
                total_stats["skipped"] += stats["skipped"]
                total_stats["deduped"] += stats["deduped"]
                total_stats["sources"].append({
                    "file": file_path.name,
                    "ingested": stats["ingested"],
                    "skipped": stats["skipped"],
                    "deduped": stats["deduped"],
                })
    finally:
        driver.close()

    return total_stats
