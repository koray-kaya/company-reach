# Learnings from v0

v0 was a Claude Code skill plus a few helper scripts in a separate, private
repository (2026-09-12 → 2026-09-19), used to find interview participants for
a master's thesis. It ran four batches over St. Gallen city and enriched 22
companies by hand-driven steps. These are the lessons worth keeping. Its code
is not ported.

Company examples are described, not named: this file will be public.

## 1. The pool — LINDAS

- LINDAS (`https://lindas.admin.ch/query`, SPARQL, no key) is the only open
  source that can list companies by municipality. Graph
  `https://lindas.admin.ch/foj/zefix`; filter on `admin:municipality` and the
  legal form (`0106` AG, `0107` GmbH). St. Gallen city (`3203`): 8,448
  companies, 5,462 AG + GmbH.
- The **ZEFIX REST API cannot enumerate**: a canton query returns
  `RESULTLIST_TO_LARGE` and `name` is mandatory. It also returns no persons.
- LINDAS carries name, legal form, address, purpose and UID — **no website,
  no persons** (checked 2026-09-19).
- Terms: the Zefix dataset's own `dcterms:rights` is `Provide-the-Source`
  (OPEN BY): commercial use allowed, **attribution required** — verified
  2026-09-19; v0 had recorded it as "recommended".

## 2. Reading the purpose text

- **Read the head clause only.** 96.4% of purpose texts end in the boilerplate
  `Die Gesellschaft kann Zweigniederlassungen … Grundstücke erwerben`.
  Matching "real estate" over the full text flags 51.4% of St. Gallen
  companies; over the head clause, 18.3%. Split on `Die Gesellschaft kann` /
  `Sie kann`.
- **The purpose text excludes, it does not classify.** 35.7% contain the
  `Waren aller Art` catch-all. Trust it for "definitely not", never for
  "definitely a manufacturer".
- `Dienstleistung` and `Entwicklung` are not evidence of an operating business:
  property firms use both. Counting them let 713 property companies through.
- Cheap exclusions (no LLM): `in Liquidation` in the name (6.1%), property-only
  head clauses (20.8%). 5,462 → 3,993 kept.
- **Narrowing a filter moves the failure, it does not remove it.** A loose
  manufacturing screen let in a nail studio, a web shop and a publisher. The
  strict screen (3,993 → 396) fixed that, and instead raised the share of
  Swiss arms of foreign groups to 3 in 10 — real manufacturers are more often
  part of a group.
- Keyword screens match the *customer's* industry too: a distributor of tools
  *for* machining matched `Zerspanung`. Same shape: `Maschinenbau` in a
  consulting clause, `Fertigung` in a software clause.

## 3. What the register does not show

Two disqualifiers are invisible in the register and cheap to spot on the site:

- **The purpose overstates.** It records what a company was permitted to do at
  incorporation. Case: registered for `Fabrikation` of security systems, the
  site describes an importer of other makers' hardware.
- **Foreign-group subsidiaries look like Swiss SMEs.** Case: registered in
  St. Gallen, website and Impressum belong to the German parent. A group
  domain, a foreign Impressum address or no Swiss contact → different subject.

## 4. Finding the website

- v0 found sites by hand (search + Impressum check) and recorded only the
  result, not the queries — not reproducible. The rebuild must log what it
  tried.
- 5 of 20 companies had no findable website at all. That is a finding about
  the population, not a failure.
- Measured 2026-09-19 on 15 known sites. A quick static fetch found the UID
  on 4; a proper probe (`httpx` + `selectolax` + `trafilatura`, Impressum and
  contact pages) found it on **7 of 15**, a clean e-mail on 13, a Swiss postal
  code on 13. One site shows a *different* UID (the group's entity — a useful
  negative signal). UID match is decisive when present, absent on about half.
- **JavaScript is not the problem we feared.** All three Wix sites served their
  full text statically (Wix renders server-side for crawlers); 0 of 15 sites
  needed a browser. Keep a cheap detector (static text < 50 words and a
  framework mount point in the HTML) and a Playwright fallback as an optional
  container; build it only if the counter says so.
- Search options researched 2026-09-19:
  - **SearXNG** — actively maintained (commits daily, ~37k stars, AGPL-3.0;
    running it as a separate container does not put our code under AGPL). It
    has no index of its own: it forwards to Google, Bing, DuckDuckGo, Brave,
    which may block or change and break an engine for a while.
  - **Brave Search API** — the free tier ended February 2026; $5/1,000 with
    $5 monthly credit, card required. Its terms (2026-09-01) **forbid storing
    or caching results**, which the run log needs — ruled out.
  - **Serper** — 2,500 free queries, no card, then $1/1,000; Google-quality
    results, no term against keeping them. The paid fallback of choice.
- Search results alone are too thin for a model to pick from: it guesses by
  name similarity, confidently. Give it the candidates' Impressum text, allow
  "none of these", and ask for a verbatim quote that the code checks against
  the page.

## 5. Finding the person

- A named mail answers at roughly 8–15%, `info@` at 2–6%. At a budget of 100
  mails a month, the name is worth real work.
- `info@` is fine when it is all there is — never with `Sehr geehrte Damen und
  Herren`. A named greeting to a general inbox gets forwarded.
- Order of sources: the company's own site (address, and the current
  role-holder) → **SHAB** (`https://www.shab.ch/api/v1/publications`, no key)
  for the name when the site names nobody → `info@` with that name →
  LinkedIn by hand.
- SHAB publishes persons only when a company files a mutation. Pilot (n=12):
  5 named a person in a target role, 4 had no publication at all, 2 had no
  person block, 1 had no stated function. Scan every publication, not only the
  newest.
- **SHAB names a past state**, not today's role-holder. In one case SHAB, a
  business directory and the company's site named three different people.
  Where the site names someone, the site wins.
- SHAB silently ignores unknown parameters instead of rejecting them — v0
  tried `uid=` and concluded the filter did not exist. The documented
  `uids=CHE-XXX.XXX.XXX` (plural, dotted) works and returns a company's full
  publication history; `keyword=` finds by UID or name; sorting works as
  `pageRequest.sortOrders=column:PUBLICATION_DATE|direction:ASC`. The general
  lesson stands: verify that a filter changed the result before trusting it.
- `publicationStates=PUBLISHED` is mandatory (401 without it). The list call
  carries no text; the publication text is in `GET /publications/{id}/xml`.
- Dead ends: ZEFIX REST (no persons), LINDAS (no person predicate), the
  cantonal register portal (JSF + login).
- The funnel's bottleneck moved from *finding companies* to *finding
  mailboxes*: 4 of 10 on-profile companies published no reachable address,
  including the best fit of a batch.
- LinkedIn: a web search restricted to `linkedin.com` finds public profiles
  without touching LinkedIn; a name alone gives false matches (a namesake in
  another canton). Lead only, checked by a human. LinkedIn's terms forbid
  scraping. A connection note is ~300 characters: needs its own short variant.

## 6. Writing and sending

- **Sending stays human.** OST runs Microsoft 365; third-party app consent is
  blocked and basic-auth SMTP is off. A `mailto:` link that opens Outlook
  pre-filled worked well.
- Plain text, no attachments, short. States the thesis at OST, about 30
  minutes, explicitly not a sales call, evaluation anonymised.
- revDSG information duty goes in the mail itself: where the name came from
  (public commercial register), what it is used for, how to have it deleted.
- UWG Art. 3(1)(o): a survey invitation is not mass advertising (Zurich
  Obergericht, 20.07.2009); the test is automated distribution. Hand-sending
  keeps it out of scope. No product name, no pitch.
- **A company is contacted once, ever.** Keep a ledger and a permanent
  suppression list; honour deletion requests forever.
- Every drawn company is recorded, so no company is reconsidered, and batches
  are drawn with a recorded seed so any batch can be redrawn.

## 7. Human review

- A card with findings but no recommendation makes the reviewer derive the
  decision every time. State send / skip and the one reason, up front.
- Show every address found, what each one is, and which to use. Mark a
  constructed address (`vorname.nachname@` from a seen pattern) as
  constructed — a bounce costs a mail out of 100.
- When a batch yields nothing worth sending, keep drawing — with a cap and a
  report of what was spent.

## Open questions carried over

- What share of the websites-without-a-name does SHAB actually fill? v0 only
  measured coverage over the whole pool; batch 1 gave 0 of 2. (was #28)
- ~~SHAB's terms of use were never found.~~ Found 2026-09-19: Amtsblattportal
  AGB (07.06.2024) allow personal and commercial use via the API, require a
  source citation and a "not an official publication" notice, and threaten an
  IP block for violations.
- Ethics approval for the interviews, and for inviting individuals named in the
  public register — open with the thesis supervisors.
