# M4 — Finding the Site Implementation Plan

Issue #4. Branch `feat/4-finding-the-site`, cut from `feat/3-parent-graph`
(M3's PR #11 is not merged — the two PRs are chained, and #12 cannot merge
before #11).

M4 gives the child graph its first real node. Until now `enrich_company`
awaited a one-node stub that said `skip` to everything; after M4 it awaits a
node that searches, fetches, verifies and either names a website with its
evidence or records that the company has none.

The milestone is also where the project first talks to the open internet, so
three audit items land here at once: the error-versus-finding rule (A4), the
URL scheme allowlist and SSRF guard (A5), and the verification tiers (A3).

## What the design already settles

Most of M4 is written down. These are not reopened; they are listed so the
plan can be read without the design doc open.

| Decision | Where |
|---|---|
| SearXNG default; Serper **only on `SearchError`**, never on empty results | `design.md:38`, `design.md:152`, `graph.spec.yaml:215` |
| Baseline engines: DuckDuckGo + Mojeek + Brave; Google is a bonus | `research/search-layer.md:155` |
| Search semaphore 2 with a 1 s gap, separate from the fetcher's per-host delay | `design.md:152`, `audit-2026-09-19.md:209` |
| Three queries: `"name" seat` · `name Impressum` · `"UID"` in both spellings | `research/search-layer.md:233-247` |
| Blocklist of 47 register, directory and social domains, verified 2026-09-19 | `research/search-layer.md:257-273` |
| Ranking: prefer a registered domain containing a name token; dedupe by registered domain | `research/search-layer.md:252-255` |
| Tier 1 UID regex + check digit; tier 2 postal-code line; tier 3 model with a verbatim quote | `research/website-reading.md:139-157`, `audit-2026-09-19.md:178` |
| Fetcher: `http(s)` only, refuse non-public IPs after DNS, 2 MB cap, `Timeout(15, connect=5)`, UA `company-reach/<version> (+repo)` | `design.md:153`, `audit-2026-09-19.md:246` |
| Cache `data/cache/<sha1(url)>.html` + JSON sidecar; `pages` table is the index | `research/website-reading.md:171`, `design.md:197` |
| robots.txt via `protego`, honour `Crawl-delay`, default 1 s per host | `design.md:174`, `research/website-reading.md:163` |
| trafilatura `favor_recall=True`, fall back to `html2txt` under ~80 words | `research/website-reading.md:53-57`, `design.md:155` |
| Page listing from sitemap or links, capped at 200 | `design.md:132`, `design.md:99` |
| "No site" is a finding **only when search worked**; infrastructure failures raise | `design.md:43`, `design.md:86` |
| SearXNG container pinned to a dated tag, `limiter: false`, no valkey | `design.md:286-292`, `research/search-layer.md:45-117` |

## Contradictions between documents, and how this plan resolves them

The design documents disagree in seven places. Six resolve the same way — the
design was written after the audit, the research notes were not corrected —
and are recorded here so nobody re-derives them.

| # | Conflict | Resolution |
|---|---|---|
| A | `research/search-layer.md:157` falls back to Serper on *empty* results; the design says errors only | Design wins. Paying for queries about companies that have no site is exactly what the audit rejected. |
| B | `research/search-layer.md:204` wants a manual `SEARCH_PROVIDER` switch | Design wins. No such key exists; the fallback is automatic and keyed on `SERPER_API_KEY` being set. |
| C | `recommend` owns "no site → skip", but the `has_site: no` edge ends the child before `recommend` runs | **Settled with Koray, below.** |
| D | 8 vs 10 pages per site | The setting wins (10). M5's problem, noted so it is not re-argued. |
| E | SearXNG port published on `127.0.0.1:8080` vs not published | **Published.** `.env.example` defaults `SEARXNG_URL=http://searxng:8080`, a container name that does not resolve from `uv run`. Without the published port M4 cannot be developed outside Docker. |
| F | Search client `Timeout(10, connect=5)` vs fetcher `Timeout(15, connect=5)` | Both, as written. Two clients with different jobs; the library table lists only one because it lists libraries, not clients. |
| G | `errors.py` says `FetchError` is "Raised by M5"; `IMPLEMENTATION.md:58` delivers the fetcher in M4 | Stale docstring, fixed in Task 2. |

## Decisions taken with Koray on 2026-09-20

1. **`find_site` writes its own verdict when no site is found** (resolves
   contradiction C). It returns `recommendation="skip"` with a reason naming
   what was tried, and the child ends. The graph shape does not change, and
   the verdict is written where the finding is produced. `recommend` then
   only ever sees companies that have a site — which is what its name says.

2. **The model is called for every company, even when tier 1 or tier 2
   already matched.** Tiers become confidence labels rather than a ladder
   that stops early.

   The cost is real and worth stating: with `LLM_CONCURRENCY=3` a batch of
   ten is about four waves of 20-40 s, so two to three minutes per batch and
   eight to ten minutes for a three-batch run, for site choice alone.

   The reason it is still right: the golden site evaluation was specified as
   "15 calls, assert ≥ 13 correct" (`research/llm-layer.md:191`). A ladder
   would call the model roughly twice in production while the evaluation
   called it fifteen times — the test would not be measuring the product.

3. **The model is the gate; the tiers are the label.** ~~A verified UID match
   beats the model.~~ **Revised 2026-09-20 after the first real run.**

   The original rule let a UID match stand against a "none of these", on the
   reasoning that a check-digit-verified UID cannot be a coincidence. It
   cannot — but that proves the page is *about* the company, not that it
   *belongs to* the company.

   The first real `enrich` found the difference immediately. A company
   directory publishes the UIDs of the firms it lists, so a directory page
   matched tier 1; the model saw a directory and rejected it; the rejection
   was overruled, and a directory was recorded as the company's website
   carrying the highest confidence label the system has. It is the same
   grounding-is-not-identity trap tier 3 was already built around.

   So a rejection stands. A UID still outranks everything as evidence — it
   decides which label a site carries — it no longer decides whether there
   is a site. When the register's UID sits on a page the model passed over,
   that is recorded as a note rather than acted on.

4. **M4 tests the mechanism; M5 measures the accuracy.** Every branch is
   tested offline with fictional companies: UID check digit, blocklist
   matching, the tier ladder, quote verification against normalised text,
   "none of these", the no-site verdict, and the SSRF guard. These fixtures
   carry no real company or person data, so they live in `tests/fixtures/`
   and are committed to the public repository.

   The ≥ 13/15 accuracy criterion (`design.md:314`, `audit-2026-09-19.md:152`)
   is **deferred to M5**, and M4's pull request must say so: the audit item
   stays open. The reason is that the fixtures it needs do not exist. The v0
   data has twenty companies with a known answer — thirteen with a site,
   seven without — but saved page text for only two of them, and none of the
   candidate pages a no-site case would need the model to reject.

### Routine decisions taken while planning

Ordinary engineering choices, recorded so they can be argued with.

- **Blocklist at `src/company_reach/data/blocklist.txt`**, inside the package,
  read with `importlib.resources` like `schema.sql`. Matching is on the
  registered domain **and all its subdomains**, so `jobs.linkedin.com` is
  blocked by the `linkedin.com` entry.
- **A robots.txt disallow skips that URL.** It is neither an error nor a
  finding: "no site" is a statement about the company, and a single refused
  page is not that. If the home page itself is disallowed, the candidate is
  dropped and the search continues.
- **The home page is retried twice, 2 s then 4 s**, the same shape as
  `llm.ask`. Only then `FetchError`.
- **The cache does not expire.** `--refetch` bypasses it
  (`research/website-reading.md:172`).
- **Query 4 (`site:.ch "name" seat`) is in scope**, used only when queries 1-3
  return nothing but blocklisted domains. The research says not to *start*
  with it, not to avoid it.
- **The SearXNG port is published on `127.0.0.1:8080`** (contradiction E), and
  `SEARXNG_SECRET` is added to `.env.example`, which the compose snippets
  require and the example file omits.
## The four open points, settled 2026-09-20

Koray asked for these to be decided rather than asked, the way the routine
decisions above were. Each carries its reasoning so it can be argued with.

### 1. The domain guess

Built from the name, not from search: strip the legal form (`AG`, `GmbH`,
`SA`, `Sàrl`, `SAGL`) and `in Liquidation`, lowercase, transliterate umlauts
the German way (`ä→ae`, `ö→oe`, `ü→ue`, `ß→ss` — `müller.ch` and
`mueller.ch` are different hosts and the second is the common registration),
drop everything that is not a letter or digit.

Four candidates: the tokens joined and the tokens hyphenated, each with `.ch`
and `.com`. `.swiss` is left out — it is restricted and rare. Each is
resolved by **DNS only**; a name that does not resolve costs nothing and is
dropped. Those that resolve join the candidate list.

The point that keeps this honest: **a guessed domain is a candidate, not an
answer.** It goes through the same three tiers as a search result, so a
parked domain or a namesake in another canton fails verification like
anything else. No special trust, therefore no special rule.

### 2. Pruning to 200 URLs, and the no-sitemap path

With a sitemap: follow `<sitemapindex>`, collect every `<loc>`. If more than
200 remain, prune in this order, stopping as soon as 200 is reached.

1. Drop bulk paths — `/produkt`, `/product`, `/shop`, `/blog`, `/news`,
   `/artikel`, `/tag/`, `/category/`, `/kategorie`, and any segment that
   looks like a year (`/20\d\d/`). These are the pages a sitemap has
   thousands of and a company profile needs none of.
2. Sort by path depth, then by length. What M5 needs — Impressum, Kontakt,
   Über uns, a products overview — sits near the root. Deep paths are
   individual items.
3. Take the first 200.

Without a sitemap: harvest the links of the home page, same host only, one
level deep. That reaches Impressum, Kontakt and Über uns, which is what the
next milestone reads. Crawling deeper is M5's problem if M5 finds it needs
to, and guessing now would be building for an unmeasured need.

Normalisation before the cap: lowercase scheme and host, drop the fragment,
drop a trailing slash, then dedupe.

### 3. "All baseline engines unresponsive"

`baseline_engines` joins `settings.py`, defaulting to
`duckduckgo,mojeek,brave` (`research/search-layer.md:155`). SearXNG reports
`unresponsive_engines` in every response.

`SearchError` is raised when the request itself fails or times out, **or**
when the result list is empty *and* every baseline engine is in
`unresponsive_engines`.

Both halves of the second condition matter. One or two suspended engines is
the normal weather — the research recorded suspension timers from three
minutes to fifteen days — so a suspension alone must not raise. And if
results came back, they came from somewhere; an unresponsive baseline is then
irrelevant. The error is for the one case where the empty list is not an
answer but an absence of one, because reporting "no site" then would be a
lie about the company.

### 4. `pick_site.md` inputs

Receives the register's facts to match against — name, seat, street, postal
code, UID — and up to **three** candidates, each with its home page text and
its Impressum text, **3,000 characters per page**.

3,000 rather than the 8,000 of `max_chars_per_page`: an Impressum is short
and the identifying lines sit at the top, and the endpoint's million-token
window is not the constraint — attention is. Three candidates × two pages is
then about 6,000 tokens.

It returns `chosen_url` (or null for "none of these"), `quote`, and `reason`.
**It is not asked for a confidence number.** `SiteChoice` has a confidence
field (`design.md:110`), and it is filled in code from the tier that verified
the site. A model's self-reported confidence is poorly calibrated, and we
already hold a better signal: whether a check-digit-verified UID matched.

## Global constraints

- Test first. Write the failing test, run it, see it fail, then write code.
- Files English, conventional commits ≤ 60 chars.
- The whole suite stays offline. Network is mocked with `respx`, as in M2.
- Every child node stays a plain function callable without the graph.
- Child nodes **raise** typed errors; `enrich_company` is the only catcher.
- No real company or person data in `tests/fixtures/`.

## File structure

```
compose.yaml                     # new: app + searxng (+ playwright, profile js)
searxng/settings.yml             # new
.env.example                     # + SEARXNG_SECRET
src/company_reach/
  data/blocklist.txt             # new: 47 domains
  errors.py                      # FetchError docstring: M4, not M5
  settings.py                    # + search_concurrency, per_host_delay_s,
                                 #   baseline_engines, max_page_urls,
                                 #   pick_site_chars
  tools/uid.py                   # new: UID regex + check digit
  tools/blocklist.py             # new: loader + subdomain matching
  tools/textify.py               # new: trafilatura + html2txt fallback
  tools/fetcher.py               # new: get(url) -> Page
  tools/search.py                # new: SearchProvider, SearXNG, Serper
  nodes/probe_search.py          # the real body
  nodes/find_site.py             # new
prompts/pick_site.md             # new
tests/
  fixtures/sites/                # new: fictional companies, saved HTML
  test_uid.py  test_blocklist.py  test_textify.py
  test_fetcher.py  test_search.py  test_find_site.py
```

Five tasks, merged from eight at Koray's request on 2026-09-20. The merges
follow the seams that were already there: the three deterministic helpers
share one test cycle, `probe_search` is three lines on top of `search`, and
page listing was always part of `find_site`'s contract in the design. The
fetcher stays alone because the audit's two security items live in it, and a
break there should be obvious about where it broke.

---

### Task 1: the deterministic helpers

**Files:** `tools/uid.py`, `tools/blocklist.py`, `tools/textify.py`,
`data/blocklist.txt`.
**Test:** `tests/test_uid.py`, `tests/test_blocklist.py`,
`tests/test_textify.py`

Three pure pieces, no network anywhere, and everything later leans on them.

The check digit is what makes tier 1 decisive: weights 5,4,3,2,7,6,5,4, sum
mod 11, digit = 11 − remainder, 11 counts as 0, 10 is invalid
(`research/website-reading.md:139-153`).

- [ ] **Step 1** — failing tests. *UID:* every spelling
      (`CHE-123.456.789`, `CHE123456789`, `CHE 123 456 789`, trailing
      `MWST`/`TVA`/`IVA`/`VAT`/`HR`) normalises to `CHE123456789`; an invalid
      check digit is rejected; a *different* valid UID is reported as such,
      not as "no match". *Blocklist:* `linkedin.com` and `jobs.linkedin.com`
      are both blocked, `mylinkedin.com` is not; the file carries 47 entries.
      *Textify:* a normal page extracts its text; a page whose `favor_recall`
      extraction is under 80 words falls back to `html2txt` and keeps the
      address block; whitespace, NBSP and `ß` normalise so that a later
      substring match cannot fail on typography alone.
- [ ] **Step 2** — run, see them fail.
- [ ] **Step 3** — write them.
- [ ] **Step 4** — green.

---

### Task 2: the fetcher

**Files:** `tools/fetcher.py`, `errors.py` (docstring), `settings.py`.
**Test:** `tests/test_fetcher.py`

The security surface of the milestone. Audit A5's two items live here, and
they are the reason this task was not merged into another.

- [ ] **Step 1** — failing tests, all offline: `javascript:` and `data:` URLs
      are refused before any request; `http://127.0.0.1/`, `http://10.0.0.1/`,
      `http://169.254.169.254/` and a hostname that *resolves* to a private
      address are refused after DNS; a response over 2 MB is dropped; a
      cached URL is not fetched twice; `--refetch` bypasses the cache; a
      robots.txt disallow skips the URL without raising; `Crawl-delay` is
      honoured; a single failed page returns a `Page` carrying the error and
      does **not** raise; a home page failing twice raises `FetchError`.
- [ ] **Step 2** — run, see them fail.
- [ ] **Step 3** — write it.
- [ ] **Step 4** — green.

---

### Task 3: search and `probe_search`

**Files:** `tools/search.py`, `nodes/probe_search.py`, `settings.py`.
**Test:** `tests/test_search.py`, extend `tests/test_graph.py`

- [ ] **Step 1** — failing tests: a SearXNG JSON response maps to results; an
      empty `results` list returns `[]` and does **not** raise; SearXNG
      unreachable raises `SearchError`; an empty result **with** every
      baseline engine in `unresponsive_engines` raises, while one
      unresponsive engine does not, and a non-empty result never raises
      whatever `unresponsive_engines` says; Serper is called only after a
      `SearchError` and only when a key is set; Serper failing too raises
      `SearchError`; the semaphore holds at two in flight with a gap;
      `probe_search` raises when its known query returns nothing and writes
      no updates when it returns something.
- [ ] **Step 2** — run, see them fail.
- [ ] **Step 3** — write them.
- [ ] **Step 4** — green.

---

### Task 4: `find_site`

**Files:** `nodes/find_site.py`, `prompts/pick_site.md`, `settings.py`.
**Test:** `tests/test_find_site.py`

The node itself, once its parts are proven — queries, the domain guess,
candidate filtering, the three tiers, and the page listing that the design
puts in the same contract.

**Koray reads the plan and the first three tasks before this one starts.**
The three tiers are the milestone's judgement, and it is the right place to
stop.

- [ ] **Step 1** — failing tests, fictional companies throughout.
      *Candidates:* queries are built in order with the legal form stripped;
      the domain guess produces at most four names, transliterates umlauts,
      and only DNS-resolving ones survive; blocklisted results never reach
      the model; candidates are deduped by registered domain.
      *Tiers:* a UID match records tier `uid`; an address match without a UID
      records tier `address`; neither, plus a model choice whose quote is
      found verbatim, records tier `model`; a quote **not** found in the page
      is rejected; a UID match plus a model "none of these" accepts the site
      and records the disagreement; an address match plus a model "none of
      these" rejects the site.
      *Outcomes:* no candidate at all returns `recommendation="skip"` with a
      reason naming the queries tried; search failing raises `SearchError`
      rather than reporting "no site".
      *Pages:* a sitemap index is followed; 4,000 URLs prune to 200 with bulk
      paths dropped first and shallow paths kept; no sitemap falls back to
      same-host home-page links; `page_urls` is normalised and deduped.
- [ ] **Step 2** — run, see them fail.
- [ ] **Step 3** — write the node and the prompt.
- [ ] **Step 4** — green.

---

### Task 5: wiring, compose, and the finish

**Files:** `graph.py`, `compose.yaml`, `searxng/settings.yml`,
`.env.example`, `cli.py`.
**Test:** `tests/test_graph.py`, `tests/test_cli.py`

The stub child is replaced by a real one: `load_company → find_site`, with
`has_site` routing to `__end__` for now (M5 adds `pick_pages`).

- [ ] **Step 1** — failing tests: the child graph runs `find_site` and
      returns a recommendation or a site; `enrich --uid … --until site`
      prints the site, tier and evidence; `run` without `--dry` no longer
      refuses.
- [ ] **Step 2** — run, see them fail.
- [ ] **Step 3** — write them; then `docker compose up searxng` and one real
      query by hand, because every other test in this milestone is mocked and
      something has to touch the real thing once.
- [ ] **Step 4** — green.
- [ ] `docs/milestones/m4-finding-the-site.md`, Turkish, local only.
- [ ] Close audit A5's URL-scheme and SSRF items with their tests.
- [ ] **State plainly in the PR that the ≥ 13/15 site-choice criterion is not
      met in M4 and why**, with the fixture work it needs, so the audit item
      is deferred on purpose and not by omission.
- [ ] PR, review, merge — after #11.

## Known rough edges

- `find_site` does search, fetch, verification and page listing. That is four
  jobs, and it is the design's shape, not a slip. If M5 finds it hard to
  follow, the page listing is the natural piece to split off.
- The two chained pull requests mean any change to M3 forces a rebase here.
- Merging eight tasks into five means a failing test localises less precisely.
  The fetcher was kept separate for exactly that reason; if Task 4 becomes
  hard to debug, the page listing splits back out.

## Self-review

- Every task writes its failing test first and runs it.
- The whole suite stays offline; no test reaches the network.
- Audit A3, A4 and A5's M4 items each have a task and a test, except the
  accuracy criterion, which is deferred in writing.
- No real company or person data enters `tests/`.
