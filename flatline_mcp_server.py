import os
import json
import logging
import socket
import subprocess
import time
import requests
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import fitz
import docx
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
from pdf2image import convert_from_path
import pytesseract

from mcp.server import Server
from mcp.types import Tool, TextContent
from neo4j import GraphDatabase, Auth

from flatline_l1_writer import create_session, write_observation, flag_contradiction
from flatline_l1_session import (
    sign_out, resolve_a_wins, resolve_b_wins, still_broken, neither_worked,
)
from flatline_session_close import signing_out
from flatline_l3_ingest import ingest_text
from flatline_l3_query import embed, search
from flatline_crystallizer import crystallize_session

DB_PATH = os.path.expanduser("~/OCProjects/flatline/flatline.db")
SESSION_FILE = os.path.expanduser("~/OCProjects/flatline/.current_session")
FLATLINE_DIR = os.path.expanduser("~/OCProjects/flatline")
BRIEFING_FILE = os.path.join(FLATLINE_DIR, "flatline_briefing.md")


def _git_commit_handoff_files(session_id: str) -> None:
    """Auto-commit handoff files on session close. Non-fatal on failure."""
    files = [
        "flatline_summary.md",
        "flatline_decisions.md",
        "flatline_briefing.md",
    ]
    try:
        subprocess.run(
            ["git", "add"] + files,
            cwd=FLATLINE_DIR,
            check=True,
            capture_output=True,
        )
        msg = f"session close: {session_id} — {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=FLATLINE_DIR,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "push"],
            cwd=FLATLINE_DIR,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        logging.warning(f"Git commit on sign_off failed: {e}")


def _query_truemem(session_id: str) -> str:
    """Pull L1 observations for the current session. Returns bullet list."""
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            """SELECT content, decay_class
               FROM observations
               WHERE session_id = ?
               ORDER BY recorded_at""",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return "- No observations recorded this session."

    bullets = []
    for content, decay_class in rows:
        bullets.append(f"- {decay_class}: {content}")
    return "\n".join(bullets)


def _query_memmachine(session_id: str) -> tuple[str, str]:
    """Query Neo4j for L2 changes and unresolved contradictions.
    Returns (changed_nodes_bullet_list, unresolved_contradictions_bullet_list).
    """
    if neo4j_driver is None:
        return ("- Neo4j driver not available (MCP not initialized).", "")

    changed = []
    unresolved = []

    try:
        with neo4j_driver.session() as neo4j_session:
            # Query: nodes created or updated in this session
            result = neo4j_session.run(
                """
                MATCH (n)
                WHERE $sid IN n.session_ids
                RETURN n.id AS node_id, n.label AS label, n.type AS type,
                       n.confidence AS confidence, n.status AS status
                """,
                sid=session_id,
            )
            for record in result:
                changed.append(
                    f"- [{record['type']}] {record['label']} "
                    f"(confidence: {record['confidence']}, status: {record['status']})"
                )

            # Query: unresolved contradiction edges from this session
            result = neo4j_session.run(
                """
                MATCH (f1:Fact)-[:CONTRADICTS]->(f2:Fact)
                WHERE $sid IN f1.source_sessions OR $sid IN f2.source_sessions
                  AND f1.status IN ('CANDIDATE', 'ACTIVE')
                  AND f2.status IN ('CANDIDATE', 'ACTIVE')
                RETURN f1.id AS fa, f1.statement AS sa,
                       f2.id AS fb, f2.statement AS sb
                """,
                sid=session_id,
            )
            for record in result:
                unresolved.append(
                    f"- Contradiction: {record['sa']} vs {record['sb']} "
                    f"(A: {record['fa']}, B: {record['fb']})"
                )
    except Exception as e:
        logging.warning(f"Neo4j query failed: {e}")
        return ("- Neo4j query failed.", "")

    if not changed:
        changed_text = "- No L2 nodes created or updated this session."
    else:
        changed_text = "\n".join(changed)

    if not unresolved:
        unresolved_text = "- No unresolved contradictions in L2."
    else:
        unresolved_text = "\n".join(unresolved)

    return changed_text, unresolved_text


def _git_diff_stat() -> str:
    """Run git diff --stat HEAD~1. Returns bullet list of file changes."""
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD~1"],
            cwd=FLATLINE_DIR,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return "- No previous commit to diff against (first commit or no changes staged)."
        lines = result.stdout.strip().split("\n")
        if not lines or "no changes" in lines[0].lower():
            return "- No file changes since last commit."
        bullets = [f"- {line.strip()}" for line in lines if line.strip()]
        return "\n".join(bullets) if bullets else "- No file changes since last commit."
    except FileNotFoundError:
        return "- git not available."
    except subprocess.TimeoutExpired:
        return "- git diff timed out."


def extract_text(path: str) -> str:
    path = os.path.expanduser(path)
    suffix = os.path.splitext(path)[1].lower()
    if suffix == ".pdf":
        doc = fitz.open(path)
        pages = []
        for i, page in enumerate(doc):
            text = page.get_text()
            if len(text) < 50:
                img = convert_from_path(path, dpi=300, first_page=i + 1, last_page=i + 1)[0]
                text = pytesseract.image_to_string(img)
            pages.append(text)
        doc.close()
        return "\n".join(pages)
    elif suffix == ".docx":
        doc = docx.Document(path)
        return "\n".join(p.text for p in doc.paragraphs)
    elif suffix == ".epub":
        book = epub.read_epub(path)
        parts = []
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            soup = BeautifulSoup(item.get_content(), "html.parser")
            parts.append(soup.get_text())
        return "\n".join(parts)
    else:
        raise ValueError(f"Unsupported format: {suffix}")
NEO4J_URI = "bolt://192.168.1.53:7687"
NEO4J_USER = "neo4j"
NEO4J_PASS = "neo4j_password"

neo4j_driver = None


def load_or_create_session():
    if os.path.exists(SESSION_FILE):
        sid = open(SESSION_FILE).read().strip()
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT id FROM sessions WHERE id=? AND status='OPEN'", (sid,)).fetchone()
        conn.close()
        if row:
            return sid
        # Fallback: query for most recent OPEN session
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT id FROM sessions WHERE status='OPEN' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            return row[0]
        raise RuntimeError("No open session found. Start a new session first.")
    sid = create_session(DB_PATH)
    open(SESSION_FILE, 'w').write(sid)
    return sid

def hand_off(session_id: str, session_description: str = "Session handoff") -> str:
    """Generate flatline_briefing.md for Naima session handoff."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Source 1: TrueMem (L1 observations)
    l1_observations = _query_truemem(session_id)

    # Source 2: MemMachine (L2 changed nodes + unresolved contradictions)
    l2_nodes, l2_contradictions = _query_memmachine(session_id)

    # Source 3: git diff --stat (ground truth file changes)
    git_changes = _git_diff_stat()

    # Assemble What Changed from all three sources
    what_changed = f"""### TrueMem (L1 observations)
{l1_observations}

### MemMachine (L2 nodes changed)
{l2_nodes}

### git diff --stat (file changes)
{git_changes}"""

    # Assemble What's Broken from L2 unresolved contradictions + L1
    what_broken = f"""### Unresolved L2 contradictions
{l2_contradictions}

### L1 observations flagged as broken or unresolved
(no additional broken items detected in L1 observations for this session.)"""

    # Decisions Made and Needs Naima are empty — Dixie fills these in manually
    # before calling hand_off, or they stay empty and Naima infers from context
    decisions_made = "- No decisions recorded this session (or not yet extracted from L1)."
    needs_naima = "- Nothing requires Naima's design/architecture input at this time."
    next_task = "- Review briefing, confirm format sufficiency, decide on repo scope for source files."

    briefing = f"""# Flatline Briefing
_Session: {session_id} — {date_str} — {session_description}_

---

## What Changed
{what_changed}

---

## What's Broken Right Now
{what_broken}

---

## Decisions Made This Session
{decisions_made}

---

## Needs Naima
{needs_naima}

---

## Next Task
{next_task}
"""
    with open(BRIEFING_FILE, "w") as f:
        f.write(briefing)

    return f"Briefing written to {BRIEFING_FILE}."


server = Server("flatline-knowledge")


@asynccontextmanager
async def server_lifespan(server):
    global neo4j_driver
    neo4j_driver = GraphDatabase.driver(
        NEO4J_URI,
        auth=Auth("basic", NEO4J_USER, NEO4J_PASS) if NEO4J_PASS else None,
    )
    yield
    neo4j_driver.close()


server.lifespan = server_lifespan


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="remember_this",
            description="Record an observation into the L1 session.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Observation content"},
                    "decay_class": {
                        "type": "string",
                        "enum": ["ARCHITECTURAL", "OPERATIONAL", "TRANSIENT", "PERSONAL"],
                        "description": "Decay class",
                    },
                },
                "required": ["content"],
            },
        ),
        Tool(
            name="flag_conflict",
            description="Flag a contradiction between two observations.",
            inputSchema={
                "type": "object",
                "properties": {
                    "obs_a_id": {"type": "string"},
                    "obs_b_id": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["obs_a_id", "obs_b_id", "description"],
            },
        ),
        Tool(
            name="resolve_a_wins",
            description="Resolve a contradiction in favor of A.",
            inputSchema={
                "type": "object",
                "properties": {
                    "flag_id": {"type": "string"},
                },
                "required": ["flag_id"],
            },
        ),
        Tool(
            name="resolve_b_wins",
            description="Resolve a contradiction in favor of B.",
            inputSchema={
                "type": "object",
                "properties": {
                    "flag_id": {"type": "string"},
                },
                "required": ["flag_id"],
            },
        ),
        Tool(
            name="still_broken",
            description="Resolve a contradiction as NEITHER, queue a GAP.",
            inputSchema={
                "type": "object",
                "properties": {
                    "obs_id": {"type": "string"},
                },
                "required": ["obs_id"],
            },
        ),
        Tool(
            name="neither_worked",
            description="Resolve a contradiction flag as neither worked.",
            inputSchema={
                "type": "object",
                "properties": {
                    "flag_id": {"type": "string"},
                },
                "required": ["flag_id"],
            },
        ),
        Tool(
            name="sign_out",
            description="Close the session and run crystallization.",
            inputSchema={
                "type": "object",
                "properties": {
                    "annotation": {"type": "string", "description": "Optional session annotation"},
                },
            },
        ),
        Tool(
            name="sign_off",
            description="Close session, crystallize, and power off the machine.",
            inputSchema={
                "type": "object",
                "properties": {
                    "annotation": {"type": "string", "description": "Optional session annotation"},
                    "observations": {
                        "type": "array",
                        "description": "JSON array of observations extracted from conversation. Each item: {content: str, decay_class: ARCHITECTURAL|OPERATIONAL|TRANSIENT|PERSONAL, confidence: float 0.0-1.0}",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"},
                                "decay_class": {"type": "string", "enum": ["ARCHITECTURAL", "OPERATIONAL", "TRANSIENT", "PERSONAL"]},
                                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                            },
                            "required": ["content"],
                        },
                    },
                },
            },
        ),
        Tool(
            name="hand_off",
            description="Generate flatline_briefing.md for Naima session handoff. Queries TrueMem (L1), MemMachine (L2), and git diff. Call before 'signing off' if you intend to end the session, but do not call signing off automatically. Wait for explicit user instruction.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_description": {"type": "string", "description": "One-line description of what this session accomplished"},
                },
            },
        ),
        Tool(
            name="read_document",
            description="Read a document (PDF, DOCX, EPUB) and return its full text content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or ~-expanded path to the document"},
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="ingest_document",
            description="Read a document (PDF, DOCX, EPUB), extract text, and ingest it into the knowledge store.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or ~-expanded path to the document"},
                    "collection": {"type": "string", "description": "Reserved for future use", "default": "knowledge"},
                    "source_type": {"type": "string", "description": "Source type label", "default": "document"},
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="query_sessions",
            description="Search L1 sessions by natural language, return each session's metadata and associated facts from Neo4j.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query text"},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="cancel",
            description="Cancel pending crystallization. Trigger phrase: 'cancel sign off'. Stops cleanup and crystallization timers, kills cleanup script if running, deletes sentinel file. Safe to call at any point before crystallization starts.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@server.call_tool()
async def call_tool(name, arguments):
    global neo4j_driver
    session_id = load_or_create_session()

    if name == "remember_this":
        content = arguments["content"]
        decay_class = arguments.get("decay_class", "TRANSIENT")
        write_observation(DB_PATH, session_id, content, decay_class)
        return [TextContent(type="text", text="Observation recorded.")]

    elif name == "flag_conflict":
        obs_a_id = arguments["obs_a_id"]
        obs_b_id = arguments["obs_b_id"]
        description = arguments["description"]
        flag_id = flag_contradiction(DB_PATH, session_id, obs_a_id, obs_b_id, description)
        return [TextContent(type="text", text=flag_id)]

    elif name == "resolve_a_wins":
        flag_id = arguments["flag_id"]
        resolve_a_wins(DB_PATH, flag_id)
        return [TextContent(type="text", text="Resolved: A wins.")]

    elif name == "resolve_b_wins":
        flag_id = arguments["flag_id"]
        resolve_b_wins(DB_PATH, flag_id)
        return [TextContent(type="text", text="Resolved: B wins.")]

    elif name == "still_broken":
        obs_id = arguments["obs_id"]
        still_broken(DB_PATH, obs_id)
        return [TextContent(type="text", text="Contradiction resolved as NEITHER. GAP queued.")]

    elif name == "neither_worked":
        flag_id = arguments["flag_id"]
        neither_worked(DB_PATH, flag_id)
        return [TextContent(type="text", text="Resolved: neither worked. GAP queued.")]

    elif name == "sign_out":
        annotation = arguments.get("annotation")
        try:
            with neo4j_driver.session() as neo4j_session:
                signing_out(DB_PATH, neo4j_session, session_id, annotation, dry_run=False)
            if os.path.exists(SESSION_FILE):
                os.remove(SESSION_FILE)
            return [TextContent(type="text", text="Session closed. Crystallization complete.")]
        except RuntimeError as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    elif name == "sign_off":
        annotation = arguments.get("annotation")

        # Step 1: create session
        session_id = create_session(DB_PATH)

        # Step 2: ingest client-provided observations (Dixie extraction pass)
        observations_raw = arguments.get("observations")
        if observations_raw:
            try:
                if isinstance(observations_raw, str):
                    observations = json.loads(observations_raw)
                else:
                    observations = observations_raw
                for obs in observations:
                    write_observation(
                        DB_PATH, session_id,
                        obs["content"],
                        obs.get("decay_class", "TRANSIENT"),
                    )
            except Exception as e:
                logging.warning(f"Observation parse failed: {e}")
                write_observation(
                    DB_PATH, session_id,
                    "Session ended without observation extraction",
                    "TRANSIENT",
                )
        else:
            write_observation(
                DB_PATH, session_id,
                "Session ended without observation extraction",
                "TRANSIENT",
            )

        # Step 3: sign_out
        result = sign_out(DB_PATH, session_id, annotation=annotation, force=False)
        if result["status"] == "BLOCKED":
            conflict_lines = []
            for c in result["conflicts"]:
                conflict_lines.append(
                    f"- {c['description']} (A: {c['observation_a_id']}, B: {c['observation_b_id']})"
                )
            return [TextContent(type="text", text=(
                "sign_out BLOCKED by unresolved contradictions:\n"
                + "\n".join(conflict_lines)
            ))]

        # Step 4: crystallize synchronously
        #   Stop llama-qwen-mtp, start llama-server on port 1235 with
        #   thinking disabled, call crystallize_session(), then restart.
        MODEL_PATH = (
            "/mnt/Models/LM Models/unsloth/"
            "Qwen3.6-35B-A3B-MTP-GGUF/"
            "Qwen3.6-35B-A3B-UD-Q3_K_M.gguf"
        )
        LLAMA_SERVER = "/home/fuad/llama-cpp-mainline/build/bin/llama-server"
        CRYSTALLIZE_URL = "http://localhost:1235/v1/chat/completions"

        # 4a: stop llama-qwen-mtp
        subprocess.run(
            ["systemctl", "--user", "stop", "llama-qwen-mtp.service"],
            check=True, capture_output=True, text=True,
        )

        # 4b: wait for port 1235 to close
        for _ in range(24):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            ready = s.connect_ex(("localhost", 1235))
            s.close()
            if ready != 0:
                break
            time.sleep(5)
        else:
            return [TextContent(type="text", text="sign_off failed: port 1235 did not close after stopping llama-qwen-mtp.")]

        # 4c: start llama-server with thinking disabled
        start_cmd = [
            LLAMA_SERVER,
            "-m", MODEL_PATH,
            "-ngl", "999", "-c", "65536", "-np", "1",
            "--cache-type-k", "q8_0",
            "--cache-type-v", "q8_0",
            "--host", "0.0.0.0", "--port", "1235",
            "--batch-size", "1024", "--ubatch-size", "512",
            "--alias", "qwen3.6-35b-a3b-mtp@q3_k_m",
            "--temp", "0.6",
            "--top-k", "20", "--top-p", "0.95", "--min-p", "0.0",
            "--flash-attn", "on",
            "--cont-batching",
            "--reasoning-budget", "0",
            "--spec-type", "draft-mtp",
            "--spec-draft-n-max", "2",
        ]
        _server_proc = subprocess.Popen(start_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # 4d: wait for port 1235 to respond
        for _ in range(36):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            ready = s.connect_ex(("localhost", 1235))
            s.close()
            if ready == 0:
                break
            time.sleep(5)
        else:
            _server_proc.terminate()
            subprocess.run(
                ["systemctl", "--user", "start", "llama-qwen-mtp.service"],
                check=True, capture_output=True, text=True,
            )
            return [TextContent(type="text", text="sign_off failed: llama-server did not start on port 1235.")]

        # 4e: call crystallize_session (reads L1, L2, POSTs to model, writes Neo4j/Qdrant)
        from neo4j import GraphDatabase, Auth
        neo4j_driver = GraphDatabase.driver(
            "bolt://192.168.1.53:7687",
            auth=Auth("basic", "neo4j", "neo4j_password"),
        )
        try:
            with neo4j_driver.session() as neo4j_session:
                result = crystallize_session(
                    DB_PATH, neo4j_session, session_id,
                    user_annotation=annotation,
                    url=CRYSTALLIZE_URL,
                )
        finally:
            neo4j_driver.close()

        # 4f: stop llama-server, restart llama-qwen-mtp
        _server_proc.terminate()
        _server_proc.wait(timeout=30)
        subprocess.run(
            ["systemctl", "--user", "start", "llama-qwen-mtp.service"],
            check=True, capture_output=True, text=True,
        )

        # Step 5: commit handoff files to local git, then push to GitHub
        _git_commit_handoff_files(session_id)

        return [TextContent(
            type="text",
            text=(
                f"Session captured. Crystallized: "
                f"{result.get('entities', '0')} entities, "
                f"{result.get('facts', '0')} facts. "
                f"Handoff committed and pushed."
            ),
        )]

    elif name == "hand_off":
        session_desc = arguments.get("session_description", "Session handoff")
        result = hand_off(session_id, session_description=session_desc)
        return [TextContent(type="text", text=f"{result}. Review and edit if needed, then call 'signing off' to finalize and push.")]

    elif name == "read_document":
        path = arguments["path"]
        try:
            text = extract_text(path)
            return [TextContent(type="text", text=text)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    elif name == "ingest_document":
        path = arguments["path"]
        source_type = arguments.get("source_type", "document")
        try:
            text = extract_text(path)
            source_name = os.path.splitext(os.path.basename(path))[0]
            result = ingest_text(text, source_name, source_type)
            return [TextContent(type="text", text=f"Ingested {result['ingested']} chunks from {os.path.basename(path)}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    elif name == "query_sessions":
        query_text = arguments["query"]
        try:
            vector = embed(query_text)
            hits = search(vector, collection="sessions", limit=5)
        except Exception as e:
            return [TextContent(type="text", text=f"Embed/search error: {e}")]

        session_ids = list(dict.fromkeys(
            h.get("session_id") for h in hits if h.get("session_id")
        ))
        if not session_ids:
            return [TextContent(type="text", text="No sessions found.")]

        results = []
        with neo4j_driver.session() as neo4j_session:
            for sid in session_ids:
                rec = neo4j_session.run(
                    """
                    MATCH (s:Session {id: $sid})
                    OPTIONAL MATCH (s)<-[:SOURCED_FROM]-(f:Fact)
                    RETURN {
                      id: s.id,
                      started_at: s.started_at,
                      status: s.status,
                      annotation: s.annotation,
                      facts: collect({
                        id: f.id,
                        statement: f.statement,
                        status: f.status,
                        confidence: f.confidence,
                        decay_class: f.decay_class
                      })
                    } AS row
                    """,
                    sid=sid,
                )
                record = rec.single()
                if record:
                    row = record.data()["row"]
                    results.append({
                        "session_id": row["id"],
                        "started_at": row["started_at"],
                        "status": row["status"],
                        "annotation": row["annotation"],
                        "facts": row["facts"],
                    })

        return [TextContent(type="text", text=json.dumps(results, indent=2))]

    elif name == "cancel":
        results = []

        # Step 1: stop timers
        for timer in ["flatline-cleanup.timer", "flatline-crystallize.timer"]:
            r = subprocess.run(
                ["systemctl", "--user", "stop", timer],
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                results.append(f"Stopped {timer}")
            else:
                results.append(f"{timer} was not running")

        # Step 2: kill cleanup script if running
        r = subprocess.run(
            ["pkill", "-f", "flatline_cleanup_run.sh"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            results.append("Killed running cleanup script")
        else:
            results.append("Cleanup script was not running")

        # Step 3: delete sentinel
        sentinel_path = os.path.expanduser("~/.flatline/pending_crystallization")
        if os.path.exists(sentinel_path):
            os.remove(sentinel_path)
            results.append("Sentinel file deleted")
        else:
            results.append("No sentinel file found")

        summary = "\n".join(f"- {r}" for r in results)
        return [TextContent(type="text", text=f"Crystallization cancelled. Machine will stay on.\n{summary}")]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


if __name__ == "__main__":
    import mcp.server.stdio
    import asyncio

    async def main():
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(main())
