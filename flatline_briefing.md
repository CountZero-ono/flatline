# Flatline Briefing
_Session: none — 2026-05-29 — Initial repo setup_

---

## What Changed
- Added: `flatline_summary.md` — consolidated project reference
- Added: `flatline_decisions.md` — architectural decisions & reasoning
- Added: `.gitignore` — excludes SQLite, logs, cached bytecode
- Added: `flatline_briefing.md` — session handoff artifact (initial)

---

## What's Broken Right Now
- [ ] GitHub token write access — was read-only, being updated to read/write
- [ ] `flatline_decisions.md` tail has raw conversation fragments — needs cleanup pass

---

## Decisions Made This Session
- Installed graphify as OpenCode skill — persistent knowledge graphs for exploratory analysis
- Added graphify to flatline docs — tracks it as an active tool in the stack
- GitHub repo initialized for Naima session handoff pipeline

---

## Needs Naima
- Decide what source files (`.py`) are safe to expose in the public/private repo
- Evaluate whether graphify's query tool should be exposed through the MCP server as `query_graph(question)`
- Plan the consolidation trigger design (what promotes L1 → L2?)

---

## Next Task
Wait for Naima to review the handoff pipeline and decide on repo scope and MCP query_graph integration.
