# M7 — Review Page Implementation Plan

Issue #7. Branch `feat/7-review-page`, cut from `main` (M6 merged as e4a5465
via #26).

M7 is where a human meets what the tool produced. After it, `company-reach
review` opens one company per screen — the recommendation and its reason,
who to write to and why that address, the evidence the site is the company's
own, and the invitation as a letter — and three buttons: Send, Skip, Never
again. Send writes the ledger first and then opens the reviewer's own mail
client. The tool still never sends anything.

It also owes the audit three P1 items that exist only because a person
decides here: the page must not accept a decision from another site open in
the same browser, the v0 companies must be in the ledger before anyone can
be mailed twice, and a deletion request must have a command that honours it.

## What the design already settles

| Decision | Where |
|---|---|
| One company per screen; left pane decision and facts, right pane the draft as a letter; top bar progress and this month's count; bottom bar actions | `ux/review-page-ux.md` §2, prototype `ux/review-onepager.html` |
| Send / Skip (four reasons) / Never again; Hold is a recommendation, never an action | `ux` §3, §7 |
| Send records the ledger row **first**, then answers with a page that meta-refreshes to the `mailto:` and shows it as a fallback link | `ux` §3, `design.md` §9 |
| Order: send, then hold, then skip; inside a group the run order; `/review/{run}` opens the first undecided | `ux` §4 |
| Works without JavaScript: every slide by URL, every action a form POST; a ten-line script adds keys and the slide | `ux` §9 |
| POST routes reject `Sec-Fetch-Site` other than `same-origin`/`none`; bind `127.0.0.1` outside Docker; `http(s)` links only; `.html` templates so autoescape is on | `design.md` §9, `audit:242` |
| FastAPI + Jinja2 (`fastapi[standard]` bundles uvicorn, jinja2, python-multipart) | `audit:329`, `AGENTS.md` |
| A uid in the ledger or in suppression is never drawn again | `graph.spec.yaml:229` |
| `forget <uid|email>` removes the person everywhere and adds the suppression key | `audit:244`, `:256` |
| v0's contacted companies go into the ledger and `seen` before the first run | `audit:304`, `:313` |
| No monthly cap; the page shows the count | `AGENTS.md`, `ux` §2 |
| Compose `app` service: binds `0.0.0.0` inside the container, published on `127.0.0.1:8000` only | issue #7, `compose.yaml` comment |

## Contradictions between documents

| # | Conflict | Resolution |
|---|---|---|
| 1 | `ux` §3: an Undo of Skip is "a second ledger row [that] reverses it; both stay". `schema.sql`: `ledger (uid TEXT PRIMARY KEY, …)` holds one row per company | `ux`: the ledger becomes a log (open point 4). |
| 2 | `ux` §2 left pane: "every address as a selectable row". M6's `Contact` carries one address | `ux`: the contact records every address considered (open point 3). |
| 3 | `ux` §5: on a hold "actions identical — the user may overrule". M6 drafts only a `send`; a hold has no draft to send | M6: no draft, no Send; the card says why (open point 2). `ux` §5 is corrected in Task 4. |
| 4 | Issue #7 names the route `/review/{run}/{n}`; `ux` §9 renders every slide in one page, the current one chosen by URL | Both: `/review/{run}/{n}` is that URL. Mechanical. |

## What M6 left on M7's doorstep

**The survey link is a placeholder.** `profile.toml` holds
`https://survey.example` until the survey is deployed, and every draft
written until then carries it. Nothing should let such a draft be sent.
Open point 1.

**Ethics approval is still open** (tracked outside this repository, and the method changed
from interviews to a survey on 2026-09-20). The audit calls a send gate
"optional but cheap" (`audit:313`); this is the milestone where Send exists.
Open point 1.

**Personal data lives in more places than the audit listed.** Besides
`contacts`, `drafts`, `profiles`, `pages` and the page cache, M6's
`results.reason` names the person ("Anna Muster at the general inbox…"), and
`contacts.alternatives` names everyone else. `forget` has to reach all of
them. Model responses are not written to disk, so `data/runs/*/llm` does not
exist to purge.

**#25** (same name under another TLD is held) is the one kind of hold the
reviewer will most want to overrule; it depends on open point 2.

## Open points

Four, answered before code (`IMPLEMENTATION.md:13-16`), one at a time. All
four were settled with Koray on 2026-09-24 as recommended.

**1. What stops a Send before the tool is allowed to send?**

**Decided (2026-09-24, with Koray): both conditions lock Send**, as
recommended below.

Two conditions are real today: the survey link is a placeholder, and ethics
approval is pending. *Recommendation:* the page refuses Send — button
disabled, one line saying why — while either holds. The placeholder is
detected, not configured: a draft whose link does not start with the
profile's current `survey_url`, or whose host is under `.example`, cannot be
sent. Ethics is a setting, `SENDING_APPROVED=false` by default in `.env`,
because only a human can know it was granted. Skip and Never again stay
available, so a batch can be reviewed while the gate is closed.

**2. Can a held company be sent?**

**Decided (2026-09-24, with Koray): no draft, no Send**, as recommended.

M6 drafts only a `send`. *Recommendation:* no draft, no Send. A hold states
why a human must look first, and the reviewer's overrule path is to fix the
cause (for example #25) and re-run the company, not to send a mail the checks
never saw. The card says so in place of the letter.

**3. Which addresses does the card offer?**

**Decided (2026-09-24, with Koray): every address considered**, as
recommended.

M6 keeps one address on the contact. *Recommendation:* `find_contact` also
records every address it considered — the chosen one, the published inbox,
the person's own, an off-domain one — each with its kind, and the card
offers them as rows with the chosen one pre-selected. The greeting stays the
drafted person's: writing to the general inbox with the person's name is the
pattern the research recommends. A lead is shown, never selectable.

**4. The ledger: one row per company, or a log?**

**Decided (2026-09-24, with Koray): a log**, as recommended.

*Recommendation:* a log. One row per decision (`id`, `uid`, `status`,
`address`, `draft_id`, `run_id`, `note`, `decided_at`); a company's state is
its latest row, and "contacted" means any `sent` row ever. Undo of Skip is a
new row, so the history the thesis reports stays whole. The table is empty
in every database so far, so it can be recreated rather than migrated.

## Tasks

**Task 1 — the ledger and suppression, as data.**
`ledger` as a log (open point 4); `record_decision`, `decision_for`,
`sent_this_month`; `draw_batch` excludes any uid with a ledger row or a
suppression key.
*Test:* a skip then an undo leaves two rows and an undecided company; a
`sent` uid is never drawn; a suppressed uid is never drawn; the month count
counts `sent` rows in the calendar month only.

**Task 2 — `import-v0`.**
Reads `data/v0/outreach.md` (decisions) and `data/v0/seen.json` (drawn), and
writes `ledger` rows with `note='v0'` and `seen` rows. Idempotent.
v0 sent no mail (Koray, 2026-09-24): its log holds skip, hold and "pending",
a recommended send nobody acted on. The audit's "22 contacted companies"
means 22 reviewed. So all 22 go into `seen`, the skips become `skipped`, and
a hold or pending one gets no ledger row.
*Test:* against a fictional fixture in the same shape; run twice, same rows.
*Real:* run on `data/v0/`, then confirm every v0 contacted uid is in the
ledger and none of them can be drawn.

**Task 3 — the addresses on the contact (open point 3).**
`Contact.addresses: list[{email, kind}]`, filled by `find_contact`; stored
with the row.
*Test:* the chosen address is first and marked; an off-domain one is
`third_party`; the lead never appears as an address.

**Task 4 — the page, read-only.**
`review/app.py` (FastAPI), `templates/review.html` from the prototype,
`static/review.css`; GET `/review/{run}` and `/review/{run}/{n}`; order send,
hold, skip; every company's facts, evidence, addresses and letter; the send
gate's reason (open point 1); `http(s)`-only links.
*Test:* `TestClient` renders a fictional run; the order; a hold shows no Send
(open point 2); a `javascript:` site URL is not rendered as a link; an
escaped company name.

**Task 5 — the decisions.**
POST `/decide/{run}/{uid}` for send, skip (+reason), never, undo;
`Sec-Fetch-Site` check; Send writes the ledger then answers with the
meta-refresh page; the send gate re-checked on the server.
*Test:* cross-site and missing header → 403 and nothing written; send with
the gate closed → refused and nothing written; send records then returns the
`mailto:`; never writes suppression; undo of a skip.

**Task 6 — the script and the look.**
Arrow keys, `S`, the toast, the slide, `prefers-reduced-motion`; tokens from
`ux` §8. Nothing depends on it.
*Real:* open the page on the real `m6-e2e-1` run and click through it in a
browser.

**Task 7 — `forget <uid|email>`.**
Removes the person from `contacts` (row and alternatives), `drafts`,
`profiles`, `results.reason`, `pages` text and the page cache for that site;
adds the suppression key.
*Test:* after `forget`, a grep of the database and `data/` for the name finds
nothing (`audit:256`), and the key is suppressed.

**Task 8 — `company-reach review` and the Compose service.**
The CLI command binds `127.0.0.1`; a Dockerfile and an `app` service bind
`0.0.0.0` inside and publish `127.0.0.1:8000`.
*Real:* `docker compose up app`, review a batch, Send opens the mail client
and writes the ledger row.

## Acceptance

- `docker compose up` → review a batch; Send opens the mail client and the
  ledger row exists before it does.
- A POST without `Sec-Fetch-Site` or with `cross-site` gets 403 and writes
  nothing (`audit:252`).
- Every v0 contacted uid is in the ledger and cannot be drawn (`audit:313`).
- After `forget`, nothing under `data/` names the person and the key is
  suppressed (`audit:256`).
- `uv run pytest -q`, `ruff check`, `ruff format --check`, CI green.

## Deliberately not in M7

- **Sending** — never automated, in any milestone.
- **Editing the draft on the page** — the mail client is the editor (`ux` §7).
- **A retention purge** (`audit:352`, P2) — `forget` is the deletion path;
  a time-based purge is a follow-up issue.
- **Drafting a held company from the page** — open point 2.
