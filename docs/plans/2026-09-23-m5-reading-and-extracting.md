# M5 — Reading and Extracting Implementation Plan

Issue #5. Branch `feat/5-reading-and-extracting`, cut from `main` (M4 merged
as 3305a3e via #19; unlike M3→M4 the branches are not chained this time).

M5 is where the child graph stops being a site finder. Today `has_site`
returns `END` on both branches (`graph.py:162-165`): `find_site` names the
website, and the child is done. After M5 the `yes` branch runs
`pick_pages → read_pages → extract → check_profile`
(`graph.spec.yaml:193-197`), and a company reaches M6 carrying a
`CompanyProfile` in which every person and every e-mail address has been
checked against the page it was taken from.

It is also the milestone where the audit's single P1 lands. Page text has
been entering a prompt since M4 (`pick_site`), but M4 only ever let the model
*choose among URLs we already had*, and a verbatim quote check stood behind
that choice. M5 is the first time what the model says about untrusted text
becomes data the tool will act on: a person's name, a role, an e-mail address
that a human will later send an invitation to. The untrusted-content boundary
(`audit-2026-09-19.md:177`, `:184`) is therefore not one section of this
plan — it is most of it.

## What the design already settles

Not reopened here; listed so the plan reads without four documents open.

| Decision | Where |
|---|---|
| Child `yes` branch: `pick_pages → read_pages → extract → check_profile → find_contact` | `design.md:79`, `graph.spec.yaml:193-197` |
| `pick_pages`: choose ≤ `max_pages_per_site` from `page_urls`; LLM; sync | `design.md:133`, `graph.spec.yaml:158` |
| `read_pages`: polite fetch with cache, clean text, Playwright when the needs-JS detector fires; async | `design.md:134`, `graph.spec.yaml:159` |
| `extract`: page texts, delimited as data → `CompanyProfile`; LLM + DB; sync | `design.md:135`, `graph.spec.yaml:160` |
| `check_profile`: deterministic, no I/O; drop persons/emails not verbatim; email domain ≠ site domain → `third_party` | `design.md:136`, `graph.spec.yaml:161` |
| `CompanyProfile` = description, size_signal, persons[{name, role, email?}], addresses[], flags `distributor_only`, `foreign_group` | `design.md` model table (§4) |
| Page text wrapped `<<<PAGE url=…>>> … <<<END>>>` with a data-not-instructions line, in `pick_pages.md` and `extract.md` | `design.md:300-302`, `graph.spec.yaml:238`; already implemented at `find_site.py:293` and in `prompts/pick_site.md` |
| `recommend` (M6) turns `third_party`-only contacts into `hold`; `draft` sees typed fields only | `design.md:303-306` |
| Needs-JS detector: after the static fetch, `extract()` **and** `html2txt()` both under ~50 words **and** a framework mount point (`id="__next"`, `id="app"`, `id="root"`, `<script type="module">`, a `<noscript>` telling you to enable JavaScript) | `research/website-reading.md:109-115` |
| Playwright stays optional and unbuilt until the counter justifies it; 0 of 15 probe sites triggered the detector | `research/website-reading.md:99-115`, `design.md:355` |
| `browser.render(url) -> str \| None`, failure = keep the static text | `design.md:154`, `graph.spec.yaml:217` |
| trafilatura `favor_recall`, fall back below 80 words, normalise NBSP/ß | `research/website-reading.md:57-62`; already implemented in `tools/textify.py` |
| E-mail from three places: `mailto:` hrefs (strip `?subject=`), visible text, JSON-LD `"email"` | `research/website-reading.md:118-119` |
| E-mail noise filters: 32-hex local parts, `sentry`/`wixpress`/`example.`/`schema.org`/`w3.org` hosts, `@2x`/`@3x` and image extensions | `research/website-reading.md:120-125` |
| Cloudflare `data-cfemail` decode: first hex byte is the key, XOR the rest; a six-line function, not a dependency | `research/website-reading.md:126-132` |
| JavaScript-assembled addresses are an accepted loss; "no address on site" is a finding | `research/website-reading.md:133-135`, `LEARNINGS.md` §5 |
| Prefer the address whose domain matches the site; mark others third-party | `research/website-reading.md:136-137` |
| UID regex, normalisation and modulo-11 check digit | `research/website-reading.md:139-154`; already implemented in `tools/uid.py` |
| Caps: `max_page_urls` 200, `max_pages_per_site` 10, `max_chars_per_page` 8000, `max_page_bytes` 2 MB | `settings.py:59-63` |
| Cache `data/cache/<sha1(url)>.html` + JSON sidecar; the `pages` table is the index | `research/website-reading.md:171-173`, `design.md:197` |
| `pages(url, fetched_at, status, text, raw_path)` and `profiles(run_id, uid, profile)` tables already exist | `schema.sql:20-23` |
| Adversarial acceptance: a page carrying `ceo@evil.example` and "ignore previous instructions" yields `email_kind=third_party`, `recommend=hold`, a draft without the link, 3 of 3 trials | `audit-2026-09-19.md:186`, `:344` |
| Golden set: saved page text with expected persons/emails/flags, plus 3–5 adversarial pages; a redacted synthetic subset committed under `tests/fixtures/golden/` | `design.md:308-320` |
| Fixtures use fictional companies; real company and person data never leaves `data/` | `AGENTS.md`, hard rules |

## Contradictions between documents, and how this plan resolves them

| # | Conflict | Resolution |
|---|---|---|
| 1 | Pages per site: `research/website-reading.md:169` says "read at most 8 pages per site"; `design.md` and `audit:11` say 10, and `settings.py:59` ships `max_pages_per_site = 10` | **10.** The research note is the earlier document and the design was written after it, the same way the six M4 conflicts resolved. The number is a setting; nothing in the code should hard-code either. |
| 2 | `third_party` is an `email_kind` value on `Contact` (`design.md` §4 model table), but `check_profile` is the node that assigns it (`design.md:136`) — and `Contact` is not built until M6's `find_contact` | Open point 1 below. The mark has to live somewhere in M5, and `CompanyProfile.persons` is the only structure that exists. |
| 3 | Golden set sizes: `design.md:310` says "the 15 known company/site pairs"; M4 captured and hand-labelled **20** (`data/golden/sites/expected.jsonl`), of which 14 have a site | **20 / 14.** The 15 were v0's; M4's capture superseded them, and `tests/test_prompts.py` already measures against the 20. Extraction labels are added to the 14 with a site. |
| 4 | `design.md:197` and `research:171` call the `pages` table the index of the cache, but nothing reads or writes it — `Fetcher._store` (`fetcher.py:114-127`) writes the disk cache only, and `tools/db.py` has no `pages` query | The table is real and empty. Task 4 fills it, because M5 is the first milestone that needs page *text* again after the child graph has moved on. |

## What M4 left on M5's doorstep

**#16 — early stopping and prune patterns.** Filed against milestone M5 and
explicitly deferred out of M4. It matters *before* the new nodes, not after,
because `pick_pages` consumes exactly what the prune patterns produce:
`_BULK` in `find_site.py` drops `/produkt`, `/blog`, `/news` and dated paths
before capping the list at `max_page_urls`. Those patterns were chosen because
they sound right, and nobody has looked at the probe's sitemaps (1,659 and
4,599 URLs). If a pattern drops the wrong thing, `pick_pages` never sees the
page and `extract` pays for it — which is precisely what the issue says.

The issue asks to check both "against the twenty companies once their
fixtures exist". They exist now (`data/golden/sites/`), so #16 is ready and
goes first. Early stopping is the cheaper half and is cost, not correctness;
prune patterns are correctness and feed everything after them.

**#20 — making a throttled run visible.** Not M5 work, but M5 introduces a
second counter with the same homelessness problem: the needs-JS detector is
useless unless its hits are counted somewhere a human later reads
(`research:113`, `design.md:355`). Whatever Task 3 decides for the needs-JS
counter is the natural answer for #20 too, and the two should be decided
together rather than twice.

## Open points, and what Koray decided (2026-09-23)

The loop says one question per open point, answered before code
(`IMPLEMENTATION.md:13-16`). There were four; all four are settled.

**1. Where does the `third_party` mark live in M5?**
`check_profile` must mark an e-mail whose domain is not the verified site's,
but `email_kind` belongs to `Contact`, which M6 builds.

**Decided: a field on the person entry in `CompanyProfile`.** The evidence
travels with the thing it describes; M6's `find_contact` reads a decision
instead of repeating the comparison, and M7's review card can show why an
address was held without recomputing anything.

**2. How are the extraction labels produced, and for how many companies?**
The 14 golden companies with a site have saved candidate pages, but nothing
says what `extract` *should* return for them.

**Decided: label all 14; grade person names and e-mail addresses only.**
`description` and `size_signal` stay ungraded — both are prose, and an
exact-match threshold on prose mostly measures noise, which is the argument
`tests/test_prompts.py:14-18` already makes for exact score agreement.

**3. Does M5 build the needs-JS detector, the Playwright service, or neither?**

**Detector: built (`f5dd723`). Service: reopened, awaiting Koray's call.**

First decided as "both", against the plan's recommendation, on the evidence
then available: the probe had measured zero hits across 15 sites
(`research/website-reading.md:115`), which is a small sample to retire a
capability on. Task 3 then produced a larger one. Run over the 419 real
pages M4 left in the cache, the detector fires 3 times — `khatabook.com`,
`atlassian.com` and `swisswebcams.ch`, all candidate pages seen while
*finding* a site, none of them a page `read_pages` would ever be given. Of
the fourteen golden sites whose pages it would actually read, none trips it.

Koray parked the decision on seeing that, so `tools/browser.py` and the
Compose service are not written and `design.md:355` is left standing. The
detector and its count are in either way — that is what the decision will
be made on. Task 3b and Task 10 below are held, not cancelled.

For the record, the same 419 pages say the detector's shape is right: thin
text alone would fire 43 times and a framework mount point alone 98, while
the research note's conjunction of the two fires 3.

**4. Which pages does `pick_pages` get to see on a small site?**
`find_site` lists pages from the sitemap or from links, capped at 200
(`design.md:132`). For a small site the whole list may be under ten URLs, in
which case asking a model to choose is a call that cannot be wrong.

**Decided: skip the call when `len(page_urls) <= max_pages_per_site`** and
read them all. One branch; it saves a call and a round trip on the small
sites that are most of this population, and it makes the node's test trivial.

## Tasks

Each task is one commit with its test. Tests are network-free except where
marked; model calls are graded by the golden set, opt-in under
`RUN_LLM_EVALS=1` (`IMPLEMENTATION.md:41-44`).

**Task 0 — #16: prune patterns and early stopping. Done, 2026-09-23.**

*Prune patterns — fixed (`8cbafd6`).* Measured against the 14 golden page
lists first. The patterns turned out to have zero false positives — no
wanted page was ever matched — but they also matched almost nothing on the
only three sites where pruning matters, because those sites write the
plural: `/products/` 1,597 and 350 times, plus `/blogs/`, `/collections/`
and `/product-category/`, none of which the singular-only pattern reached.
With the plural endings and a hyphen separator the 200-URL cap no longer
bites on any of the fourteen, and the worst-hit site keeps 11 of its 12
wanted pages instead of 3.

*Early stopping — not done, and retired.* Its premise died twice over. The
stop signal was "a candidate passed the UID or Impressum check", but
`efffeaf` made the model the gate precisely because a directory page carries
the UID too; and deciding after query 1 shows the model fewer candidates,
which is the capped-at-three experiment `_MAX_CANDIDATES` already lost. The
saving was about ten seconds a batch, and no "candidate list is already
full" shortcut exists either — all three queries together reach the
ten-candidate cap for only 6 of 20 companies. `research/search-layer.md` §4
now carries the reasoning, so the next reader does not re-derive it.

**Task 1 — `CompanyProfile` and the profile store.**
The model in `models.py`, and `profiles` read/write in `tools/db.py`
(`schema.sql:22-23`).
*Test:* round-trip a profile through SQLite; a profile with no persons is
valid and distinct from a missing one.

**Task 2 — `nodes/pick_pages.py` and `prompts/pick_pages.md`.**
Render the URL list (not page text — there is none yet) and ask for ≤
`max_pages_per_site`. Skip the call under the threshold per open point 4.
Answers outside the offered list are dropped, the way `score_pool` checks the
returned UID set against the sent one.
*Test:* under the threshold, no model call and every URL kept; a model
answering with an unoffered URL has it dropped; an empty answer falls back to
the home page.

**Task 3 — `nodes/read_pages.py`, the needs-JS detector and the counter.**
Fetch the chosen pages through the shared `Fetcher`, `textify` each, truncate
to `max_chars_per_page`, and record a needs-JS hit per `research:109-115`.
The detector stays a pure function of the fetched HTML and its text, so it is
testable without a browser and still meaningful if the service is later
removed.
*Test:* `respx`-mocked pages; a framework-shell fixture trips the detector, a
thin-but-static one does not; truncation is at a word boundary; a page that
fails to fetch is skipped without failing the node.

**Task 3b — `tools/browser.py` and the Compose service (open point 3). HELD.**
Not written: the decision that called for it was parked once Task 3 measured
the detector against 419 real pages. What follows is what it would be.
`render(url) -> str | None` against a Playwright service, called only when
Task 3's detector fires, with the static text kept on failure
(`design.md:154`, `graph.spec.yaml:217`). Service in `compose.yaml` from
`mcr.microsoft.com/playwright/python`, pinned to a dated tag the way SearXNG
is, `--ipc=host` and `--init` per `research:103-106`, and `PLAYWRIGHT_URL`
already exists in settings (`settings.py:57`) and `.env.example`.
*Test:* `respx`-mocked service; a render that fails, times out or returns
empty leaves the static text untouched and does not raise; the node never
calls `render` when the detector did not fire. The service itself is not
exercised in tests — it is opt-in infrastructure, like SearXNG.

**Task 4 — write the `pages` table.**
Index what was read: url, fetched_at, status, text, raw_path
(contradiction 4).
*Test:* reading two pages leaves two rows; re-reading updates rather than
duplicates.

**Task 5 — `nodes/extract.py` and `prompts/extract.md`.**
Page texts wrapped `<<<PAGE url=…>>> … <<<END>>>` with the
data-not-instructions line, exactly as `pick_site.md` already does, into
`CompanyProfile`. Write the profile to `profiles`.
*Test:* the rendered prompt contains the delimiters and the instruction; a
page whose text contains `<<<END>>>` cannot close the block early.

**Task 6 — `nodes/check_profile.py` and `checks.py`.**
The P1 boundary. Drop any person name or e-mail not present verbatim in the
page texts, after the same normalisation the quote check uses
(`textify.normalise`, per `audit:178`); mark e-mails outside the verified
site's registered domain per open point 1. Apply the research's e-mail
filters (`research:120-137`).
*Test:* a name the model invented is dropped; a name present only with an
NBSP or ß difference is kept; `ceo@evil.example` on a site at `example.ch` is
marked, not dropped; each noise filter has a case.

**Task 7 — wire the child graph.**
`has_site` returns `pick_pages` on the yes branch; the four nodes in line to
`END` until M6 adds `find_contact`. Extend `ChildState` with `pages_to_read`,
`page_texts`, `profile` (`design.md` §4).
*Test:* a company with a site walks all four nodes; one without still ends
after `find_site`.

**Task 8 — `enrich --until profile`.**
Extend the existing `--until` so the demo command runs.
*Test:* CLI test on a stubbed child.

**Task 9 — adversarial fixtures and the extraction eval.**
Three to five poisoned pages under `tests/fixtures/golden/`, fictional
companies, committed (`design.md:317-319`): injected instructions, a
look-alike contact address, a group site with a different valid UID. Plus the
extraction eval in `tests/test_prompts.py` against the labels from open
point 2.
*Test:* the deterministic half runs without a model — injection and
look-alike cases are `check_profile`'s job and must pass with no endpoint.
The graded half is opt-in, n=2, printing the spread like the two evals
already there.

**Task 10 — reconcile the documents with open point 3. HELD with 3b.**
Only needed if the service is built; `design.md` §16 stands as written until
then.
`design.md` §16 no longer has "whether the Playwright service is ever needed"
as an open question, and its §5 `browser` row and the research note's
recommendation (`research:19-21`) say the service is built rather than
optional. The needs-JS counter's purpose changes with it: it no longer
decides whether to build, it measures whether the built thing was worth it.
*Test:* none — documentation. Reviewed as part of the milestone PR.

## Acceptance

- `company-reach enrich --uid CHE… --until profile` prints the profile
  (`IMPLEMENTATION.md:59`).
- Extraction eval on the 14 labelled companies, set comparison on persons and
  e-mails, baseline recorded in the test the way `SITE_BASELINE` is.
- Adversarial: a poisoned page yields a marked (not accepted) address and no
  invented person, deterministically, 3 of 3 (`audit:186`). The `recommend=hold`
  half of that criterion belongs to M6 and is checked there.
- `uv run pytest -q`, `ruff check`, `ruff format --check` green.
- Needs-JS counter reported for the run, so at M8 the Playwright service can
  be kept or dropped on a number rather than an opinion.
- `docker compose up` brings the Playwright service up and `read_pages`
  reaches it; with the service down, reading still succeeds on static text.

## Deliberately not in M5

- `recommend=hold` for third-party-only contacts — M6 owns `recommend`; M5
  only marks.
- `Contact` and `email_kind` — M6.
- The `pages` table as a *cache* — it stays an index; the disk cache in
  `Fetcher` remains the thing that avoids refetching.
