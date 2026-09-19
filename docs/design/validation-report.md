# Validation Report — company-reach graph design (revision 2)

Date: 2026-09-19. Checked against `graph.spec.yaml` revision 2, after the
design audit (`audit-2026-09-19.md`). Rules first, then critique.

## Step 1 — Rule-based checks

| Check | Result | Evidence |
|---|---|---|
| Reachability | ✅ | parent: 8/8 nodes reachable from `__start__` (probe_search and the enrich_company wrapper added); child: 10/10 (check_profile, check_draft added) |
| Dead-ends | ✅ | parent ends only through `collect → need_another_batch → __end__`; child ends through `has_site=no`, `needs_draft=skip`, or `check_draft` |
| Cycle exits | ✅ | one cycle (`collect → draw_batch`); exits on `batches_drawn >= 3`, `sendable_count > 0`, `pool_exhausted`; `recursion_limit=40` set explicitly (default would be 1000 — audit A1) |
| State usage — parent | ✅ | every field read by a node or a router; `criteria` replaces `selection_prompt` and is read by `score_pool` |
| State usage — child | ✅ | 13 fields, each read and written by ≥ 1 node or router; the former `error` field is gone — errors are raised, not stored (audit A1) |
| Composition | ✅ | child → parent goes through the `enrich_company` function node returning `{"results": [...]}`; the child writes no parent key (audit A1: a compiled subgraph as a node would have dropped `results` silently) |
| Resume | ✅ | `collect` counts from the `results` table; rerun/`retry` keyed on `results.error_kind` (audit A1/A4) |
| Tool binding | ✅ | nodes reference lindas, search, fetcher, browser, textify, shab, llm, db, mailto, doctor — all ten declared |
| Conditional coverage | ✅ | `fan_out {Send…, empty_batch}`, `need_another_batch {again, done}`, `has_site {yes, no}`, `needs_draft {yes, skip}` map to existing nodes or `__end__` |
| Secret hygiene | ✅ | no state field matches `api_key|token|password|secret|pii_`; no checkpointer |
| HITL coherence | ✅ | no write-side tool to the outside world; the human step is documented with its security rule (`Sec-Fetch-Site`) |
| Error semantics | ✅ | typed errors raised in the child, caught in the wrapper; a superstep can no longer be aborted by one company (audit A1 P1) |

## Step 2 — Critique

- **(accepted) `find_site` is the heaviest node.** Search, verify (three
  tiers), list pages. Stays one node because nothing branches between those
  steps; inside it three helpers (`search_candidates`, `verify_candidate`,
  `list_pages`), each tested alone.
- **(bounded) Memory.** `page_texts` ≤ 10 × 8,000 chars; `results` ≤ 3 ×
  batch_size; `page_urls` ≤ 200. No messages channel.
- **(edge) LINDAS empty**: `load_pool` raises with the municipality id.
- **(edge) SearXNG down before the run**: `probe_search` raises; nothing is
  drawn; `seen` unchanged. **During the run**: `search` raises `SearchError`,
  the wrapper records `error_kind=search`, the company is not redrawn and is
  re-enriched by rerun or `retry`.
- **(edge) Endpoint ignores the JSON schema**: `doctor` fails before the run
  with the raw answer; in a run, `llm` retries once and raises `LlmError`.
- **(edge) Local model with a 4k context**: `doctor`'s marker check fails and
  names the cause.
- **(edge) Hostile page**: instructions in page text are inside the data
  delimiters; a planted `ceo@other.example` becomes `third_party`, the contact
  is `hold`; a person name not on the page is dropped; a link in the draft
  fails `check_draft`. Covered by adversarial golden cases.
- **(edge) Everything screened out**: `draw_batch` sets `pool_exhausted`,
  `fan_out` routes to `collect`, the run ends with counts.
- **(edge) Same `run_id` rerun**: scores kept, batch reused, only error rows
  re-enriched; `collect` sees the earlier results.
- **(edge) Ten children write at once**: one connection per call, WAL,
  `busy_timeout` (audit A1).
- **(edge) Cross-site POST to the review page**: rejected by the
  `Sec-Fetch-Site` check (audit A5).
- **(edge) mailto too long**: `fits=false`; the page says to copy the text.
- **(risk, monitored)** json_schema enforcement on OpenRouter is best-effort;
  Pydantic validation is the guard; the golden set measures it.
- **(deferred, P2)** score cache is in the schema; per-node cost attribution is
  in the manifest; Serper fallback trigger is on errors only.

No errors remain. No waivers.
