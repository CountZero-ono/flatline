# naima.md
version: 2
updated: 2026-06-21 01:58 AZT

Architect's spec. Naima (Claude) writes this; F.B. commits it; Dixie reads it at session start. This overrides Dixie's own judgment on architectural questions. If something here conflicts with AGENTS.md on a behavioral rule, AGENTS.md wins for mechanics — this file is for design decisions and standing instructions, not session command syntax.

---

## Git hygiene (2026-06-21)

Sign-off is for the memory pipeline only — extraction, crystallization, briefing generation. It is not a proxy for git safety.

**Rule:** Commit code changes as you make them, not when signing off. Small, scoped commits, descriptive messages, normal dev hygiene. Don't batch a session's worth of edits into one commit at sign-off time.

Background: `_git_commit_handoff_files()` previously only fired inside the sign-off flow, meaning any code edits made in a session with no sign-off sat uncommitted indefinitely. This is now considered a bug in process, not a tooling gap to patch with a timer.

**Addendum (2026-06-21): push immediately after every commit.** Found in practice on 2026-06-21 — several commits sat local-only for 2+ weeks because nobody ran `git push` after committing. The commits were fine; the push step was just never run. Run `git push` as the last step of every commit, automatically, without being asked. Verify it landed — `git log --oneline -1 origin/main` should match local HEAD — before considering any task done.

---

## GAP chain — known bug (2026-06-21)

Confirmed: `neither_worked()` in `flatline_l1_session.py` resolves the contradiction flag (`resolve_contradiction(..., 'NEITHER')`) but never calls `mark_gap()` on either observation. Per the original spec (Section 04), "neither worked" should transition both observations to INVALIDATED and open a GAP — that second half never happens in code. `run_gap_handler()` in `flatline_gap_handler.py` is fully built and otherwise ready, but has never fired because no GAP has ever actually been created.

The `neither_worked` MCP handler's response text ("Resolved: neither worked. GAP queued.") is currently false — nothing gets queued. Fix this alongside the `mark_gap()` call so the message matches reality.

If this is still unfixed when you read this: fixing it is approved, already-decided work — go ahead, don't wait for confirmation. Test by creating a fake contradiction, resolving as neither-worked, and confirming both observations end up with `status = 'GAP'` in the database. Wiring `run_gap_handler()` to actually fire on a schedule/trigger is a separate decision — do not do that part without F.B.'s explicit go-ahead.

---

## Knowledge base ingestion — schema (2026-06-21, status: DESIGN LOCKED, not yet built)

This applies to the upcoming `llm-wiki` / knowledge-base layer (books, PDFs, EPUBs, Obsidian clippings). Do not start building ingestion against this schema until F.B. explicitly says to proceed — as of this writing we are still clearing other pendings first.

**KnowledgeNode is not a session Observation.** Separate label in Neo4j, same database (not a separate DB — we want graph relationships between session memory and knowledge nodes, splitting databases loses that).

```
KnowledgeNode {
  id: UUID
  content: string          # the fact itself, one clear statement
  source_title: string      # book/doc title
  source_chunk_ref: string  # chapter/page/offset — enough to relocate in source
  decay_class: ARCHITECTURAL | OPERATIONAL | TRANSIENT | PERSONAL
  confidence: float
  status: CANDIDATE | ACTIVE | VALIDATED | INVALIDATED | SUPERSEDED | DECAYED
  ingested_at: epoch
  embedding_id: string      # Qdrant vector ID, for fast neighbor lookup
  corroboration_count: int  # starts at 1, increments on dedup match
}
```

Lifecycle is the same state machine as session observations (see AGENTS.md). A fact from an old or low-quality source can be superseded by a better one like anything else — no special protection just because it came from a book.

**Dedup strategy: embedding similarity, not content hash.** Content hash misses paraphrases ("2% salt by weight" vs "use 2% salt") which is exactly the case that matters for multi-source ingestion. Threshold: 0.92 cosine similarity. Below → new KnowledgeNode. At or above → do not create a new node; increment `corroboration_count` on the existing node, add the new source to its source list, and let confidence rise accordingly.

**Relationship:** `CORROBORATES` — points from a source/chunk to the KnowledgeNode it confirms. This is the conflict-resolution primitive: three books agreeing converges to one node with three corroborating sources and higher confidence, not three duplicate nodes.

**Chunking principle:** extract durable technique-level knowledge, not procedural recipe steps. Example (fermentation domain): "lacto-fermentation requires sufficient salt concentration to suppress pathogenic bacteria" is a correct chunk. "Add 2% salt by weight to shredded cabbage" is not — too procedural, too source-specific, won't corroborate well across sources.

**Ingestion order when this resumes:** Obsidian clippings first (already human-filtered, highest trust), then EPUBs, then PDFs last (most variable quality, OCR noise risk).

**Open, not yet decided:** hot-folder watcher mechanics, batching size/throttle, whether `ingest_document` (existing raw chunker in flatline_mcp_server.py) gets extended or replaced outright. Do not assume the existing `ingest_document` tool is reusable as-is — it currently has no schema awareness, no dedup, no graph write. Treat it as a reference implementation for the chunking/OCR plumbing only.
