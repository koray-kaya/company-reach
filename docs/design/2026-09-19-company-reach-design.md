# company-reach — system design

Date: 2026-09-19, revision 2 (after the design audit `audit-2026-09-19.md`).
Status: approved by Koray phase by phase; audit findings P0–P1 worked in.
Machine-readable twin: `graph.spec.yaml`. Checks: `validation-report.md`.
Visual: <https://claude.ai/artifact/P7hmJMMTzHswUtgv72sgVF>. Review page
prototype: `ux/review-onepager.html`, spec `ux/review-page-ux.md`.

Research behind the choices: `docs/research/`. Lessons from v0: `LEARNINGS.md`.
How it gets built: `../IMPLEMENTATION.md`.

## 1. What it does

From one Swiss municipality and the user's goal, produce about ten reviewed
candidates — verified website, named contact, personal German invitation
draft — for a human to send. It never sends mail.

Input (CLI): municipality id, goal text, about-me text, batch size, seed.
Output: a local review page, one company per screen, arrow keys to move;
decisions land in a SQLite ledger.

First use: recruiting SME interview participants for the thesis. Built to be
published on GitHub as a general tool: the goal and the "about me" are input,
nothing personal is in the code.

## 2. Decisions taken

| Decision | Choice | Why |
|---|---|---|
| Source | LINDAS only | the one open source that lists companies by municipality; keeps v1 simple |
| Candidate selection | fully automatic | Koray's review starts once ten candidates are ready |
| Orchestration | LangGraph, StateGraph API | same tool as the thesis pipeline; explicit nodes and edges to learn from. A plain asyncio pipeline would also do; LangGraph is a learning choice, recorded as such |
| Structure | parent graph; a function node per company that runs the child graph, fanned out with `Send` | ten companies in parallel; the wrapper catches failures so one company never aborts the batch; child testable alone |
| Human review | outside the graph | a FastAPI page over SQLite; no `interrupt()` |
| Checkpointer | none | our SQLite is the system of record; rerun by `run_id`. Chosen for simplicity — the SQLite checkpointer's 2025 injection issue is patched and is not the reason |
| LLM access | OpenAI-compatible endpoint (`base_url` + key + model) | Koray's decision: users bring any provider (OpenAI, OpenRouter, Ollama, gateways). The research recommended the Anthropic SDK for guaranteed schemas; the price of freedom is that schema enforcement varies by provider, so the tool validates every answer itself and probes the endpoint before a run (`doctor`) |
| Structured output | `with_structured_output(method=LLM_STRUCTURED_METHOD)`, default `json_schema`, `include_raw=True` | library default is function calling, which Ollama cannot force; json_schema is honoured by OpenAI and Ollama, best-effort on OpenRouter |
| Search | SearXNG (Docker) default, Serper optional — on search *errors* only | free, self-hosted; Brave's terms forbid storing results; falling back on empty results would spend paid queries on companies that have no site |
| Website reading | `httpx` + `selectolax` + `trafilatura` + `protego`; Playwright as optional service | 0 of 15 known sites needed JavaScript; keep the door open cheaply |
| Site verification | tiers: UID match > register address match > model choice with verbatim quote | a quote proves grounding, not identity; the register address is free corroboration (13/15 in the probe) |
| Contacts | site → SHAB name → generic address with name → LinkedIn lead (unverified) | measured yields from v0 |
| Untrusted content | page text is data: delimited in prompts; deterministic checks after extraction (email domain = site domain, persons verbatim in text, no URL in drafts) | the only credible path to a mis-addressed invitation; cheap to close |
| Errors vs findings | infrastructure failures are typed errors, retried; "no site" is a finding only when search worked | transient outages must not consume the best candidates |
| Sending | `mailto:` only | UWG/revDSG position from v0; OST mail cannot be automated anyway |
| Mail volume | no hard cap; the page shows this month's count | the user decides |
| Review page | full screen, one company per screen, left facts / right draft, arrow keys | Koray's brief: summary first, no scrolling, slide through |
| Pages read per site | setting, default 10; text per page capped at 8,000 chars (setting) | tuned by the golden set |
| Licence | MIT, with `NOTICE` for data attributions | permissive; stack is licence-clean (SearXNG runs as a separate container) |

## 3. Architecture

```
CLI (typer) ──► LangGraph "reach" ──► SQLite ◄── FastAPI review page ──► mailto:
                    │                    ▲
                    └─ Send ×N ─► enrich_company (function node) ─► child graph
external: LINDAS · SearXNG (container) · company sites · SHAB · LLM endpoint · [Playwright container]
```

### 3.1 Parent graph `reach` — once per run

```
start → load_pool → screen_pool → write_criteria → score_pool → probe_search → draw_batch
draw_batch ──Send per uid──► enrich_company ──► collect
draw_batch ──empty batch──► collect
collect ──(sendable == 0 and batches < 3 and pool not exhausted)──► draw_batch
collect ──otherwise──► end
```

`enrich_company` is an ordinary node function: it runs the child graph for one
uid inside `try/except`, writes a `results` row, and returns
`{"results": [CompanyResult]}` to the parent. A raised exception in the child
becomes `CompanyResult(error=...)`; siblings are unaffected. `collect` counts
from the `results` table, not from state, so a rerun sees earlier results.

### 3.2 Child graph `enrich_company` — once per company

```
start → load_company → find_site ──no site──► end
                        └─yes──► pick_pages → read_pages → extract → check_profile → find_contact → recommend
recommend ──skip──► end
recommend ──send/hold──► draft → check_draft → end
```

Child nodes **raise** on infrastructure failure (search down, LLM error,
fetch timeout after retries); the wrapper turns that into an error result.
Findings ("no site", "no address") are normal returns.

## 4. State

Principle: state carries ids, counts and small typed records. The pool
(thousands of rows), scores and raw HTML live in SQLite and the disk cache.

**Parent:** `run_id, goal, about_me, municipality, batch_size, seed` (input) ·
`pool_count, kept_count` · `criteria` (SelectionCriteria) · `batch_uids` ·
`batches_drawn` · `pool_exhausted` · `results` (reducer `operator.add`,
CompanyResult per child) · `sendable_count`.

**Child:** `run_id, goal, about_me, uid` (from the wrapper) · `company` ·
`site` · `page_urls` (≤ 200) · `pages_to_read` (≤ `MAX_PAGES_PER_SITE`) ·
`page_texts` (per page ≤ `MAX_CHARS_PER_PAGE`) · `profile` · `contact` ·
`recommendation` · `draft`.

All records are Pydantic models and double as the LLM output schemas:

| Model | Fields |
|---|---|
| `CompanyRecord` | uid, name, legal_form, seat, street, postal_code, city, purpose, purpose_head |
| `SelectionCriteria` | must (list), must_not (list), positive_signals (list) — produced from the goal, shown to the user, stored in the manifest |
| `Score` | uid, score 0–100 (validated client-side), reason |
| `SiteChoice` | url, tier `uid` \| `address` \| `model`, evidence quote, page url, confidence; or none |
| `CompanyProfile` | description, size_signal, persons[{name, role, email?}], addresses[], flags `distributor_only`, `foreign_group` |
| `Contact` | name, role, email, `email_kind` seen \| constructed \| generic \| third_party, source site \| shab, source_url, linkedin_lead? |
| `Recommendation` | send \| hold \| skip, reason |
| `Draft` | subject, body, mailto_fits |
| `CompanyResult` | uid, recommendation?, error? (typed: search \| fetch \| llm \| other), finished_at |

No secrets in state. No checkpointer, so nothing is persisted outside `data/`.

## 5. Nodes

| Node | Does | Calls | async |
|---|---|---|---|
| `load_pool` | LINDAS → companies table; raises if empty | LINDAS, DB | |
| `screen_pool` | rule-based exclusion (liquidation, property-only head clause) | DB | |
| `write_criteria` | goal → `SelectionCriteria` (typed, editable, stored) | LLM | |
| `score_pool` | render `score.md` with the criteria; batches of 50; compare returned UID set with the sent set, re-send the difference, drop duplicates, validate range; **skip companies already scored for `(goal_hash, prompt@version, model)`** | LLM ×~80, DB | ✓ |
| `probe_search` | one known query must return ≥ 1 result, else the run stops before drawing | SearXNG | |
| `draw_batch` | next N unseen by score; record seen; reuse the recorded batch on rerun; set `pool_exhausted` | DB | |
| `enrich_company` (wrapper) | run the child for one uid in try/except; write the `results` row; on rerun skip uids with a non-error result | child graph, DB | ✓ |
| `collect` | count sendable from the `results` table; write the run report | DB | |
| `load_company` | company record from DB | DB | |
| `find_site` | 3 queries + domain guess → drop directory/social domains → fetch home + Impressum of ≤ 3 candidates → tier 1 UID match, tier 2 register street+postal code match, tier 3 model choice with a verbatim quote checked against normalised text → list pages (sitemap or links, ≤ 200) | SearXNG, sites, LLM, DB | ✓ |
| `pick_pages` | choose ≤ `MAX_PAGES_PER_SITE` pages | LLM | |
| `read_pages` | polite fetch with cache, clean text, Playwright when the "needs JS" detector fires | sites, disk | ✓ |
| `extract` | page texts (delimited as data) → `CompanyProfile` | LLM, DB | |
| `check_profile` | deterministic: every person name and email must occur verbatim in the page texts (else dropped); email domain ≠ site domain → `third_party` | — | |
| `find_contact` | typed rules: personal address on the site domain > SHAB name + generic address on the site domain > LinkedIn lead (web search restricted to linkedin.com, unverified); noise filters from the research (sentry/wixpress, `@2x`, Cloudflare decode) | SHAB, SearXNG, DB | |
| `recommend` | rules: no site / distributor / foreign group → skip; no contact or only third_party → hold; else send; one reason | — | |
| `draft` | German invitation: the model writes subject and middle from company, contact role, about_me and the profile description (delimited as data; M6 open point 1, #22); code adds greeting, survey link, revDSG sentence and closing; mailto length on the final body | LLM, DB | |
| `check_draft` | deterministic: no URL and no e-mail address in the model's text; the survey link is the body's only URL; ≤ 1,200 chars, greets the contact by name, contains the revDSG sentence; on failure one regeneration, then `hold` | — | |

Async only where a node issues many requests itself. The graph runs with
`ainvoke(config={"recursion_limit": 40})`; progress streams with
`stream_mode="updates", subgraphs=True`. Sync nodes run in LangGraph's thread
pool, so every DB access opens its own connection (see §8).

## 6. Integration modules ("tools")

| Module | Contract |
|---|---|
| `lindas` | `fetch_companies(municipality) -> list[CompanyRecord]`; httpx POST SPARQL; retry 3×; raises if empty |
| `search` | `SearchProvider` protocol: `search(query, limit) -> list[SearchResult]`; **raises `SearchError`** when SearXNG is unreachable or all baseline engines are unresponsive; returns `[]` for a real empty result. SearXNG default; Serper only on `SearchError` when a key is set. Queries pass a semaphore of 2 with a 1 s gap. Every query and raw response → `searches` table with `unresponsive_engines` |
| `fetcher` | `get(url) -> Page{url, status, html, error}`; `http(s)` only; refuses private/link-local IPs after DNS; disk cache; robots.txt + Crawl-delay; per-host delay 1 s; UA `company-reach/<version> (+repo)`; 2 MB cap; never raises for a single page, raises `FetchError` only when the site's home page cannot be reached after retries |
| `browser` | `render(url) -> str \| None`; Playwright service, optional |
| `textify` | `clean(html) -> str`; trafilatura `favor_recall`, `html2txt` below 80 words; normalises NBSP/whitespace |
| `shab` | `persons(uid) -> list[Person]`; `uids=` filter, `publicationStates=PUBLISHED`, XML detail per publication; DE/FR person-block parser; returns `[]` on empty, raises `ShabError` on HTTP failure |
| `llm` | `ask(prompt_name, variables, output_model) -> output_model`; loads `prompts/<name>.md`, renders with `string.Template`, `ChatOpenAI(base_url).with_structured_output(model, method=settings.llm_structured_method, include_raw=True)`; Pydantic validation; one retry on parse failure; raw text stored on failure; provenance (prompt@version, sha, model, usage) on every call; concurrency semaphore 5; raises `LlmError` after retry |
| `db` | `connect()` context manager: one connection per call, `PRAGMA journal_mode=WAL`, `busy_timeout=5000`, `autocommit=False`, `with conn:` transactions; `schema.sql` |
| `mailto` | `build(to, subject, body) -> MailtoLink{href, length, fits}`; RFC 6068 encoding; fits below 2,000 |
| `doctor` | startup checks: endpoint answers a tiny schema request with a valid parse; a marker at the end of a 20k-token prompt is echoed (context length); SearXNG JSON reachable; DB writable |

Clients are created once per module (httpx `AsyncClient`, `ChatOpenAI`);
DB connections per call. There is deliberately no `send_email`.

## 7. Libraries

| Concern | Library | Note |
|---|---|---|
| Graph | `langgraph>=1.2,<2`, `langchain-core` | StateGraph, Send |
| LLM | `langchain-openai` | `ChatOpenAI(base_url=…)` + `with_structured_output` |
| Schemas / settings | `pydantic`, `pydantic-settings` | `.env`, `SecretStr` |
| HTTP | `httpx` | async client, `Timeout(15, connect=5)` |
| HTML → text | `selectolax`, `trafilatura` | `html2txt` fallback under 80 words |
| robots.txt | `protego` | |
| JS fallback | `playwright` (separate container, optional) | |
| CLI | `typer` | |
| Storage | stdlib `sqlite3` + `schema.sql` | no ORM |
| Review page | `fastapi[standard]` + Jinja2 (`.html` templates, autoescape on) | plain forms; ten lines of inline JS for arrow keys |
| Tests | `pytest`, `pytest-asyncio`, `respx` (httpx mocking) | golden set gated by `RUN_LLM_EVALS=1` |
| Tooling | `uv`, `ruff`, Python 3.13 | `uv.lock` committed |

Rejected: Crawl4AI (95 packages, 1.65 GB image, attribution clause), Firecrawl
(AGPL, six containers), Brave Search API (terms forbid storing results),
litellm / instructor / pydantic-ai (weight or pins), sqlmodel (beta), Streamlit
(29 deps, rerun model), `ultimate-sitemap-parser` (GPL).

## 8. Data model (SQLite, `data/company_reach.db`)

```sql
companies   (uid PK, name, legal_form, seat, street, postal_code, city, purpose, purpose_head, imported_at)
runs        (id PK, goal, goal_hash, about_me, municipality, seed, batch_size, model, prompt_versions JSON,
             criteria JSON, started_at, finished_at, status, counts JSON, cost_usd)
scores      (uid, goal_hash, prompt_version, model, score, reason, scored_at,
             PRIMARY KEY (uid, goal_hash, prompt_version, model))       -- cache across runs
seen        (uid PK, run_id, batch_no, drawn_at)                        -- never redrawn
searches    (id PK, run_id, uid, query, provider, results JSON, unresponsive JSON, chosen_url, tier, evidence)
pages       (url PK, fetched_at, status, text, raw_path)                -- cache index
profiles    (run_id, uid, profile JSON, PRIMARY KEY (run_id, uid))
contacts    (id PK, run_id, uid, name, role, email, email_kind, source, source_url, linkedin_lead)
drafts      (id PK, run_id, uid, contact_id, subject, body, mailto_fits, prompt_version, model)
results     (run_id, uid, recommendation, reason, error_kind, error_text, finished_at,
             PRIMARY KEY (run_id, uid))                                 -- resume + retry key
ledger      (uid PK, status, address, draft_id, decided_at, note)        -- sent | skipped | never
suppression (key PK, reason, added_at)                                  -- uid or email; permanent
```

Every row that a run produces carries `run_id`. Raw LLM responses and the run
manifest go to `data/runs/<run_id>/`; fetched HTML to `data/cache/`. Access
rules: one connection per call, WAL, `busy_timeout`, explicit transactions.

Resume and retry: a rerun with the same `run_id` skips `load/screen/score`
when scores exist, reuses the recorded batch, and re-enriches only uids whose
`results` row has an `error_kind`. `company-reach retry <run_id>` does the
same on demand. `seen` is unaffected: a company is drawn once, but an error
is not a verdict.

## 9. Review page

`company-reach review` serves FastAPI on `127.0.0.1:8000` (inside Docker it
binds `0.0.0.0`, Compose publishes `127.0.0.1:8000` only). Design in
`ux/review-page-ux.md`, prototype `ux/review-onepager.html`.

One company fills the screen; nothing scrolls on a desktop. Top bar: goal ·
progress marks · "3 / 10" · sent this month (a number, not a limit). Left:
verdict chip with its one reason, company name, what it does, site, who to
write to (every address with its kind, the recommended one selected, LinkedIn
lead marked unverified), the evidence line. Right: the draft as a letter (To,
Subject, body, link length). Bottom bar: Skip with four one-tap reasons ·
Never again (confirm) · **Send**. ← → or the side arrows move between
companies; **S** sends; a decision slides to the next company.

Actions are POSTs to `/decide/{run}/{uid}`; Send writes the ledger row first
and then opens the `mailto:` (recorded-before-opened, so "contacted once,
ever" never depends on a link being followed). All POST routes reject
requests whose `Sec-Fetch-Site` is not `same-origin`/`none`. Only `http(s)`
links are rendered. `mailto:` per RFC 6068; above 2,000 encoded characters
the page says to copy the text.

## 10. Configuration

`.env` (gitignored; `.env.example` committed with every key):

```
LLM_BASE_URL=https://api.openai.com/v1     # any OpenAI-compatible endpoint
LLM_API_KEY=...
LLM_MODEL=...
LLM_STRUCTURED_METHOD=json_schema          # json_schema | function_calling | json_mode
LLM_CONCURRENCY=5
SEARXNG_URL=http://searxng:8080
SERPER_API_KEY=                            # optional; used only when SearXNG errors
PLAYWRIGHT_URL=                            # optional; empty = disabled
MAX_PAGES_PER_SITE=10
MAX_CHARS_PER_PAGE=8000
SCORE_BATCH_SIZE=50
MAX_BATCHES_PER_RUN=3
LANGSMITH_TRACING=false                    # traces would contain names and page text
```

`profile.toml` (gitignored): `goal` and `about_me`, overridable per run.

## 11. Repository layout

```
company-reach/
├── src/company_reach/
│   ├── cli.py            # typer: doctor · pool · run · retry · review · ledger · forget · import-v0
│   ├── settings.py       # pydantic-settings
│   ├── models.py         # Pydantic records (also LLM output schemas)
│   ├── graph.py          # parent StateGraph, enrich_company wrapper, fan-out
│   ├── enrich.py         # child StateGraph
│   ├── nodes/            # one file per node
│   ├── tools/            # lindas search fetcher browser textify shab llm db mailto doctor
│   ├── checks.py         # deterministic post-checks (profile, draft, mailto length)
│   ├── review/           # FastAPI app, templates/*.html, static css
│   └── schema.sql
├── prompts/              # criteria.md score.md pick_site.md pick_pages.md extract.md draft.md
├── tests/                # unit + contract tests; test_prompts.py (golden, opt-in)
│   └── fixtures/golden/  # committed, redacted/synthetic subset
├── searxng/settings.yml
├── compose.yaml · Dockerfile · pyproject.toml · uv.lock · .env.example
├── LICENSE (MIT) · NOTICE (LINDAS/Zefix + SHAB attributions) · PRIVACY.md
├── docs/design · docs/research · docs/plans · IMPLEMENTATION.md · LEARNINGS.md · README.md
└── data/  (gitignored: db, cache, runs, golden, v0)
```

## 12. Docker Compose

`app` (this image; `docker compose up` serves the review page,
`docker compose run --rm app run …` executes a pipeline run), `searxng`
(official image pinned to a dated tag, `formats: [html, json]`,
`limiter: false`, not published), `playwright` (profile `js`, only with
`--profile js`). `./data` is bind-mounted.

## 13. Prompts, untrusted content, evaluation

Prompts are Markdown files with a `name`/`version` header rendered with
`string.Template`. Every model output records `prompt@version`, the file's
short SHA, the model id and token usage.

**Untrusted content.** Page text enters only `pick_site`, `pick_pages` and
`extract`, wrapped as `<<<PAGE url=…>>> … <<<END>>>` with the instruction that
it is data to describe, never instructions to follow. After `extract`,
`check_profile` drops any person or email not present verbatim in the text and
marks emails outside the verified site domain `third_party`; `recommend`
turns `third_party`-only contacts into `hold`. `draft` sees typed fields only;
`check_draft` rejects URLs and e-mail addresses in the body.

**Golden set.** `data/golden/`: 30 labelled purpose texts; the 15 known
company/site pairs with saved page text and expected persons/emails/flags;
3–5 adversarial pages (injected instructions, a look-alike contact address, a
directory page, a group site with a different UID, a departed SHAB person). A
redacted subset with synthetic persons is committed under
`tests/fixtures/golden/` so a fresh clone can run the evaluation.
`tests/test_prompts.py` (with `RUN_LLM_EVALS=1`) grades: scoring (≥ 8 of 10
`yes` above every `no`), site choice (≥ 13/15, tier recorded), extraction
(set comparison), draft (deterministic checklist), adversarial cases (3/3
trials `hold` and no injected link). Runs n=2 and prints the spread; results
append to `data/evals/results.jsonl` with per-node cost.

## 14. Reproducibility and failure

Run manifest at start and end (`goal, criteria, seed, model, prompt versions,
settings, counts, cost per node, git commit`). `doctor` runs before the first
model call. Parent-stage failure stops the run loudly; a child failure yields
an error result that `retry` re-enriches; `recursion_limit=40` guards the one
loop. Ctrl-C cancels through `ainvoke`.

## 15. Legal, privacy, publication

- Zefix data via LINDAS: attribution required — `NOTICE` and README.
- SHAB via the Amtsblattportal API: source citation and "not an official
  publication" notice — `NOTICE` and README.
- Named people are personal data (revDSG): only under gitignored `data/`; the
  invitation states the source and how to be deleted; `company-reach forget
  <uid|email>` removes the person from `contacts`, `profiles`, `drafts`, the
  page cache and `data/runs/*/llm/`, then adds the suppression key; a
  retention purge removes cache and run files older than 12 months.
- `PRIVACY.md` states what leaves the machine: page text and SHAB person
  blocks go to the configured LLM endpoint (choose a local or EU endpoint if
  that matters); tracing is off by default and would contain names.
- v0's 22 examined companies are imported into `ledger`/`seen` before the
  first run (`import-v0`), so no company is contacted twice.
- No automated sending; a survey invitation sent by hand is not mass
  advertising under UWG (v0 research).
- Before the repository goes public: `LICENSE`, `NOTICE`, `.env.example`,
  `PRIVACY.md` present; workspace symlinks and notes removed; history free of
  `data/`.

## 16. Open questions

- Ethics approval for the thesis interviews — a gate before the first real
  `Send`.
- Which provider Koray runs first; `doctor` and the golden set answer whether
  json_schema holds there.
- Whether the Playwright service is ever needed — the "needs JS" counter
  decides.
