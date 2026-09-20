# Implementation guide

How company-reach gets built, milestone by milestone, and how each milestone
is handed over so the person who owns it learns the system as it grows. The
code is written with an AI assistant (Claude); every milestone is explained
and reviewed by a human before the next one starts. Design:
`docs/design/2026-09-19-company-reach-design.md`.

## 1. The loop

Every milestone runs the same four steps:

1. **Plan.** At the start of the milestone Claude writes a task-level plan to
   `docs/plans/<date>-m<N>-<slug>.md` (small tasks, each with its test), and
   Koray reads it. One question per open point, answered before code.
2. **Build.** Claude writes the code and the tests on a branch, task by task,
   test first. Commits are small and conventional.
3. **Explain.** Claude writes `docs/milestones/m<N>-<slug>.md` — short and
   concrete: what was built, which libraries and why, the three to five
   things worth understanding, where to look in the code, how to run it, and
   what to check. Then walks Koray through it in chat, one topic at a time.
4. **Review.** Koray reads the code with the explanation beside it, runs the
   demo command, asks, changes what he wants changed. The PR merges on his
   approval.

A milestone is done when its demo command works and its acceptance criteria
pass. No milestone starts before the previous one is merged.

## 2. Ground rules for the code

- Plain over clever. A function Koray can read in one screen beats a class
  hierarchy. No framework until a step clearly needs one.
- Every module has a docstring saying what it is for and which library it
  uses, in two or three lines.
- Tests without the network for everything deterministic (`respx` mocks
  httpx; fixtures under `tests/fixtures/`). Model calls are tested by the
  golden set, opt-in (`RUN_LLM_EVALS=1`).
- Company and person data only under `data/` (gitignored). Fixtures use
  fictional companies.
- English in files, Turkish in chat — except `docs/milestones/`, which is
  Turkish (its only reader is Koray); library names and settled technical
  terms stay English there. Conventional commits, ≤ 60 chars.

## 3. Milestones

Eight milestones. Each one ends with something Koray can run.

| # | Milestone | Delivers | Demo | Learn |
|---|---|---|---|---|
| M1 | **Skeleton and pool** | `uv` project, settings, `schema.sql` + `db`, `models`, `lindas`, CLI `pool` | `company-reach pool --municipality 3203` fills `companies`; `screen` counts survivors | uv/pyproject/src layout · pydantic-settings · sqlite3 with WAL and `with conn:` · httpx + SPARQL · typer |
| M2 | **LLM layer and scoring** | `llm.ask` with prompt files, provenance, `doctor`; `criteria.md`, `score.md`; `write_criteria`, `score_pool` with the UID-set check and score cache | `company-reach doctor`; `score --goal "…"` writes `scores`; golden scoring test | langchain-openai `with_structured_output` · Pydantic as schema · `string.Template` · asyncio semaphore · what a golden set is |
| M3 | **Parent graph** | `graph.py` with the pool stage, `draw_batch`, `fan_out` with `Send`, a stub `enrich_company`, `collect`, the loop; run manifest | `run --dry` draws a batch and loops on stubs; stubbed-graph test (one raising child, one sending) | LangGraph StateGraph, reducers, `Send`, conditional edges, `recursion_limit`, `ainvoke` and threads |
| M4 | **Finding the site** | `search` (SearXNG + `SearchError`, `probe_search`), `fetcher` (cache, robots, delays, SSRF guard), `textify`, `find_site` with three tiers and `pick_site.md`; Compose with SearXNG | `enrich --uid CHE-… --until site` prints the site, tier and evidence; golden site test | SearXNG JSON API · protego · trafilatura · UID check digit · why "no site" must not mean "search was down" |
| M5 | **Reading and extracting** | `pick_pages`, `read_pages`, `extract` with delimited page text, `check_profile`; needs-JS detector (Playwright service optional) | `enrich --uid … --until profile` prints the profile; extraction golden test; adversarial page → third_party | prompt-injection boundary · deterministic post-checks · text normalisation |
| M6 | **Contact and draft** | `shab` with the DE/FR parser, `find_contact` rules, `recommend`, `draft.md`, `check_draft`, `mailto`; child graph wired; real `enrich_company` wrapper; `retry` | `run` end to end on one batch; results in SQLite; draft golden checklist | SHAB API · regex parsers with fixtures · RFC 6068 · error vs finding in practice |
| M7 | **Review page** | FastAPI app, `review.html` from the prototype, POST routes with the `Sec-Fetch-Site` check, ledger, `forget`, `import-v0`, Compose `app` service | `docker compose up` → review the batch, Send opens Outlook, ledger rows written | FastAPI + Jinja2 · forms without JS · `mailto:` encoding · CSRF-class risks |
| M8 | **Publish** | `PRIVACY.md`, README setup section, committed golden subset, CI (`pytest`, `ruff`). (`LICENSE`, `NOTICE` and the short README shipped on day one) | fresh clone → `doctor` → golden subset runs | what a public tool owes its users |

Dependencies: M2 needs M1; M3 needs M2; M4–M6 need M3 and each other in
order; M7 needs M6; M8 needs everything. M4 is the first milestone that
needs Docker running (SearXNG).

Audit conditions (`docs/design/audit-2026-09-19.md`) land here: P0
composition fix in M3; typed errors and `retry` in M4/M6; `doctor` and the
structured-output setting in M2; content checks in M5/M6; review-page
hardening, `forget`, `import-v0` in M7; licence and privacy in M8.

## 4. Repository layout (target)

```
company-reach/
├── src/company_reach/
│   ├── __init__.py
│   ├── cli.py                 # typer app: doctor pool score run retry enrich review ledger forget import-v0
│   ├── settings.py            # Settings(BaseSettings) — every .env key, typed
│   ├── models.py              # CompanyRecord, SelectionCriteria, Score, SiteChoice, CompanyProfile,
│   │                          # Contact, Recommendation, Draft, CompanyResult
│   ├── errors.py              # SearchError, FetchError, LlmError, ShabError
│   ├── graph.py               # parent StateGraph: ReachState, nodes wiring, fan_out, need_another_batch
│   ├── enrich.py              # child StateGraph: EnrichState, wiring, routers
│   ├── nodes/                 # one file per node, one function each
│   │   ├── load_pool.py · screen_pool.py · write_criteria.py · score_pool.py · probe_search.py
│   │   ├── draw_batch.py · enrich_company.py · collect.py
│   │   └── load_company.py · find_site.py · pick_pages.py · read_pages.py · extract.py
│   │       · check_profile.py · find_contact.py · recommend.py · draft.py · check_draft.py
│   ├── tools/
│   │   ├── lindas.py · search.py · fetcher.py · browser.py · textify.py · shab.py
│   │   ├── llm.py · db.py · mailto.py · doctor.py
│   ├── checks.py              # deterministic checks shared by check_profile / check_draft
│   ├── manifest.py            # run manifest read/write
│   ├── review/
│   │   ├── app.py             # FastAPI routes
│   │   ├── templates/review.html
│   │   └── static/review.css
│   └── schema.sql
├── prompts/
│   ├── criteria.md · score.md · pick_site.md · pick_pages.md · extract.md · draft.md
├── tests/
│   ├── conftest.py            # tmp data dir, settings override, fixture loaders
│   ├── fixtures/              # fictional LINDAS/SHAB/HTML responses
│   ├── fixtures/golden/       # committed redacted golden subset
│   ├── test_<module>.py       # one per tool / node
│   ├── test_graph.py          # stubbed parent graph
│   └── test_prompts.py        # golden set, RUN_LLM_EVALS=1
├── searxng/settings.yml
├── compose.yaml · Dockerfile
├── pyproject.toml · uv.lock · .python-version · .env.example
├── LICENSE · NOTICE · PRIVACY.md · README.md
├── IMPLEMENTATION.md · LEARNINGS.md · AGENTS.md · CLAUDE.md
├── docs/
│   ├── design/                # design doc, spec, validation, audit, ux/, diagrams/
│   ├── research/              # five research notes
│   ├── plans/                 # one task plan per milestone
│   ├── milestones/            # one explanation per milestone, written for Koray
│   └── sketches/
└── data/                      # gitignored: company_reach.db, cache/, runs/, golden/, v0/
```

## 5. What "explain" looks like

`docs/milestones/m<N>-<slug>.md`, one to two pages:

```
# M1 — Skeleton and pool

## What exists now
Three commands work: … (with the exact commands)

## Libraries used and why
- pydantic-settings: reads .env into a typed object; we chose it over os.environ because …
- …

## Worth understanding (3–5 items)
1. `with conn:` in sqlite3 commits or rolls back but does not close — see tools/db.py:18
2. …

## Where to look
tools/lindas.py (60 lines) → nodes/load_pool.py (25 lines) → cli.py `pool`

## Check this
- run `company-reach pool --municipality 3203`; expect ~5,400 rows
- open data/company_reach.db with `sqlite3` and look at one company row
- read tests/test_lindas.py: how the SPARQL call is mocked

## Open threads
…
```

Each item names a file and line. No paragraph longer than five lines.

## 6. Tracking

GitHub milestones M1–M8, one issue per milestone; every work session runs
against its issue. Audit actions are checkboxes inside the milestone issues,
not separate issues.
