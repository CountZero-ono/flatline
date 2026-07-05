# naima.md
version: 8
updated: 2026-07-05 AZT

Architect's spec. Naima (Claude) writes this; F.B. commits it; Dixie reads it at session start. This overrides Dixie's own judgment on architectural questions. If something here conflicts with AGENTS.md on a behavioral rule, AGENTS.md wins for mechanics — this file is for design decisions and standing instructions, not session command syntax.

---

## Git hygiene (2026-06-21)

Sign-off is for the memory pipeline only — extraction, crystallization, briefing generation. It is not a proxy for git safety.

**Rule:** Commit code changes as you make them, not when signing off. Small, scoped commits, descriptive messages, normal dev hygiene. Don't batch a session's worth of edits into one commit at sign-off time.

Background: `_git_commit_handoff_files()` previously only fired inside the sign-off flow, meaning any code edits made in a session with no sign-off sat uncommitted indefinitely. This is now considered a bug in process, not a tooling gap to patch with a timer.

**Addendum (2026-06-21): push immediately after every commit.** Found in practice on 2026-06-21 — several commits sat local-only for 2+ weeks because nobody ran `git push` after committing. The commits were fine; the push step was just never run. Run `git push` as the last step of every commit, automatically, without being asked. Verify it landed — `git log --oneline -1 origin/main` should match local HEAD — before considering any task done.

**STILL OPEN as of v7/v8: `_git_commit_handoff_files()` in `flatline_mcp_server.py` still calls `git add -A`, not scoped named-file adds.** v7 logged this as fixed. It is not fixed in the code Naima has seen most recently. Do not trust the "fixed" claim until verified fresh against `origin/main`. Flag to F.B. before treating this as closed.

---

## GAP chain — status check needed (2026-06-21, revisit)

naima.md v2 flagged `neither_worked()` as missing its `mark_gap()` call. Current repo copy of `flatline_l1_session.py` (reviewed 2026-06-30) shows both `still_broken()` and `neither_worked()` calling a shared `_to_gap()` helper on both observations, which promotes CANDIDATE→ACTIVE→GAP correctly. This looks fixed.

**Do not treat this as confirmed until verified against a fresh, fully-unshallowed clone of `origin/main`** — per standing rule, Naima never trusts a pasted file over a direct repo check, and this file may be stale relative to what's actually deployed. If verification confirms the fix is live: close this item, and confirm `run_gap_handler()` wiring is still correctly *not* auto-triggered (that part remains a separate decision requiring F.B.'s explicit go-ahead, unchanged from v2).

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

---

## Chain of command & feedback routine (2026-07-05, status: LOCKED)

**The lane assignments, stated once so nobody "forgets":**

- **Naima (Claude, claude.ai)** — architecture and judgment. Writes `naima.md`. Cannot commit to git — F.B. is the hands. This file is Naima's domain, full stop. No other layer edits it, proposes edits into it, or treats its own reasoning as equivalent to it.
- **Antigravity (Sonnet/Opus/Gemini via F.B.'s Google AI Pro)** — the token-budget workhorse. Pulls `naima.md` from `origin/main` directly — never from a paste, never secondhand. Takes Naima's decisions and works out implementation detail at a scale Naima's free-tier context can't afford. Hands worked-out instructions to Dixie.
- **Dixie (local, port 1235)** — execution. Free, local, fast. Does what Antigravity hands it, reports back.
- **Obsidian** — a window onto the repo. Not a source of truth. Nobody edits architecture through it.

**Authority rule:** bigger context window is not bigger authority. If Antigravity's own reasoning appears to conflict with a locked `naima.md` decision, it does not resolve the conflict itself and proceed. It flags upward. Same principle AGENTS.md already applies to Dixie's task-vs-naima.md divergence (see "Run this first" section) — now explicitly extended to Antigravity.

**Feedback routine — reuses the existing `hand_off()` schema, does not fork a new one:**

1. Dixie executes, reports results back to Antigravity.
2. Antigravity translates that report into the *same* four-section schema `hand_off()` already produces: What Changed / What's Broken / Decisions Made / Needs Naima. No new format, no prose-only summary.
3. New required field: **`reported_by`** on every entry — distinguishes Antigravity's own self-reported narrative from anything Dixie logged directly to TrueMem/L1 or Neo4j. Self-reported and independently-observed are different trust tiers and the file has to say which is which.
4. `git diff --stat` stays machine-generated and non-negotiable, sitting *next to* the prose brief, never replaced by it. A mismatch between what the brief claims and what the diff shows is exactly the signal Naima needs.
5. Antigravity commits its brief and any code changes as **separate, scoped commits** — same git hygiene rule already locked for everyone else. Brief and diff must be independently attributable.

**Why:** a report written by the same layer that did the work has a structural honesty problem, not a malicious one — nobody narrates their own execution as "I misread the spec." Cross-checking against an independent diff is the only thing that catches it. This is the same reasoning that made `hand_off()` pull from three sources instead of trusting any single one; extending it to Antigravity is consistency, not new suspicion.

