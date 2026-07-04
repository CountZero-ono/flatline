# naima.md
version: 7
updated: 2026-07-05 AZT

Architect's spec. Naima (Claude) writes this; F.B. commits it; Dixie reads it at session start. This overrides Dixie's own judgment on architectural questions. If something here conflicts with AGENTS.md on a behavioral rule, AGENTS.md wins for mechanics — this file is for design decisions and standing instructions, not session command syntax.

---

## Git hygiene (2026-06-21)

Sign-off is for the memory pipeline only — extraction, crystallization, briefing generation. It is not a proxy for git safety.

**Rule:** Commit code changes as you make them, not when signing off. Small, scoped commits, descriptive messages, normal dev hygiene. Don't batch a session's worth of edits into one commit at sign-off time.

Background: `_git_commit_handoff_files()` previously only fired inside the sign-off flow, meaning any code edits made in a session with no sign-off sat uncommitted indefinitely. This is now considered a bug in process, not a tooling gap to patch with a timer.

**Addendum (2026-06-21): push immediately after every commit.** Found in practice on 2026-06-21 — several commits sat local-only for 2+ weeks because nobody ran `git push` after committing. The commits were fine; the push step was just never run. Run `git push` as the last step of every commit, automatically, without being asked. Verify it landed — `git log --oneline -1 origin/main` should match local HEAD — before considering any task done.

---

## GAP chain — CONFIRMED FIXED (2026-07-05)

Closing this out. v2 flagged `neither_worked()` for missing its `mark_gap()` call. v6 noted it *looked* fixed but explicitly withheld confirmation pending a check against a fresh, unshallowed `origin/main` clone rather than a pasted file.

That check happened today. Pulled `flatline_l1_session.py` and `flatline_l1_lifecycle.py` fresh from `origin/main`, not from context. Confirmed:

- Both `still_broken()` and `neither_worked()` route through the shared `_to_gap()` helper on both observations.
- `_to_gap()` promotes CANDIDATE→ACTIVE where needed, then calls `mark_gap()`.
- The state machine's `TRANSITIONS` dict genuinely permits `ACTIVE → GAP` — this isn't a call quietly failing against a disallowed transition, it fires for real.

Item closed. `run_gap_handler()` auto-triggering remains a separate, still-unapproved decision, unchanged from v2 — that's not what this closes.

**New, narrower item opened by this verification:** `mark_gap()` (via `transition()`) raises `ValueError` if called on an observation already in `GAP` status, since `TRANSITIONS['GAP']` only permits `GAP → ACTIVE`. This would surface if a single observation is party to two separate open contradiction flags simultaneously — resolving the second flag would crash rather than no-op gracefully. Narrow edge case, not yet hit in practice, not blocking anything. Logged here so it doesn't get rediscovered from scratch; not yet approved for a fix.

---

## Knowledge base ingestion — schema (2026-06-30, status: APPROVED, build in progress)

**Gate lifted 2026-06-30.** F.B. gave explicit go-ahead to begin. This supersedes the "do not start building" hold from v2.

This applies to the `llm-wiki` / knowledge-base layer (books, PDFs, EPUBs, Obsidian clippings, plus library content staged through Open Notebook — see Ingestion Tooling below).

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

**Chunking principle — REVISED 2026-06-30 (replaces v2's "technique not procedure" rule).**

v2 drew the line as binary: extract technique-level knowledge, reject procedural recipe steps. That rule doesn't survive contact with real material — some procedural-looking content (fermentation salt ratios, brine percentages) actually does corroborate cleanly across sources because it reduces to a small number of discrete, source-independent parameters. Other content (a specific recipe's step sequence) genuinely doesn't generalize and shouldn't be forced into a "principle."

**The real test is not technique-vs-procedure. It's: does this content express a generalizable parameter — a rate, ratio, percentage, or formula — that would converge across multiple independent sources?**

- **If yes:** extract the parameter itself, normalized, independent of how the source phrased it. "2% salt by weight," "use 20g salt per kg of vegetables," and "2g per 100g" are the same fact and must converge to the same KnowledgeNode via embedding similarity — phrase the extracted `content` in a normalized form (e.g. "vegetable lacto-fermentation: 2% salt by weight of vegetable mass") so embedding similarity actually catches the convergence rather than three near-duplicate nodes sitting unmerged. This is what lets Dixie answer "I have 500g carrots and 500g cabbage, what's the salt ratio" or "this apple juice is at 1.5% sugar, how much do I add" — it does the arithmetic against the stored rate at query time, not against a cached worked example.
- **If no:** store it as a paraphrased procedure in the same node type, same schema fields — there is no separate ProcedureNode, that idea was floated and explicitly rejected 2026-06-30. A multi-step, sequence-dependent recipe (e.g. a specific curry) doesn't reduce to a rate and shouldn't be force-fit into one; store it close to the source's actual sequence, still paraphrased per copyright handling, still tagged with `source_chunk_ref` so it's traceable.

One node type. The decision of "rate vs procedure" is made per-chunk at extraction time, not by routing into different schema structures.

**Ingestion order when this resumes:** Obsidian clippings first (already human-filtered, highest trust), then EPUBs, then PDFs last (most variable quality, OCR noise risk).

**Ingestion tooling (2026-06-30):** Open Notebook (self-hosted, MIT-licensed, `lfnovo/open-notebook`) is the approved extraction workbench for staging library content before it hits the ingestion pipeline. Point its model provider at Dixie's existing llama-server endpoint (port 1235) rather than a cloud provider — this keeps the no-cloud-runtime commitment intact, since Open Notebook supports local model backends via the same Ollama-compatible interface. Its REST API (localhost:5055) makes this scriptable rather than manual-click-through, unlike NotebookLM (no public API, cloud-only, evaluated and rejected 2026-06-30 for this reason). Open Notebook itself is **not** part of the runtime system — it is prep tooling. Its job ends at producing structured extracts (flat JSON/CSV matching the KnowledgeNode fields above); those extracts land in the repo as files, and the actual ingestion script reads from disk like any other source. Do not wire Open Notebook into the live ingestion path as a standing dependency.

**Open, not yet decided:** hot-folder watcher mechanics, batching size/throttle, whether `ingest_document` (existing raw chunker in flatline_mcp_server.py) gets extended or replaced outright. Do not assume the existing `ingest_document` tool is reusable as-is — it currently has no schema awareness, no dedup, no graph write. Treat it as a reference implementation for the chunking/OCR plumbing only.
