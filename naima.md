# naima.md
version: 5
updated: 2026-06-30 02:50 AZT

Architect's spec. Naima (Claude) writes this; F.B. commits it; Dixie reads it at session start. This overrides Dixie's own judgment on architectural questions. If something here conflicts with AGENTS.md on a behavioral rule, AGENTS.md wins for mechanics — this file is for design decisions and standing instructions, not session command syntax.

---

## Model quant: IQ3_S (2026-06-30)

Dixie's primary inference model switched from Q3_K_M to **IQ3_S** on 2026-06-30. F.B.'s explicit decision — lower UMA pressure, fewer memory spills, faster.

**Benchmark results (spec-draft-n-max=2, Qwen3.6 35B A3B MTP):**

| Run | Prompt source | Draft acceptance | Throughput |
|-----|--------------|-----------------|------------|
| IQ3_S, n=2 | synthetic (n=10) | 79.2% | 33.7 tok/s |
| IQ3_S, n=2 | real session content (n=15) | 80.1% | 33.8 tok/s |
| Q3_K_M (prior baseline) | — | 83% | ~30 tok/s |

Net: -3pts draft acceptance, +13% throughput. Two independent prompt sources landed within 1 point of each other — consistent, not noise.

Update `flatline_summary.md` to reflect IQ3_S everywhere Q3_K_M appears in the infrastructure table, two-model strategy table, and decisions table. The VALIDATED MTP decision row stays — the decision was `spec-type draft-mtp, n=2`, which is unchanged. Only the quant designation changes.

**Note:** spec-draft-n-max was drifted to 3 at some point before this benchmark — the n=2 override restored the correct setting and is what produced the final numbers above. Confirm the service override is locked to n=2.

---

## Git hygiene (2026-06-21)

Sign-off is for the memory pipeline only — extraction, crystallization, briefing generation. It is not a proxy for git safety.

**Rule:** Commit code changes as you make them, not when signing off. Small, scoped commits, descriptive messages, normal dev hygiene. Don't batch a session's worth of edits into one commit at sign-off time.

Background: `_git_commit_handoff_files()` previously only fired inside the sign-off flow, meaning any code edits made in a session with no sign-off sat uncommitted indefinitely. This is now considered a bug in process, not a tooling gap to patch with a timer.

**Addendum (2026-06-21): push immediately after every commit.** Found in practice on 2026-06-21 — several commits sat local-only for 2+ weeks because nobody ran `git push` after committing. The commits were fine; the push step was just never run. Run `git push` as the last step of every commit, automatically, without being asked. Verify it landed — `git log --oneline -1 origin/main` should match local HEAD — before considering any task done.

---

## GAP chain — RESOLVED (2026-06-22)

Status update: this is fixed and committed, not just a future task. Verified via `git diff HEAD -- flatline_l1_session.py` returning empty — the working tree matches HEAD exactly.

`neither_worked()` and `still_broken()` in `flatline_l1_session.py` both call `_to_gap()` on both observations after resolving the contradiction flag. `_to_gap()` promotes CANDIDATE→ACTIVE first if needed (CANDIDATE→GAP isn't a legal lifecycle transition), then calls `mark_gap()`. The MCP handler's response text for `neither_worked` was also corrected to accurately say "transitioned to GAP" instead of the old false "GAP queued" line.

Do not re-attempt this fix. If a future session sees old context suggesting this is still broken (stale briefings, old chat history, etc.), check current file state first — this entry is the ground truth as of 2026-06-22.

`run_gap_handler()` in `flatline_gap_handler.py` is still orphaned — fully built, zero callers, otherwise ready to run now that GAP facts can actually be created. Wiring it to fire on a schedule/trigger remains a separate decision — do not do that part without F.B.'s explicit go-ahead.

---

## Knowledge base ingestion — BUILD APPROVED (2026-06-25)

F.B. gave explicit go-ahead on 2026-06-25. All prior housekeeping pendings (git auto-push fix, dead code removal, GAP chain resolution, Neo4j health, spec doc v0.2) are clear. This applies to the `llm-wiki` / knowledge-base layer (books, PDFs, EPUBs, Obsidian clippings). Schema below is locked. Four previously-open implementation questions are now decided — see "Build decisions" below. Proceed with Phase 1 (Obsidian only) per that section.

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

**Build decisions (2026-06-25) — resolves the four items previously left open:**

1. **Qdrant collection: stay in `flatline`, don't fragment.** `flatline_l3_query.py` currently hardcodes a single collection (`COLLECTION_NAME = "flatline"`) — `flatline_summary.md`'s description of separate `knowledge`/`sessions` Qdrant collections was never actually built and should be treated as aspirational, not current state. Same call here as the Neo4j label decision: one collection, add a `node_type` payload field (`KNOWLEDGE_NODE` for KB entries). Dedup search = embed candidate chunk → query `flatline` collection filtered on `node_type: KNOWLEDGE_NODE` → compare top hit to 0.92 cosine threshold. Do not create a second Qdrant collection for this.

2. **`ingest_document` stays untouched. Build new.** It has existing callers and intentionally has no schema awareness — don't retrofit dedup/graph-write logic onto it. New module: `flatline_kb_ingest.py`. Reuse `extract_text()` (flatline_mcp_server.py) and `chunk_text()` (flatline_l3_ingest.py) as plumbing only, per the existing note that they're reference implementations, not reusable as-is.

3. **Hot-folder watcher: deferred, not Phase 1.** Phase 1 is a manual/explicit trigger (a new MCP tool or session command, Dixie's call on naming) that ingests a given path on demand. No filesystem watcher yet — that's a Phase 2 decision, don't build it now.

4. **Batching/throttle: none needed yet.** Slots into the existing trigger priority queue (one job at a time, no concurrent Neo4j writes — already a hard rule elsewhere in this doc). Synchronous per-chunk embedding calls are fine at current corpus scale. Revisit only if real ingestion proves slow.

**Phase 1 scope: Obsidian only.** Point the new ingestion path at the Obsidian vault (plain markdown on disk, already human-filtered, highest trust per the existing ingestion-order decision). EPUBs and PDFs come later, in that order — don't build all three source types at once.

---

## Knowledge base scope — ONE knowledge base, not two (2026-06-25)

Resolving a scope question before Dixie starts: "feed it books/manuals" and "a knowledge base of problems solved with Dixie" are not two separate systems. Do not build two.

- **Single store.** One Neo4j database, one Qdrant collection (per the build decisions above) — `KnowledgeNode` (books/manuals) and `Fact` (session-derived, already existing) are distinct labels/types in the *same* graph, filterable apart, joinable together. The whole point of keeping KnowledgeNode in the same database as the Fact graph is so a manual's claim and a hard-won session result can `CONTRADICTS`/`CORROBORATES` each other directly. Splitting into two KBs throws that away.
- **"Solved with Dixie" is not a new build.** That KB already exists and is already running — it's the Fact graph, populated every sign-off by the crystallization pipeline. See the "Key Decisions & Rationale" table in `flatline_summary.md` for a hand-maintained snapshot of what's already in there (Vulkan-over-ROCm, 16GB UMA, etc.) The graph behind that table is live today. Nothing needs building for this half.
- **The only new build approved here is books/manuals ingestion** — `KnowledgeNode`, Phase 1 = Obsidian only, per "Build decisions" above.
- If a future session (Dixie or otherwise) is asked to build "a KB of solved problems," the correct response is: it exists, query the Fact graph — do not scaffold a parallel system. Improving *how* that table gets generated (auto-pull from the graph instead of hand-maintained) is a real future task but is explicitly out of scope right now.
