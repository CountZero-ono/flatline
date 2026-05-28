#!/usr/bin/env python3
"""
extract_decisions.py

Reads a directory of claude-exporter JSON files, extracts architectural
decisions and reasoning from assistant turns, writes flatline_decisions.md.

Usage:
    python extract_decisions.py /path/to/json/folder /path/to/output.md

The summary field at the top of each JSON is prioritized — it's pre-digested
and usually contains the most useful high-level context. Assistant turns are
then scanned for reasoning-dense paragraphs.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# --- Keywords that signal reasoning / decisions, not just instructions ---
DECISION_KEYWORDS = [
    "because", "reason", "decided", "instead", "tradeoff", "trade-off",
    "why", "approach", "architecture", "design", "chose", "choice",
    "problem", "issue", "workaround", "alternative", "caveat", "limitation",
    "warning", "important", "note that", "key point", "discovered",
    "turns out", "the trick", "gotcha", "failed", "doesn't work",
    "won't work", "avoid", "prefer", "recommend", "best practice",
]

# --- Flatline-specific terms to always capture ---
FLATLINE_TERMS = [
    "flatline", "trumem", "memmachine", "neo4j", "qdrant", "postgres",
    "crystallizer", "decay", "l1", "l2", "l3", "mcp", "opencode",
    "dixie", "naima", "signing_off", "sign_off", "session_close",
    "gap_handler", "searxng", "llama-server", "llama_server",
    "qwen", "granite", "embedding", "promote", "ingest",
    "flatline_l1", "flatline_l2", "flatline_l3", "flatline_mcp",
]


def is_relevant(text: str) -> bool:
    t = text.lower()
    for term in FLATLINE_TERMS:
        if term in t:
            return True
    for kw in DECISION_KEYWORDS:
        if kw in t:
            return True
    return False


def extract_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs, skip pure code blocks and very short lines."""
    paragraphs = []
    in_code_block = False
    current = []

    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        if line.strip() == "":
            if current:
                para = " ".join(current).strip()
                if len(para) > 80:  # skip one-liners
                    paragraphs.append(para)
                current = []
        else:
            current.append(line.strip())

    if current:
        para = " ".join(current).strip()
        if len(para) > 80:
            paragraphs.append(para)

    return paragraphs


def process_file(filepath: Path) -> dict | None:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  Skipping {filepath.name}: {e}", file=sys.stderr)
        return None

    name = data.get("name", filepath.stem)
    created_at = data.get("created_at", "")
    summary = data.get("summary", "")
    messages = data.get("chat_messages", [])

    date_str = ""
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            date_str = dt.strftime("%Y-%m-%d")
        except ValueError:
            date_str = created_at[:10]

    extracted = []

    # Summary is pre-digested — always include if it mentions Flatline terms
    if summary and is_relevant(summary):
        # Pull first 800 chars of summary as a note
        short = summary[:800].strip()
        if len(summary) > 800:
            short += "..."
        extracted.append(f"[SUMMARY] {short}")

    # Walk assistant turns, follow the main branch via parent_uuid
    # Build uuid->message map for branch traversal
    msg_map = {m["uuid"]: m for m in messages}
    leaf_uuid = data.get("current_leaf_message_uuid")

    # Collect main branch message uuids by walking back from leaf
    main_branch = set()
    cursor = leaf_uuid
    while cursor and cursor in msg_map:
        main_branch.add(cursor)
        cursor = msg_map[cursor].get("parent_message_uuid")

    for msg in messages:
        if msg.get("sender") != "assistant":
            continue
        if msg["uuid"] not in main_branch:
            continue

        for block in msg.get("content", []):
            if block.get("type") != "text":
                continue
            text = block.get("text", "")
            for para in extract_paragraphs(text):
                if is_relevant(para):
                    extracted.append(para)

    if not extracted:
        return None

    return {
        "name": name,
        "date": date_str,
        "points": extracted,
    }


def deduplicate(sessions: list[dict]) -> list[dict]:
    """Very light dedup — drop exact duplicate paragraphs, keep latest."""
    seen = set()
    for session in reversed(sessions):  # latest first
        unique = []
        for point in session["points"]:
            key = point[:120].lower()
            if key not in seen:
                seen.add(key)
                unique.append(point)
        session["points"] = unique
    return [s for s in sessions if s["points"]]


def main():
    if len(sys.argv) < 3:
        print("Usage: python extract_decisions.py <json_dir> <output.md>")
        sys.exit(1)

    json_dir = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    if not json_dir.is_dir():
        print(f"Not a directory: {json_dir}")
        sys.exit(1)

    json_files = sorted(json_dir.glob("*.json"))
    print(f"Found {len(json_files)} JSON files in {json_dir}")

    sessions = []
    for fp in json_files:
        print(f"  Processing: {fp.name}")
        result = process_file(fp)
        if result:
            sessions.append(result)
        else:
            print(f"    -> No relevant content found, skipping")

    sessions = deduplicate(sessions)
    print(f"\n{len(sessions)} sessions with relevant content")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# Flatline — Architectural Decisions & Reasoning\n\n")
        f.write(f"_Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}_\n\n")
        f.write("---\n\n")

        for session in sessions:
            f.write(f"## {session['name']} — {session['date']}\n\n")
            for point in session["points"]:
                # Trim long paragraphs to 400 chars
                display = point if len(point) <= 400 else point[:400] + "..."
                f.write(f"- {display}\n")
            f.write("\n")

    print(f"Written to: {output_path}")


if __name__ == "__main__":
    main()
