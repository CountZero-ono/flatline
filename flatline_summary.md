# Project Flatline — Consolidated Reference

## Description

**Project Flatline** is a self-maintaining, multi-layered knowledge system built around a personal AI stack on a Beelink SER7. It solves a single problem: every AI session starts with complete amnesia. Decisions made, problems solved, configurations tuned — gone.

The name is a William Gibson reference. The Dixie Flatline in *Neuromancer* was a ROM construct — a crystallized snapshot of a human mind, consulted for expertise, self-aware enough to know what it was. Flatline does the same thing for your sessions: extracts, structures, and permanently stores knowledge so it can be retrieved by future sessions.

The system is also named "Flatline" because the flatline is where memory begins — when the session ends, the crystallization starts.

**Core principles:**
- Fully local. No cloud dependency. Forever yours.
- Empirically honest. The graph admits what it doesn't know.
- Self-maintaining. No manual curation required.
- Schema-constrained. The schema is the intelligence multiplier.
- Failure is data. Every dead end is permanently queryable.
- A GAP cannot be closed by inference. Only reality closes it.

---

## Recent Changes

| Date | Change |
|------|--------|
| May 2026 | Built llama-cpp-mainline alongside turboquant (separate binary, no conflict) |
| May 2026 | Created `llama-qwen-mtp.service` on port 1235 with MTP draft speculative decoding (`--spec-type draft-mtp --spec-draft-n-max 2`) |
| May 2026 | Turboquant parked on 1239, disabled — service file intact if needed |
| May 2026 | Updated agent configs (`generic.md`, `dixie.md`) to `qwen3.6-35b-a3b-mtp@q3_k_m` |
| May 2026 | Fixed `GameMode.sh` to stop/start the MTP service instead of the old llama-qwen |
| May 2026 | Enabled `llama-qwen-mtp` to autostart on boot |
| May 2026 | Confirmed 83% draft acceptance rate, ~30 tok/s on code tasks |
| May 2026 | Added `memmachine` remote MCP entry to `~/.opencode/opencode.json` (`http://192.168.1.53:8080/mcp/`) |
| May 2026 | Migrating MemMachine IP from `192.168.1.208` → `192.168.1.53` |
| May 2026 | `AGENTS.md` updated: `new session` command now writes session UUID to `~/.flatline/current_session` |
| May 2026 | Installed `graphify` Opencode skill — converts any folder of files into a navigable knowledge graph with community detection, persistent graph storage, and an honest audit trail (EXTRACTED / INFERRED / AMBIGUOUS edges) |

---

## Architecture Overview

### Three-Layer Memory Pipeline

Each layer has a distinct role. No layer substitutes for another.

| Layer | Store | Role | Analogy |
|-------|-------|------|---------|
| **L1** | true-mem (SQLite) | Short-term working memory. Ebbinghaus forgetting curve — decays unless reinforced. OpenCode plugin. | Short-term memory |
| **L2** | MemMachine 0.3.6 (Neo4j + Postgres) | Long-term episodic memory. Relational graph. Survives across sessions. Typed facts and relationships. | Long-term memory |
| **L3** | Qdrant 1.17.1 | Semantic archive. Never forgets, never prioritizes. Vector embeddings. Two collections: knowledge + sessions. | Paper archive |

### Consolidation — The Missing Piece

Human memory works because of consolidation: the brain moves things from short-term to long-term during sleep. This process does not yet exist in Flatline. Nothing automatically promotes important L1 entries to L2.

**Planned consolidation logic (to be designed during MCP wiring phase):**
- Importance scoring at write time — errors, config decisions, explicit preferences score high; variable names score low
- Anything above threshold gets parallel write to L2 immediately
- End-of-session sweep — anything accessed multiple times during session also gets promoted
- L3 gets everything, always — raw storage, semantic retrieval on demand

### Infrastructure Details

**SER7 / Vulcan** — Ryzen 7 7840HS with Radeon 780M iGPU, 32GB unified RAM (16GB allocated to iGPU via BIOS)

| Service | IP / Port | Status |
|---------|-----------|--------|
| MemMachine (Neo4j + Postgres) | 192.168.1.53:8080 | Healthy (LXC 106) |
| Portainer UI | 192.168.1.53:9443 | — |
| Qdrant 1.17.1 | 192.168.1.44:6333 | Healthy |

**llama.cpp (llama-server) on SER7 — serves both inference and embedding models**

| Model | Port | Service | Details |
|-------|------|---------|---------|
| Qwen3.6 35B A3B Q3_K_M (MTP) | 1235 | `llama-qwen-mtp.service` | Primary inference, context 98304, KV q8_0/q8_0, flash-attn, kv-unified, batch 512/512, **draft speculative decoding** (`--spec-type draft-mtp --spec-draft-n-max 2`), 83% acceptance, ~30 tok/s |
| llama-cpp-mainline (turboquant) | 1239 | `llama-turboquant.service` | Parked, disabled — service file intact if needed |
| Granite-embedding-97M-multilingual-r2-Q8_0 | 1236 | `llama-granite.service` | L3 embeddings, 384-dim, Cosine distance |
| Granite-4.0-H-Micro Q4_K_M | 1237 | — | Context 8192 |
| Qwen3.6 27B Q3_K_S | 1238 | `llama-crystallizer.service` | Crystallizer, registered |

**true-mem** installed as OpenCode plugin via `~/.opencode/opencode.json`: `"plugin": ["true-mem"]`

Note: `~/.config/opencode/` is a stale duplicate — leave it alone.

### Hardware

- **CPU**: Ryzen 7 7840HS (Zen4, 8c/16t)
- **RAM**: 32GB unified (16GB allocated to iGPU via BIOS)
- **GPU**: Radeon 780M (iGPU, UMA — no PCIe penalty)
- **Storage**: NVMe SSD
- **OS**: Garuda Linux / Hyprland (Wayland)
- **Backend**: Vulkan (ROCm blocked by guardrails — causes swap hell)
- **Model weights**: ~15.4GB in unified memory + 1GB KV cache
- **Process RSS**: ~6.5GB (CPU overhead + 397MB CPU_Mapped buffer)
- 780M uses unified memory so VRAM IS system RAM — not separate

---

## Core Mechanisms

### Fact Lifecycle State Machine

Facts progress through defined states:

```
CANDIDATE → ACTIVE → VALIDATED → INVALIDATED → SUPERSEDED → DECAYED → GAP
```

| Status | Meaning | Next State |
|--------|---------|------------|
| **CANDIDATE** | Extracted, not yet corroborated | ACTIVE / INVALIDATED |
| **ACTIVE** | Corroborated, in use | VALIDATED / SUPERSEDED / DECAYED |
| **VALIDATED** | Empirically confirmed — tried it, it worked | SUPERSEDED (if replaced) |
| **INVALIDATED** | Empirically failed — tried it, didn't work | Terminal (kept for history) |
| **SUPERSEDED** | Replaced by a validated fact | Terminal (kept for history) |
| **DECAYED** | Aged past threshold, never resolved | Terminal |
| **GAP** | All candidates invalidated. Unsolved problem. | CLOSED (when new fact validated) |

### Decay Classes

| Class | Example | Decay Rate |
|-------|---------|------------|
| **ARCHITECTURAL** | Core system design decisions | Months (180 days) |
| **OPERATIONAL** | Current config, active tools | Weeks (30 days) |
| **TRANSIENT** | Bugs, one-off fixes, in-progress | Days (7 days) |
| **PERSONAL** | Preferences, opinions, style | Until overridden |

### GAP Handling Protocol

A GAP is not a dead end. It is a work order.

1. GAP surfaces in context injection at session start when topic is relevant
2. Triggers automatic **L3 semantic search** against Qdrant vector store
3. If L3 returns nothing → **external search** fires (SearXNG)
4. External results enter graph as CANDIDATE facts with `source_type: EXTERNAL`
5. EXTERNAL facts start with lower confidence than SESSION-derived facts
6. User tests them → VALIDATED closes the GAP → INVALIDATED keeps it open
7. A GAP cannot be closed by inference. Only empirical outcome closes it.

### Crystallization Loop

The core pipeline. Async, unattended, self-maintaining. Triggered by `signing out`.

1. Session generates raw observations → written to L1 with Ebbinghaus decay
2. Trigger fires → crystallization job queued
3. 35B MoE unloads → Qwen3 27B Dense loads as dedicated crystallizer
4. Crystallizer reads L1 observations + relevant L2 subgraph context
5. Pass 1: Extract typed entities + facts with confidence scores
6. Pass 2: Lightweight resolver handles contradiction ID matching
7. Quality gate: confidence threshold, corroboration check, contradiction resolution
8. Promoted facts written to L2 (Neo4j) with typed edges
9. Facts embedded and indexed in L3 (Qdrant)
10. Dense unloads → 35B MoE reloads → ready for next session
11. Next session start: relevant L2 subgraph injected into context

### Two-Model Strategy

| Role | Model | Config |
|------|-------|--------|
| Interactive / Agentic | Qwen3.6 35B A3B Q3_K_M (MTP) | Vulkan, 16GB UMA, ctx 98304, thinking ON, **draft speculative decoding** (`--spec-type draft-mtp --spec-draft-n-max 2`), 83% acceptance, ~30 tok/s |
| Crystallizer | Qwen3.6 27B Q3_K_S | Async, unattended, thinking ON, no time constraint |

### Command Vocabulary

| Phrase | Action |
|--------|--------|
| `signing out` | Triggers session close protocol + crystallization |
| `signing out — [notes]` | Same + attaches annotation as crystallizer hint |
| `still broken` | Explicit GAP signal mid-session, queues external search |
| `neither worked` | Same as `still broken` |

### Pre-flight Conflict Check

On `signing out`, the system scans for contradictions before finalizing.

- L1 scans session for contradiction flags
- Conflicts found? → system asks for verdict before closing
- No conflicts? → straight to crystallization queue

**Verdict options:**
- A or B worked → winner VALIDATED, loser INVALIDATED
- Neither worked → both INVALIDATED, GAP opens, external search queues
- Didn't try yet → both stay CANDIDATE, contradiction edge preserved, revisited next relevant session

### Trigger Priority Queue

All triggers feed one queue. One job at a time. No concurrent writes to Neo4j.

| # | Trigger | Condition |
|---|---------|-----------|
| 1 | GAP work order | Active unsolved problem |
| 2 | Observation threshold | Hot session, 20+ observations |
| 3 | Session end | `signing out` trigger received |
| 4 | Nightly sweep | Scheduled maintenance |
| 5 | Decay check | Routine confidence audit |

---

## Knowledge Schema

### Neo4j Graph Model

**Entity nodes** — The atomic unit. Every nameable, referenceable thing.
- `id`: uuid
- `label`: string
- `type`: PERSON | SYSTEM | MODEL | CONCEPT | TOOL | LOCATION | FILE
- `aliases`: string[]
- `first_seen`: timestamp
- `confidence`: float (0.0–1.0, updated on corroborations)
- `session_ids`: string[]

**Fact nodes** — The real unit of knowledge.
- `id`: uuid
- `statement`: string
- `subject`: entity_id
- `predicate`: string
- `object`: entity_id | literal
- `valid_from`: timestamp
- `valid_until`: timestamp | null
- `confidence`: float
- `corroboration_count`: int
- `source_sessions`: string[]
- `decay_class`: ARCHITECTURAL | OPERATIONAL | TRANSIENT | PERSONAL
- `status`: CANDIDATE | ACTIVE | VALIDATED | INVALIDATED | SUPERSEDED | DECAYED | GAP
- `source_type`: SESSION | EXTERNAL | INFERRED

**Session nodes** — Provenance anchor. All facts trace back to sessions.
- `id`: uuid
- `started_at`: timestamp
- `ended_at`: timestamp
- `raw_l1_ref`: string
- `crystallized_at`: timestamp | null
- `user_annotation`: string | null

### Edge Types

| Edge | Meaning |
|------|---------|
| (Entity)-[:USES]→(Entity) | — |
| (Entity)-[:RUNS_ON]→(Entity) | — |
| (Entity)-[:REPLACES]→(Entity) | — |
| (Entity)-[:CONFLICTS_WITH]→(Entity) | — |
| (Entity)-[:INSTANCE_OF]→(Entity) | — |
| (Entity)-[:LOCATED_AT]→(Entity) | — |
| (Entity)-[:PREFERS]→(Entity) | — |
| (Entity)-[:DEPENDS_ON]→(Entity) | — |
| (Entity)-[:CONFIGURED_WITH]→(Entity) | — |
| (Fact)-[:ASSERTS]→(Entity) | — |
| (Fact)-[:SOURCED_FROM]→(Session) | — |
| (Fact)-[:CONTRADICTS]→(Fact) | — |
| (Fact)-[:CORROBORATES]→(Fact) | — |

### Predicate Vocabulary

Use existing predicates. Extend only if genuinely necessary. New predicates flagged for schema review.

`USES | RUNS_ON | REPLACES | CONFLICTS_WITH | INSTANCE_OF | LOCATED_AT | PREFERS | DECIDED | REJECTED | DEPENDS_ON | CONFIGURED_WITH | PLANNED`

---

## Crystallizer Prompt

### System Prompt

> You are a knowledge crystallizer. Your job is to read raw session observations and extract structured facts for permanent storage in a knowledge graph.
>
> You are precise, conservative, and schema-disciplined.
>
> You do not invent. You do not infer beyond what observations support.
>
> When uncertain: lower confidence. Do not omit.
>
> Output: valid JSON only. No prose. No explanation. No markdown.

### User Prompt Template

```
<graph_context>
  {{injected_subgraph}} // relevant L2 subgraph for this session
</graph_context>
<observations>
  {{l1_session_content}} // raw L1 observations to crystallize
</observations>
<session_annotation>
  {{user_annotation}} // from 'signing out — [notes]', may be null
</session_annotation>
```

### Output Schema

```json
{
  "entities": [{
    "label": string,
    "type": "PERSON|SYSTEM|MODEL|CONCEPT|TOOL|LOCATION|FILE",
    "aliases": [string],
    "confidence": float
  }],
  "facts": [{
    "statement": string,
    "subject": string,
    "predicate": string,
    "object": string,
    "confidence": float,
    "decay_class": "ARCHITECTURAL|OPERATIONAL|TRANSIENT|PERSONAL",
    "source_type": "SESSION|EXTERNAL|INFERRED",
    "contradiction_flag": string | null,
    "corroborates": [string]
  }]
}
```

### Confidence Guidelines

| Confidence | Meaning |
|------------|---------|
| 0.9+ | Explicitly stated, unambiguous |
| 0.7 | Clearly implied, high probability |
| 0.5 | Mentioned once, uncertain context |
| <0.5 | Flag but include — do not discard. Low confidence is information. |

---

## MCP Wiring Reference

MCP (Model Context Protocol) is the nervous system connecting OpenCode to the memory stack. Without it the three layers are isolated islands.

**MemMachine MCP**
- Endpoint: `http://192.168.1.53:8080/mcp/`
- Config location: `~/.opencode/opencode.json` (NOT `~/.config/opencode/` — stale duplicate)
- Requires `user-id` header to scope memory operations

Target config entry:
```json
{
  "mcp": {
    "memmachine": {
      "type": "http",
      "url": "http://192.168.1.53:8080/mcp/",
      "headers": { "user-id": "fb" }
    }
  }
}
```

**Verify MCP Server Running**
```bash
curl http://192.168.1.53:8080/health
// Expected: {"status":"healthy","version":"0.3.6"}
```

**Smoke Test After Wiring**
1. Restart OpenCode
2. Have OpenCode write a test memory
3. Verify MemMachine tools appear in agent's available toolset
4. Confirm via Portainer UI @ `https://192.168.1.53:9443`

---

## RAG Pipelines

Two separate ingestion pipelines, both terminating in Qdrant. Both accessible to OpenCode via MCP.

**Pipeline 1 — Personal Knowledge Base**
- Sources: PDFs, books, notes, recipes, Obsidian vault, everything personal
- Ingestion tool: TBD (candidates: AnythingLLM, LlamaIndex, custom script)
- Process: chunked → embedded (Granite 384-dim) → Qdrant collection: `knowledge`
- Notes live in Obsidian — plain markdown files on disk, ideal RAG format, no export step ever

**Pipeline 2 — AI Session Knowledge**
- Sources: OpenCode transcripts, curated Claude sessions
- llm-wiki scope: AI session transcripts only — NOT general documents
- Tool: Pratiyush/llm-wiki (12-tool MCP server, produces llms.txt + JSON-LD graph)
- Output: Qdrant collection: `sessions`
- OpenCode can query llm-wiki natively via MCP

---

## System Philosophy

- The graph never guesses — it either knows or admits it doesn't.
- Failure is data. Every dead end is permanently queryable.
- A GAP is not a dead end. It is a work order.
- External facts start with lower confidence than session-derived facts.
- A GAP cannot be closed by inference. Only empirical outcome closes it.

---

## Key Decisions & Rationale

### Decisions Made

| Decision | Rationale | Status |
|----------|-----------|--------|
| MTP draft speculative decoding (draft-mtp, n=2) | 83% draft acceptance rate, ~30 tok/s on code tasks vs ~27 t/s baseline | VALIDATED |
| Vulkan over ROCm | ROCm causes swap hell on 780M, Vulkan stable at 27 t/s | VALIDATED |
| 16GB UMA in BIOS | Max the hardware supports. Single biggest performance unlock. | VALIDATED |
| ctx 98304 over 24576 | OpenCode hits context limits on real workloads. 1-2 t/s cost is worth it. | VALIDATED |
| Qwen3.6 35B MoE over dense | MoE activates ~3B params per token, full dense would be far slower | VALIDATED |
| MemMachine over mem0/Zep | Zep Community Edition deprecated April 2025. MemMachine alive and compatible. | VALIDATED |
| Continue MemMachine, evaluate mem0 later | MemMachine healthy and running. Switch after real usage reveals if Neo4j graph earns its keep. | ACTIVE |
| Two Qdrant collections | knowledge (personal docs) + sessions (AI transcripts). Same instance, different namespaces. | ACTIVE |
| llama.cpp (llama-server) | Single stack, unified memory optimized. No LM Studio overhead. | VALIDATED |
| No cloud dependency | Philosophical commitment. Everything runs locally. | ARCHITECTURAL |
| dry_run mode for crystallization | Guards model swap during testing, prevents OOM crashes. | ACTIVE |
| Neo4j Auth("basic") scheme | Required for neo4j driver 6.x with Neo4j 5.23. | VALIDATED |
| graphify as exploratory graph tool | Complements L2 Neo4j structured memory with persistent, audited, community-detected knowledge graphs from arbitrary corpora. | ACTIVE

### Rejected Approaches

| Rejected | Reason |
|----------|--------|
| Ollama | Redundant with llama.cpp, ROCm generation was 10 t/s |
| Zep Community Edition | Deprecated April 2025. Self-hosting now requires 3+ systems. |
| Unsloth quants for primary model | Q4_K_M quality beats Q2 at same UMA ceiling. Speed gain not worth quality loss. |
| LM Studio (bypass llama.cpp) | Benchmarking showed no meaningful gain over llama.cpp on UMA hardware. |
| Bumping UMA past 16GB | BIOS maximum on Beelink SER7 is 16GB. Hardware ceiling. |
| Syncthing | User preference. MEGA used instead. |
| MemMachine MoE CPU layer offload | Setting causes model not to load on Vulkan backend. Confirmed broken. |
| mem0ai OpenMemory (now) | Potentially cleaner than MemMachine long-term. Defer until MemMachine real-usage data collected. |

---

## Open Questions & Gaps

| Question | Context | Priority |
|----------|---------|----------|
| Consolidation trigger design | What promotes L1 → L2? Time? Importance score? Both? | HIGH |
| Pipeline 1 ingestion tool | AnythingLLM vs LlamaIndex vs custom script for personal docs | MEDIUM |
| llm-wiki OpenCode adapter | Does llm-wiki pick up OpenCode transcripts or need path tweak? | MEDIUM |
| mem0ai OpenMemory evaluation | After real MemMachine usage: does Neo4j graph earn its keep? | LOW (defer) |
| Remote access frontend | Tailscale confirmed. Frontend: LobeHub self-hosted or custom? | LOW (later) |
| Crystallizer model deployment | llama-crystallizer.service created, port 1238, Qwen3.6-27B-Q3_K_S.gguf confirmed | RESOLVED |

---

## What's Been Implemented

### L1 — true-mem / SQLite

| File | Purpose |
|------|---------|
| `flatline_l1_schema.sql` | SQLite schema — `sessions`, `observations`, `contradiction_flags` tables |
| `flatline_l1_writer.py` | CRUD: `create_session`, `write_observation`, `close_session`, `flag_contradiction`, `resolve_contradiction` |
| `flatline_l1_lifecycle.py` | State machine: `transition()`, `promote_to_active()`, `mark_gap()`, `decay_observation()` |
| `flatline_l1_session.py` | Session orchestration: `sign_out()`, `still_broken()`, `neither_worked()` |
| `flatline_l1_test.py` | 15 passing tests |
| `flatline_l2_promote.py` | Promotes L1 observations to MemMachine API |

- true-mem installed as OpenCode plugin, verified working
- L1 wiring test — `promote_session` working
- L1 sign_out auto-promote test passed

### L2 — MemMachine / Neo4j

| File | Purpose |
|------|---------|
| `flatline_crystallizer.py` | Full crystallization pipeline (L1 → LLM → Neo4j + Qdrant) |
| `flatline_session_close.py` | Session close protocol with model swap, crystallization, poweroff |

- MemMachine 0.3.6 deployed via Docker on LXC 106 @ 192.168.1.53
- Neo4j + Postgres healthy
- Wired to llama-server, health check passing
- Entity/Fact/Session nodes with ASSERTS, SOURCED_FROM, CONTRADICTS, CORROBORATES edges

### L3 — Qdrant

| File | Purpose |
|------|---------|
| `flatline_l3_ingest.py` | Ingestion: `chunk_text()`, `stable_id()`, `ingest_text()`, `ingest_file()` |
| `flatline_l3_query.py` | Query: `embed()`, `ensure_collection()`, `search()`, `gap_search()`, `upsert_chunk()` |

- Qdrant 1.17.1 at 192.168.1.44:6333
- Granite-embedding-97M serving 384-dim Cosine vectors (port 1236)
- Test data (4 Asian Pickles chunks) verified working
- CHUNK_SIZE=500, OVERLAP=50, md5 stable IDs

### Gap Handling & Decay

| File | Purpose |
|------|---------|
| `flatline_gap_handler.py` | L3 → SearXNG fallback for GAP facts |
| `flatline_decay_sweep.py` | Time-based decay sweep with thresholds per decay class |

### Crystallizer

| File | Purpose |
|------|---------|
| `flatline_crystallizer.py` | Reads L1 observations + L2 subgraph, calls Qwen3.6 27B, writes entities/facts to Neo4j + Qdrant |
| `flatline_crystallize_run.sh` | Delayed crystallization: model swap, port polling, crystallize_session() call, poweroff |
| `flatline_session_close.py` | Model swap: stops QWEN service, starts CRYSTALLIZER service, runs crystallization, restores QWEN |

- Qwen3.6 27B Q3_K_S on port 1238 as dedicated crystallizer
- MIN_CONFIDENCE = 0.3
- Two-pass contradiction handling (flag in Pass 1, resolve in Pass 2)
- Delayed crystallization via systemd timer (1h after sign_off), sentinel file, error recovery with llama-qwen restart

### Graph Knowledge Extraction

| File / Path | Purpose |
|------|---------|
| `~/.config/opencode/skills/graphify/SKILL.md` | graphify skill — turns any folder into a navigable knowledge graph via AST + semantic extraction, community detection, and persistent JSON/HTML output |

- Installed as an OpenCode skill under `~/.config/opencode/skills/graphify/`
- Three things it does an AI alone cannot: persistent graph storage (`graphify-out/graph.json`), honest audit trail (EXTRACTED / INFERRED / AMBIGUOUS edges), cross-document surprise via community detection
- Outputs: interactive HTML, GraphRAG-ready JSON, plain-language audit report
- Supports incremental updates (`--update`), clustering (`--cluster-only`), Neo4j push (`--neo4j-push`), SVG/GraphML export, MCP stdio server for agent access
- BFS/DFS query tools for navigation; shortest path between concepts; per-node explanation

### MCP / Session Close

| File | Purpose |
|------|---------|
| `flatline_mcp_server.py` | 8 MCP tools wired into OpenCode, session persistence via `.current_session`, Neo4j auth with `Auth("basic", ...)` scheme, `extract_text()` helper with PDF/DOCX/EPUB support, PDF OCR via pdf2image + Tesseract 5.5.2 (per-page lazy rasterization, 50-char threshold, 1-indexed page fix), `read_document` + `ingest_document` tools |
| `flatline_session_close.py` | `dry_run` flag guards model swap and poweroff, `CASE WHEN` replaces `max()` for Neo4j 5.x compatibility |

### Delayed Crystallization + Shutdown

| File | Purpose |
|------|---------|
| `flatline_mcp_server.py` | `sign_off` handler writes `~/.flatline/pending_crystallization` sentinel (JSON: session_id + ISO timestamp), starts `flatline-crystallize.timer` via systemd |
| `~/.config/systemd/user/flatline-crystallize.timer` | Triggers 1h after activation (1min accuracy), **not enabled at boot** |
| `~/.config/systemd/user/flatline-crystallize.service` | Oneshot service running `flatline_crystallize_run.sh`, **not enabled at boot** |
| `flatline_crystallize_run.sh` | Sequential shutdown script: stops llama-qwen, polls port 1235 closed (120s), starts llama-crystallizer, polls port 1238 up (180s), calls `crystallize_session()` directly from `flatline_crystallizer.py` via Neo4j driver, stops crystallizer, deletes sentinel, `systemctl poweroff` |

- **Flow**: `sign_off` → write sentinel → start timer → (1h delay) → timer fires → service runs → model swap → crystallize → poweroff
- **Error handling**: on any failure, stops crystallizer, restarts llama-qwen, deletes sentinel, logs to `~/logs/flatline-crystallize.log`, exits without poweroff
- **Notable**: `flatline_session_close.py` is NOT called by the shell script — `sign_off` already ran `signing_off()` which ran `sign_out()`. The shell script only handles crystallization (model swap + `crystallize_session()`).

---

## What to Implement / Pending

### High Priority

1. **End-to-end testing** — run full lifecycle with real data (create session → write observations → trigger contradictions → sign out → crystallize)
2. **Consolidation trigger design** — what promotes L1 → L2? Time? Importance score? Both?
3. **MCP wiring** — DONE
4. **Seed MemMachine with stack context**
5. **Delayed crystallization + shutdown** — DONE (sentinel file, systemd timer/service, shell script with error handling and port polling)

### Medium Priority

5. **llm-wiki install and session ingestion** → Qdrant collection: `sessions`
6. **Document ingestion pipeline** (tool TBD) → Qdrant collection: `knowledge` — DONE
7. **Observation threshold trigger** — hot session, 20+ observations (in spec, not in code)
8. **Nightly sweep** — scheduled maintenance trigger (in spec, not in code)
9. **Decay check trigger** — routine confidence audit (sweep code exists, trigger not wired)
10. **Crystallizer model deployment** — DONE
11. **Performance tuning** — optimize L3 pipeline throughput and Ebbinghaus curve parameters
12. **Document reader tools** (read_document, ingest_document) — DONE
13. **Session history retrieval** (query_sessions) — semantic search against sessions Qdrant collection

### Low Priority

14. **YAML fix** — bad character (0x16) in configuration — requires patching
15. **Remote access** — Tailscale + thin frontend (LobeHub or custom)
16. **mem0ai OpenMemory evaluation** — after real MemMachine usage: does Neo4j graph earn its keep?

---

## Files

### Project Root (`/home/fuad/OCProjects/flatline/`)

| File | Purpose |
|------|---------|
| `flatline_l1_schema.sql` | SQLite schema |
| `flatline_l1_writer.py` | L1 CRUD |
| `flatline_l1_lifecycle.py` | L1 state machine |
| `flatline_l1_session.py` | L1 session orchestration |
| `flatline_l1_test.py` | L1 tests |
| `flatline_l2_promote.py` | L1 → L2 promotion |
| `flatline_crystallizer.py` | Crystallization pipeline |
| `flatline_crystallize_run.sh` | Delayed crystallization shutdown script |
| `flatline_session_close.py` | Sign-out + model swap |
| `flatline_gap_handler.py` | GAP → L3 → SearXNG |
| `flatline_decay_sweep.py` | Time-based decay |
| `flatline_l3_ingest.py` | L3 ingestion |
| `flatline_l3_query.py` | L3 query/retrieval |
| `flatline_spec.docx` | Spec v0.1 (superseded by this doc) |
| `flatline.db` | L1 SQLite database (test) |

### Skills

| Path | Purpose |
|------|---------|
| `~/.config/opencode/skills/graphify/SKILL.md` | Knowledge graph extraction — any input → persistent graph + community detection + HTML/JSON/report |

### Infrastructure

| Path | Purpose |
|------|---------|
| `~/.true-mem/memory.db` | L1 true-mem SQLite database |
| `~/.opencode/opencode.json` | OpenCode config (plugin + MCP) |
| `~/.config/systemd/user/flatline-crystallize.timer` | Delayed crystallization trigger (1h, not enabled at boot) |
| `~/.config/systemd/user/flatline-crystallize.service` | Delayed crystallization oneshot (not enabled at boot) |
| `~/.flatline/current_session` | Active session ID (written by `new session` command) |
| `~/.flatline/pending_crystallization` | Sentinel file (JSON, session_id + timestamp, written by sign_off) |
| `~/logs/flatline-crystallize.log` | Crystallization run log |
| `192.168.1.53:8080` | MemMachine MCP endpoint |
| `192.168.1.53:9443` | Portainer UI |
| `192.168.1.44:6333` | Qdrant server |
| `192.168.1.112:1235` | llama-server Qwen3.6 inference |
| `192.168.1.112:1236` | llama-server Granite embedding |
| `192.168.1.112:1237` | llama-server Granite Micro |
| `localhost:1235` | llama-qwen-mtp.service (Qwen3.6 35B MTP inference, port 1235, draft spec decoding) |
| `localhost:1238` | llama-crystallizer.service (Qwen3.6 27B crystallizer, port 1238) |

---

*FLATLINE v1.0 — Consolidated May 2026 — Built on Beelink SER7*
