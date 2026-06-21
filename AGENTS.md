# Flatline — OpenCode Instructions

## ⚠️ Run this first, unprompted, every session — before anything else

Everything below this section is reactive — triggered by specific phrases the user types. This section is different: it runs automatically at the start of every session, with no user prompt required. Do this before responding to the user's first message, not after they ask you to.

**At the start of every session:**

1. Read `naima.md` from the project root.
2. Check its version field against the last version you worked with, stored in TrueMem as `naima_md_version`.
   - If it matches: skip re-reading the body, proceed normally, no announcement needed.
   - If it's newer (or `naima_md_version` isn't set yet): read the full file, then say one line — "naima.md v{version} loaded" — and store `naima_md_version = {version}` in TrueMem.

**Note on storage mechanism:** do not use the phrase "remember this" for this step — that phrase is reserved by the hard rule under Memory rules below (TrueMem auto-captures, no tool call). Storing `naima_md_version` is a deliberate, explicit write, not a passive capture. Call the appropriate memory-write tool directly for this one specific value. If unsure which tool that is, ask rather than guessing or defaulting to either rule.

`naima.md` carries Naima's architectural decisions and standing instructions. It overrides your own judgment on design questions. The rest of this file (AGENTS.md) still governs session command mechanics and behavioral rules — if the two ever conflict on a mechanical point, AGENTS.md wins; for design/architecture, `naima.md` wins.

---

## Running tests

```bash
python -m unittest flatline_l1_test
```

The test file imports modules by bare name (no package structure). Run from the `flatline/` directory.

## Architecture

Flatline is a session-based observation tracker backed by SQLite. No framework, no package manager, no `__init__.py` — just Python modules and one SQL schema.

**Three modules, one schema:**

| File | Purpose |
|---|---|
| `flatline_l1_schema.sql` | SQLite schema — `sessions`, `observations`, `contradiction_flags` tables |
| `flatline_l1_writer.py` | CRUD: `create_session`, `write_observation`, `close_session`, `flag_contradiction`, `resolve_contradiction` |
| `flatline_l1_lifecycle.py` | State machine for observations — `transition()`, `promote_to_active()`, `mark_gap()`, `decay_observation()` |
| `flatline_l1_session.py` | Session orchestration — `sign_out()` (blocks on unresolved contradictions), `still_broken()`, `neither_worked()` |

**Observation state machine** (from `lifecycle.py`):

```
CANDIDATE → ACTIVE → VALIDATED → DECAYED
              │        └→ SUPERSEDED
              │        └→ INVALIDATED
              └→ INVALIDATED
ACTIVE → GAP → ACTIVE (re-open)
DECAYED is terminal (auto-triggers when score ≤ 0.1)
```

**Key constraints an agent should not guess:**
- Observations can only be written to sessions with `status = 'OPEN'`.
- `sign_out()` blocks if there are unresolved contradiction flags unless `force=True`.
- Contradiction verdicts can only be set once — already-resolved flags raise.
- `decay_observation()` auto-transitions to `DECAYED` when score drops to 0.1 or below (if the current state allows it).
- All IDs are UUID strings; timestamps are Unix epoch integers.

## Schema quirks

- `PRAGMA journal_mode=WAL` and `foreign_keys=ON` are in the schema file — apply them before any data operations if connecting outside the module functions.
- `contradiction_flags.verdict` is `NULL` when unresolved; set to one of `A_WINS`, `B_WINS`, `NEITHER`, `DEFERRED` when resolved.
- `observations.decay_class` constrains which decay path is plausible: `ARCHITECTURAL`, `OPERATIONAL`, `TRANSIENT`, `PERSONAL`.

## Memory rules

**HARD RULE — "remember this" must NEVER call any tool.**
When the user says "remember this", "keep in mind", "note that", or "don't forget": TrueMem (the opencode plugin) handles this automatically by extracting from the conversation. **Do NOT call `memmachine_add_memory`. Do NOT call `remember_this`. Do NOT call any MCP tool.** Acknowledge only with: "TrueMem will capture this."

**"make a note" → `memmachine_add_memory` only.**
When the user says "make a note": call `memmachine_add_memory` with a structured summary of everything significant this session — decisions, file changes, outcomes, paths. producer=opencode, produced_for=fb. One entry per distinct fact. This is the ONLY phrase that triggers `memmachine_add_memory`.

## Session commands

These are natural language commands the user types. When detected, execute the corresponding Python calls exactly as described. The db path is always `flatline.db` in the project root.

If flatline.db does not exist or has no tables, apply the schema first:

```python
import sqlite3
with open('flatline_l1_schema.sql') as f:
    schema = f.read()
conn = sqlite3.connect('flatline.db')
conn.executescript(schema)
conn.close()
```

Only do this once — if the DB already has tables, skip it.

**Session ID resolution**

When any session command requires a session_id, always resolve it by querying the database for the most recent open session:

```python
import sqlite3
conn = sqlite3.connect(db_path)
row = conn.execute(
    "SELECT id FROM sessions WHERE status = 'OPEN' ORDER BY started_at DESC LIMIT 1"
).fetchone()
conn.close()
if row is None:
    raise ValueError("No open session found. Start a new session first.")
session_id = row[0]
```

If no open session exists, tell the user: "No open session found. Say 'new session' to start one."

**signing out** or **signing out — [notes]**

Import `sign_out` from `flatline_l1_session`
Call `sign_out(db_path, session_id, annotation=notes_if_any, force=False)`
- If result `status == 'BLOCKED'`: list each conflict — show description, observation_a_id, observation_b_id. Tell the user to resolve them before closing.
- If result `status == 'CLOSED'`: confirm session closed. If `conflicts_unresolved > 0`, mention how many were left open.

**still broken** followed by an observation ID or description

Import `still_broken` from `flatline_l1_session`
- Identify the observation by ID or by searching content
- Call `still_broken(db_path, obs_id)`
- Confirm: "Marked as still active."

**neither worked** followed by a contradiction description or flag ID

Import `neither_worked` from `flatline_l1_session`
- Identify the contradiction flag
- Call `neither_worked(db_path, flag_id)`
- Confirm: "Contradiction resolved as NEITHER."

**cancel sign off**

Call the `cancel` MCP tool from flatline-knowledge.
Do NOT manually stop timers or delete files. Use the MCP tool only.

**signing off** or **signing off — [notes]**

Before calling the MCP tool, Dixie performs an extraction pass over the full conversation history in context. Extract every fact worth storing permanently: decisions made, configs changed, bugs found, bugs fixed, paths confirmed, approaches rejected, design choices and their rationale.

For each observation produce:
- content: one sentence, specific and factual
- decay_class: ARCHITECTURAL | OPERATIONAL | TRANSIENT | PERSONAL
- confidence: 0.0–1.0

Then call the sign_off MCP tool from flatline-knowledge with:
- annotation: notes_if_any
- observations: the extracted JSON array

Do NOT call sign_out() directly. Do NOT call signing_off() directly. Use the MCP tool only.
This is the only session command needed. No prior new session required.
