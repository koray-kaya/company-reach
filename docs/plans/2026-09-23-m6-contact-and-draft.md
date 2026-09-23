# M6 — Contact and Draft Implementation Plan

Issue #6. Branch `feat/6-contact-and-draft`, cut from `main` (M5 merged as
aab045d via #23).

M6 is where the tool stops being a research pipeline and starts producing
something a human sends. After it, `company-reach run` takes a batch of ten
from the pool and leaves ten rows a reviewer can act on: a company, a
person, an address with its provenance, a send / hold / skip verdict, and a
German invitation short enough to fit in a `mailto:` link.

It is also the milestone that closes the audit's **other** P1 — the one M4
and M5 only partly answered. `audit-2026-09-19.md:207` describes it: an
infrastructure failure today consumes a company for ever, because `seen` is
written at draw time and a rerun skips anything with a result. M4 gave us
typed errors and a search probe; M6 owes the other half, `error` rows that a
rerun and a `retry` command pick back up.

And it is the first milestone where the tool touches a **person**. Everything
before this was about companies; from here on the records are personal data
under the revDSG, and the constraints in `LEARNINGS.md` §6 stop being
background.

## What the design already settles

| Decision | Where |
|---|---|
| Child tail: `check_profile → find_contact → recommend → (draft → check_draft) → END` | `design.md:79-81`, `graph.spec.yaml:197-201` |
| `find_contact` order: personal address on the site domain > SHAB name + generic address on the site domain > LinkedIn lead, unverified | `design.md:137`, `graph.spec.yaml:162` |
| `recommend`: no site / distributor / foreign group → skip; no contact or only third-party → hold; else send; one reason | `design.md:138`, `graph.spec.yaml:163` |
| `check_draft`: no URL, no e-mail, ≤ 1,200 chars, greets by name, carries the revDSG sentence; one regeneration, then `hold` | `design.md:140`, `graph.spec.yaml:165` |
| `Contact` = name, role, email, `email_kind` seen \| constructed \| generic \| third_party, source site \| shab, source_url, linkedin_lead? | `design.md:112` |
| `Draft` = subject, body, mailto_fits | `design.md:114` |
| `shab.persons(uid)`: `uids=` filter, `publicationStates=PUBLISHED`, XML detail per publication, DE/FR person parser; `[]` on empty, `ShabError` on HTTP failure | `design.md:156` |
| `mailto.build(to, subject, body)`: RFC 6068, fits below 2,000 encoded characters | `design.md:159`, `design.md:236` |
| `drafts`, `results`, `ledger`, `contacts` tables already exist | `design.md:200-203`, `schema.sql` |
| Sending stays human; no automatic mail, ever | `AGENTS.md` hard rules, `LEARNINGS.md` §6 |
| A company is contacted once, ever: ledger plus a permanent suppression list | `LEARNINGS.md` §6 |
| The revDSG duty goes in the mail: where the name came from, what it is for, how to have it deleted | `LEARNINGS.md` §6 |
| No product name, no pitch — a survey invitation is not advertising only while it is hand-sent | `LEARNINGS.md` §6, UWG Art. 3(1)(o) |

### What the research measured, and it shapes the rules

| Finding | Where |
|---|---|
| A named mail answers at 8–15%, `info@` at 2–6%. The name is worth real work | `LEARNINGS.md` §5 |
| `info@` is fine when it is all there is — **never** with "Sehr geehrte Damen und Herren". A named greeting to a general inbox gets forwarded | `LEARNINGS.md` §5 |
| **4 of 10 on-profile companies published no reachable address at all**, including the best fit of a batch. The bottleneck is mailboxes, not companies | `LEARNINGS.md` §5 |
| SHAB pilot (n=12): 5 named a person in a target role, 4 had no publication, 2 had no person block, 1 no stated function. Scan every publication, not only the newest | `LEARNINGS.md` §5 |
| **SHAB names a past state.** In one case SHAB, a directory and the site named three different people. Where the site names someone, the site wins | `LEARNINGS.md` §5, `data-sources.md:187` |
| `uids=CHE-000.000.003` works — plural, dotted only; `CHE000000003` returns nothing. `uid=` is silently ignored, which is the v0 pitfall | `data-sources.md` §B2 |
| `publicationStates=PUBLISHED` is mandatory; without it the API answers 401 | `data-sources.md` §B1 |
| The list call carries no text. The notice is in `GET /publications/{id}/xml` | `data-sources.md` §B1 |
| Always assert a filter changed `total` before trusting it | `data-sources.md` §B2 |
| SHAB's terms: cite the source, do not create the impression of an official document; Art. 11 para. 3 VSHAB by analogy for republishing persons | `data-sources.md:195-210` |
| LinkedIn: a restricted web search finds public profiles without touching LinkedIn; a name alone gives false matches. Lead only, human-checked, never scraped | `LEARNINGS.md` §5 |

## Contradictions between documents

| # | Conflict | Resolution |
|---|---|---|
| 1 | `draft`'s inputs: `design.md:139` says "typed fields only (company, contact, goal, about_me)"; `graph.spec.yaml:164` has `reads: [company, profile, contact, goal, about_me]` | **The spec**, settled in open point 1; `design.md:139` is corrected by Task 5. See #22. |
| 2 | `design.md:138` and `graph.spec.yaml:163` say `recommend` holds on "only third_party", using `Contact.email_kind`. M5 marks `Person.email_offsite` instead, because `Contact` did not exist yet | Mechanical: `find_contact` maps `email_offsite` to `email_kind="third_party"`. Written down so nobody re-derives it. |
| 3 | `design.md:79` draws `recommend` as unconditional, `graph.spec.yaml:199` routes `skip` straight to `__end__` | The spec. A skipped company needs no draft, and drafting one would spend a model call on a row the reviewer will not read. |

## What M5 left on M6's doorstep

**#22 — `description` can legitimately carry hostile text.** `extract.md`
tells the model that a page which addresses it should be described rather
than obeyed, and measured on the live endpoint it does exactly that: one of
three trials returned a description quoting the injected instruction,
including `https://evil.example/offer`. Under `design.md:139` that string
never reaches the drafting prompt; under `graph.spec.yaml:164` it does, and
`check_draft`'s no-URL rule becomes the last line of the P1 defence rather
than a tidiness rule. Open point 1.

**Presence is not employment.** `tests/test_adversarial.py` asserts the limit
in writing: a page naming an invented person beside the company's own address
gets past `check_profile`, because the verbatim check proves the string is on
the page, not that the person works there. SHAB is the first source of truth
about who held a role — but it describes a *past* state, and the research
rule is that the site wins where it names someone. Open point 3.

**Six names, no addresses.** The live M5 run on one golden company returned
six people and not one e-mail address; that site takes contact through a
form. This is the ordinary case, not the exception — the research measured 4
of 10 with no reachable address. What `find_contact` does with a known name
and no address is open point 2, and it decides whether the tool produces
anything for most companies.

**#20 — a throttled run is still invisible.** Two counters now have nowhere
to live: search's empty answers and M5's needs-JS hits. `run` writing a
manifest for the first time is the moment to settle it. Not a blocker for
M6, but M6 is when it becomes cheap.

## Open points

Four, to be answered before code (`IMPLEMENTATION.md:13-16`).

**1. Does the drafting prompt see the profile?**

**Decided: yes.** `graph.spec.yaml:164` wins and `design.md:139` gets
corrected. The plan recommended the opposite and the recommendation was
wrong on its main point.

What it got wrong: the invitation's `To` address does not come from the
drafting prompt at all. `find_contact` decides it from typed rules, so
"the invitation goes to the attacker" was already closed by `check_profile`
and `find_contact` before this question was asked. What actually remains is
an attacker-chosen URL or phrasing in the *body* of a mail addressed to the
company — and `design.md:140` already rejects a body containing a URL or an
e-mail address.

What it underweighted: personalisation is measurably worth something in this
domain. A named mail answers at 8–15% against 2–6% for `info@`
(`LEARNINGS.md` §5), and a sentence that shows we know what the company
makes is the point of the tool rather than decoration.

**The condition that comes with it.** `check_draft` is now the guard that
matters rather than a tidiness rule, so Task 6 tests it against a draft
generated from `tests/fixtures/golden/poisoned_instructions.html`, not only
against a well-behaved one. The residual risk is stated rather than
dismissed: non-URL steering survives every deterministic rule, and the
control is a reviewer the audit itself describes as skimming ten cards
(`audit-2026-09-19.md:177`).

**2. What does `find_contact` do with a name and no address?**

**Decided: construct `info@<site domain>` and greet by name.** Only when the
site names a person and no personal address was found; recorded as
`email_kind="constructed"`.

This is the majority case, not an edge: 4 of 10 on-profile companies publish
no reachable address, and M5's live run found six names and no addresses on
one site. The two halves travel together because the research says they must
— `info@` is worth having when it is all there is, and a general inbox with
an unnamed greeting gets forwarded rather than answered (`LEARNINGS.md` §5).
A constructed address that does not exist bounces, which is visible and
harmless; holding four companies in ten is not.

**3. When SHAB and the site name different people, and when does SHAB run at all?**

**Decided: only when the site named nobody.** A network call whose answer
would be discarded is not worth making, and the research settles the
precedence — SHAB describes a past state, the site describes today
(`LEARNINGS.md` §5, `data-sources.md:187`).

When SHAB is used, `source="shab"` and the notice date are recorded, so the
card can say how old the claim is rather than presenting it as current. The
consequence worth naming: the case M5 cannot catch — an injected name on a
page — still wins over SHAB, because SHAB is never consulted when the site
names someone. The reviewer remains the control for that one.

**4. Is the LinkedIn lead in M6?**

**Decided: yes.** The plan recommended deferring it to M7 on the grounds
that nothing displays it until then; Koray kept it, so the chain the design
describes is complete in one milestone rather than two.

What it costs: one more search query per company that reached the end of the
chain without an address, through the same semaphore and one-second gap as
every other query. What it must never become: LinkedIn is not touched, only
a web search restricted to `linkedin.com`, and a name alone gives false
matches — a namesake in another canton (`LEARNINGS.md` §5). It is stored as
`linkedin_lead`, never as a contact, and never as something to write to.

## Tasks

**Task 1 — `tools/shab.py` and its fixtures.**
`persons(uid) -> list[Person]`: `uids=` in dotted form, `publicationStates=PUBLISHED`,
then `/publications/{id}/xml` per hit, then the DE/FR person-block parser.
Every publication, not only the newest.
*Test:* saved XML fixtures for a DE notice with a person block, a FR one, one
with no block and one with no stated function; a test that the dotted form is
what gets sent, since the undotted one silently returns nothing; `ShabError`
on HTTP failure, `[]` on a company with no publications.

**Task 2 — `nodes/find_contact.py`.**
The rules in order, with the noise filters `tools/checks.py` already has.
Writes `contacts` rows.
*Test:* a site address wins over SHAB; SHAB is not called at all when the
site named someone (open point 3); a name with no address becomes a
constructed `info@` marked as such (open point 2); an `email_offsite` person
becomes `email_kind="third_party"`; nothing at all is a finding, not an
error.

**Task 2b — the LinkedIn lead (open point 4).**
A web search restricted to `linkedin.com`, only for a company that reached
the end of the chain with no address at all. Stored as `linkedin_lead` on
the contact row, never as an address.
*Test:* the query really is restricted to the one site; a company that
already has an address never triggers it; the result never becomes
`Contact.email`, whatever it looks like; no request ever goes to
linkedin.com itself.

**Task 3 — `nodes/recommend.py`.**
Pure rules, no I/O: no site / distributor / foreign group → skip; no contact
or only third-party → hold; else send. One reason, always.
*Test:* one case per branch, and that the reason is never empty — the
reviewer reads it.

**Task 4 — `tools/mailto.py`.**
`build(to, subject, body) -> MailtoLink{href, length, fits}`, RFC 6068, fits
below 2,000 encoded characters.
*Test:* a body with umlauts, newlines and an ampersand round-trips; the
length is measured on the encoded string, not the plain one; `fits` is false
just above the limit.

**Task 5 — `prompts/draft.md` and `nodes/draft.py`.**
German, plain text, short. Typed fields only (open point 1). Carries the
revDSG sentence, names no product, makes no pitch.
*Test:* the rendered prompt contains no page text and no profile prose; the
draft is stored with its provenance.

**Task 6 — `nodes/check_draft.py`.**
No URL, no e-mail address, ≤ 1,200 characters, greets the contact by name,
contains the revDSG sentence. One regeneration on failure, then `hold`.
*Test:* one case per rule; a draft that fails twice ends as `hold` rather
than being sent; and the P1 case — a draft generated from a company whose
profile came from `poisoned_instructions.html` carries no injected link.

**Task 7 — wire the tail and the real wrapper.**
`check_profile → find_contact → recommend`, conditional to `draft →
check_draft → END`. `enrich_company` already catches and records; confirm it
still does with a tail this long.
*Test:* a company walks the whole child; a skipped one stops at `recommend`;
one raising in `draft` still produces an error row, not a crashed run.

**Task 8 — `run` end to end, and `retry`.**
The audit's P1 (`audit:207`, `:215`). A company whose only result is an
`error` row is eligible for re-enrichment on a rerun, and `company-reach
retry <run_id>` re-enriches exactly those.
*Test:* the acceptance from `audit:217` — with search failing for 2 of 10
children, those 2 get `error`, are not redrawn, and are re-enriched by
`retry`; `seen` is unchanged throughout.

**Task 9 — the draft checklist eval.**
Deterministic grading against the golden set, opt-in like the others: does a
draft greet by name, stay under the length, carry the revDSG sentence, avoid
URLs. Plus the adversarial case, 3 of 3 trials (`audit:186`).

## Acceptance

- `company-reach run` processes one batch end to end; `drafts` and `results`
  rows exist; nothing was sent.
- The draft checklist passes on the golden set, and the adversarial case 3 of
  3.
- `audit:217`'s retry scenario behaves as written.
- `uv run pytest -q`, `ruff check`, `ruff format --check` green, and CI green
  on the pull request — which from #24 onwards is the real gate rather than a
  claim in a report.

## Deliberately not in M6

- **The review page** — M7 owns everything a human looks at.
- **Actually sending** — never automated, in any milestone.
- **The ledger and suppression list** — the tables exist; M7 writes them,
  because "contacted once, ever" is decided at the moment a human clicks
  send.
