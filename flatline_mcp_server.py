import os
import json
import logging
import subprocess
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
                },
            },
        ),
        Tool(
            name="hand_off",
            description="Generate flatline_briefing.md for Naima session handoff. Must be called before 'signing off'.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_description": {"type": "string", "description": "One-line description of what this session accomplished"},
                    "what_changed": {"type": "string", "description": "Bullet list of changes (fixed, added, changed)"},
                    "what_broken": {"type": "string", "description": "Bullet list of current bugs/blockers with what was tried and what wasn't"},
                    "decisions_made": {"type": "string", "description": "Bullet list of decisions made this session with rationale"},
                    "needs_naima": {"type": "string", "description": "What requires Naima's design/architecture/strategic thinking"},
                    "next_task": {"type": "string", "description": "Single next action to pick up at start of next session"},
                },
                "required": ["session_description", "what_changed", "what_broken", "decisions_made", "needs_naima", "next_task"],
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
    ]


@server.call_tool()
async def call_tool(name, arguments):
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
        try:
            sign_out(DB_PATH, session_id, annotation=annotation)
            if os.path.exists(SESSION_FILE):
                os.remove(SESSION_FILE)

            # Auto-commit handoff files to GitHub (non-fatal)
            _git_commit_handoff_files(session_id)

            # Write sentinel file for delayed crystallization
            sentinel_dir = os.path.expanduser("~/.flatline")
            os.makedirs(sentinel_dir, exist_ok=True)
            sentinel_path = os.path.join(sentinel_dir, "pending_crystallization")
            sentinel_data = {
                "session_id": session_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            with open(sentinel_path, "w") as f:
                json.dump(sentinel_data, f)

            # Reset the delayed crystallization timer
            subprocess.run(
                ["systemctl", "--user", "stop", "flatline-crystallize.timer"],
                capture_output=True,
            )
            subprocess.run(
                ["systemctl", "--user", "start", "flatline-crystallize.timer"],
                check=True,
                capture_output=True,
                text=True,
            )

            return [TextContent(type="text", text="Session closed. Crystallization scheduled. Machine powering off.")]
        except RuntimeError as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    elif name == "hand_off":
        session_desc = arguments.get("session_description", "")
        what_changed = arguments.get("what_changed", "")
        what_broken = arguments.get("what_broken", "")
        decisions_made = arguments.get("decisions_made", "")
        needs_naima = arguments.get("needs_naima", "")
        next_task = arguments.get("next_task", "")

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        briefing = f"""# Flatline Briefing
_Session: {session_id} — {date_str} — {session_desc}_

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

        return [TextContent(type="text", text=f"Briefing written to {BRIEFING_FILE}. Call 'signing off' to finalize.")]

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

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


if __name__ == "__main__":
    import mcp.server.stdio
    import asyncio

    async def main():
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(main())
