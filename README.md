# Flatline

Local-first AI memory and knowledge system. Solves one problem: every AI session starts with complete amnesia. Flatline extracts, structures, and permanently stores knowledge so future sessions can pick up where the last one left off.

The name is a William Gibson reference. The Dixie Flatline in *Neuromancer* was a ROM construct — a crystallized snapshot of a human mind, consulted for expertise, self-aware enough to know what it was. Flatline does the same thing for your sessions. It's also where memory *begins*: when a session ends, crystallization starts.

Runs entirely on a Beelink SER7 homelab box. No cloud dependency, by design.

---

## Core principles

- **Fully local.** No cloud runtime dependency. Forever yours.
- **Empirically honest.** The graph admits what it doesn't know. A GAP cannot be closed by inference — only by testing something and getting a result.
- **Self-maintaining.** No manual curation required for the memory pipeline itself.
- **Schema-constrained.** The schema is the intelligence multiplier, not the model.
- **Failure is data.** Every dead end is permanently queryable.

---

## Chain of command

Flatline isn't one agent — it's three, with a strict hierarchy:

| Agent | Role | Runs on |
|---|---|---|
| **Naima** | Strategic/architecture layer. Holds continuity, history, judgment. The only one who writes `naima.md` after real decisions. | Claude via claude.ai |
| **Antigravity** | Middle-layer teammate. Repo read access. Does **not** get write access to `sign_off` / `hand_off` / `cancel` until proven stable over weeks of real use. | Claude Sonnet/Opus + Gemini (Google AI Pro) |
| **Dixie** | Default daily coding workhorse. Reads `naima.md` at session start; it overrides Dixie's own judgment on architecture questions. | Local Qwen3.6-35B-A3B-MTP@IQ3_S, port 1235 |

**F.B. is the only one who commits and pushes.** Obsidian (synced via the Obsidian Git plugin) is a human-readable window onto the same repo — not a separate source of truth.

---

## Architecture: the three-layer memory pipeline

| Layer | Store | Role |
|---|---|---|
| **L1 — TrueMem** | SQLite | Short-term working memory. Ebbinghaus decay curve — fades unless reinforced. |
| **L2 — MemMachine** | Neo4j + Postgres | Long-term episodic memory. Typed facts and relationships, survives across sessions. |
| **L3 — Qdrant** | Vector store | Semantic archive. Never forgets, never prioritizes. Single `flatline` collection, filtered by `node_type` payload. |

### Fact lifecycle

```
CANDIDATE → ACTIVE → VALIDATED → INVALIDATED → SUPERSEDED → DECAYED → GAP
```

A GAP is a work order, not a dead end: it triggers L3 semantic search, then external search if L3 comes up empty. External facts start at lower confidence than session-derived ones. Only an empirical outcome — something tested and confirmed or refuted — closes a GAP.

### Crystallization

Triggered by `sign_off`. Runs in a background daemon thread: L1 observations + relevant L2 subgraph go to the crystallizer model (port 1235, same Qwen3.6 endpoint — no model swap), which extracts typed entities and facts, resolves contradictions, and writes the result to Neo4j (L2) and Qdrant (L3). Result lands in `~/.flatline/last_crystallization.json`. Machine stays on throughout — no poweroff.

---

## Command vocabulary

| Phrase | What happens |
|---|---|
| `hand off` | Generates `flatline_briefing.md` for Naima — queries TrueMem, MemMachine, and `git diff --stat`. Call before `signing off` if ending the session. |
| `signing off` | Creates a session, ingests Dixie-extracted observations, runs sign-out, crystallizes in the background, generates the handoff briefing. No prior session required. |
| `cancel sign off` | Stops any pending cleanup/crystallization timers, kills the cleanup script if running, deletes the sentinel. Machine stays on. |
| `still broken` / `neither worked` | Explicit GAP signal — marks the relevant observation(s)/contradiction as unresolved and queues external search. |

Full mechanics live in `AGENTS.md` (session command syntax, tool wiring) and `naima.md` (architecture decisions — overrides Dixie's judgment when the two disagree on design, though `AGENTS.md` wins on mechanics).

---

## Repo layout

```
flatline_l1_schema.sql       SQLite schema: sessions, observations, contradiction_flags, tasks, task_results
flatline_l1_writer.py        L1 CRUD
flatline_l1_lifecycle.py     Observation state machine
flatline_l1_session.py       sign_out / still_broken / neither_worked orchestration
flatline_l2_promote.py       L1 → MemMachine promotion
flatline_l3_ingest.py        Chunking + embedding into Qdrant
flatline_l3_query.py         Embed / search / upsert against Qdrant
flatline_crystallizer.py     Full L1 → LLM → Neo4j + Qdrant pipeline
flatline_gap_handler.py      GAP → L3 search → SearXNG fallback
flatline_decay_sweep.py      Time-based decay by decay_class
flatline_mcp_server.py       MCP server — all tool wiring (sign_off, hand_off, cancel, task relay, document ingestion)
flatline_kb_ingest.py        Knowledge-base ingestion (Obsidian vault → KnowledgeNode)
AGENTS.md                    Session command mechanics, memory rules, agent scoping
naima.md                     Architecture spec — versioned (currently v9), Naima writes it, Dixie reads it at session start
.antigravity.rules           Antigravity-specific boundaries (OKF auto-update, AGENTS.md scope note, chain-of-command reminder)
```

---

## Infrastructure

- **Neo4j + Postgres** (MemMachine, `192.168.1.53:8080`) — graph layer
- **Qdrant** (`192.168.1.44:6333`) — single `flatline` collection
- **llama-server** on SER7 — port 1235 (Dixie / Qwen3.6-35B-A3B-MTP@IQ3_S), port 1236 (granite-embedding), port 1237 (`llama-granite-micro.service`, Granite-4.0-H-Micro Q4_K_M — MemMachine's own LLM, confirmed via `/v1/models` and `systemctl`, not "fish-ai / shell generation" as an earlier OKF entry claimed)
- **Open Notebook** (Docker, UI `:8502`, API `:5055`) — extraction workbench for staging library content before ingestion, pointed at the local llama-server endpoint. Prep tooling only, not part of the runtime.
- **GitHub:** `CountZero-ono/flatline` (private). Prefer `raw.githubusercontent.com` over the GitHub API — the API rate-limits from shared sandbox IPs.

---

## Known open items (updated 2026-07-08, naima.md v9)

Most of the previous list closed out after a fresh verification pass against `origin/main` and live services. Current state:

**Closed:**
1. `_git_commit_handoff_files()` — confirmed using scoped `git add` on named files, not `-A`. Verified directly against `origin/main`.
2. GAP chain (`still_broken()` / `neither_worked()` → `_to_gap()` → `mark_gap()`) — confirmed firing correctly.
3. `run_gap_handler()` — confirmed still uncalled anywhere, correctly not auto-wired pending F.B.'s explicit go-ahead.
4. Stale `sign_out` MCP tool / `flatline_session_close.py` (retired 27B-swap dead code) — confirmed deleted. `dixie_housekeeping_task.md` removed as a result — nothing left in it to track.
5. `AGENTS_addition.md` — deleted rather than patched. It did contain a real bug (told Dixie to write `naima_md_version` via a literal "remember this" phrase, which collides with `AGENTS.md`'s hard rule), but nothing in the repo actually loaded the file — it duplicated `AGENTS.md`'s own startup section, which already had the correct instruction. Orphaned scaffolding, not a live doc.
6. Port 1237's role — confirmed via `curl localhost:1237/v1/models` + `systemctl --user list-units`: it's `llama-granite-micro.service`, Granite-4.0-H-Micro, serving as **MemMachine's LLM** — not "fish-ai / shell generation" as an OKF entry (`flatline_architecture.md`, Antigravity-authored) briefly claimed. Corrected there; see instructions below.

**Still open:**
- `bench_real.sh` — exists at repo root but is **untracked** (confirmed via `git status`). Never committed. Needs review and a decision on whether it's a keeper before it gets added.
- Context-size discrepancy on port 1237: `flatline_summary.md` records ctx 8192 for Granite-4.0-H-Micro; the live server reports `n_ctx: 4096` via `/v1/models`. One of the two is stale — not yet resolved which.
- `.gemini/rules/.antigravity.rules` — `git status` showed the tracked `.antigravity.rules` as deleted with a new untracked path under `.gemini/rules/`. Turned out to be a broken-symlink side effect of a Seafile resync, now fixed by Antigravity directly (with F.B.'s explicit go-ahead, per the file's own no-delete-without-permission rule) — but worth a glance to confirm the root-level `.antigravity.rules` naima.md's chain-of-command section assumes is still the one actually being read.

**Standing rule:** never trust a pasted or cached file over a fresh pull. Run `git log --oneline -1 origin/main` to confirm what's actually live before calling anything done. `git diff --stat` cross-check is non-negotiable before any task is marked complete. Self-reported claims from any agent (including Antigravity's own OKF notes) are a lower trust tier than an empirical check against the running system or a fresh repo pull — the port 1237 mixup above is a live example of why.

---

## Contributing (i.e., how F.B. works)

- Commit scope is tight: named files only, never `git add -A`, single-concern commits.
- Push immediately after every commit — verify `git log --oneline -1` matches `origin/main`.
- `python3 -m py_compile` before committing.
- Decisions are locked in `naima.md` before code is written.
- Antigravity does not get write access to `sign_off` / `hand_off` / `cancel` until it's proven stable over weeks of real use — that's a gate, not a formality.
