# Housekeeping — two separate jobs, do not mix

## Job 1 — Update flatline_spec.docx (low risk, do first)

The 27B Dense crystallizer model is retired. Dixie (Qwen3.6 35B A3B) now
handles crystallization himself — there is no second model, no swap.

Edit `flatline_spec.docx`:

- **Section 02 (Crystallization Loop):** remove the two lines about
  "35B MoE unloads → Qwen3 27B Dense loads as dedicated crystallizer"
  and "Dense unloads → 35B MoE reloads." Replace with a single line:
  Dixie performs crystallization directly, no model swap.
- **Section 07 (Model Configuration):** remove the "Crystallizer" row
  and the "Two-model strategy" framing. This is now a one-model system.
  Remove the swap sequence diagram and the "LM Studio API handles model
  switching" line — also no longer accurate (mainline llama.cpp now,
  not LM Studio).

Do not touch Sections 01, 03, 04, 05, 06, 08 — schema, lifecycle, fact
structure, edges, predicates, trigger logic, and philosophy are all
still the intended design and remain accurate as written.

Bump the doc version footer/title from v0.1 to v0.2 and note the date
of the edit somewhere visible (title page or a short changelog line).

---

## Job 2 — Codebase cleanup (do after Job 1, report before deleting anything)

Three things to resolve. For each: report findings first, do not delete
or change behavior until confirmed.

**1. `flatline_session_close.py` and the `sign_out` MCP tool**
This implements the now-retired 27B swap (`service_stop`/`service_start`
around crystallization). The `sign_out` MCP tool routes through it; the
`sign_off` MCP tool does not (it has its own inline path and is the one
actually used per AGENTS.md).
- Confirm nothing else calls `flatline_session_close.py`.
- If confirmed unused in any live path other than the `sign_out` tool:
  remove the `sign_out` MCP tool definition and delete the file. One
  sign-off path only (`sign_off`), no near-duplicate naming left behind.

**2. GAP triggering — does it ever actually fire?**
Per the original spec, GAP is reached when a contradiction is tested
and both candidates fail ("neither worked" → both INVALIDATED → GAP
opens). Check:
- Does `neither_worked()` in `flatline_l1_session.py` actually set any
  fact/observation to GAP status, or does it just resolve the
  contradiction flag without touching GAP?
- If GAP is never actually set anywhere: that's the real bug — not the
  handler. Report this clearly, don't fix yet.

**3. `run_gap_handler()` — orphaned**
Defined in `flatline_gap_handler.py`, fully functional, zero callers.
Per spec, GAP work orders are priority #1 in the trigger queue — above
session end, above nightly sweep.
- Do not wire this yet. Just confirm: if GAP were ever actually set
  (per item 2 above), is `run_gap_handler()` otherwise ready to run as-is,
  or does it also need updates (e.g. SearXNG endpoint reachable,
  `flatline_l3_query.py`'s `gap_search`/`ensure_collection` still valid)?

Report back on all three before any deletion or wiring happens.
