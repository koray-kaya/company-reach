# Review page — UX spec

Date: 2026-09-19, revision 2. Approved by Koray. Revised 2026-09-24 for
what M7 settled (hold without Send, one page per company, the ledger as a
log). Prototype:
`review-onepager.html` (three fictional companies: send, hold, skip). Companion
to section 9 of the design doc.

## 1. User and job

One person at their own machine, mail client open beside the browser, a batch
of about ten enriched companies. Per company one decision: **send this draft
to this address, skip it, or never contact this company.** The page states a
recommendation with its reason; the user confirms or overrules, then moves on.

Koray's brief, verbatim in spirit: one page = one company, full screen, no
scrolling, slide through with the arrow keys, facts left and draft right,
never drown the reader.

## 2. Layout — one screen, three bands

| Band | Content | Notes |
|---|---|---|
| **Top bar** (one line) | ← Home · previous / `3 / 10` / next · progress marks (one per company: blue = current, green = sent, grey = skipped) · `3 / 10` · `Sent this month: 14` | Read once. The count is a plain number: no cap exists by design. |
| **Stage** (fills the rest) | left pane 5/11: decision and facts · right pane 6/11: the draft as a letter | The stage is a horizontal track; each company is one slide. Arrows at the left and right edges. |
| **Bottom bar** (one line) | `Skip:` four one-tap reasons · `Never again` · spacer · `Send` (primary, right) | Always in the same place, so the hand learns it. |

The page does not scroll on a desktop (≥ 900 px): each pane that holds more
than the screen scrolls on its own and shows a soft shadow on the edge with
more. Below that width the panes stack and the page scrolls, with the action
bar kept at the bottom — a phone cannot hold both panes.

Changed 2026-09-25 (issue #49): the goal left the top bar — it is the same on
every card, and as one unbreakable line it made the page wider than a 1470 px
window, pushing the draft and Send off screen. The page's one grid column is
`minmax(0,1fr)` so no content can widen it again; previous and next moved
from the screen edges into the top bar; the draft's paragraphs are spaced by
a margin instead of a blank line, so a whole invitation fits a 738 px high
window.

### Left pane, top to bottom

1. **Verdict chip** (`SEND` green · `HOLD` amber · `SKIP` grey) and the one
   reason sentence beside it. The decision comes first; the name is context.
2. **Company**: name (largest type on the page), what it does in one line,
   `site · legal form · seat · UID`. "No site" replaces the link when none was
   found.
3. **Write to**: person and role with the source of the name (`named on the
   Impressum` / `SHAB, 03.2023 — may be out of date`); every address as a
   selectable row with its kind tag — `seen`, `constructed`, `generic`,
   `third_party`, `unverified lead` — and one hint line under the rows.
   The recommended address is pre-selected; the choice travels with Send.
4. **Evidence** (pinned to the bottom of the pane): the verbatim quote or
   address match that verified the site, with its page and tier (`UID
   matches the register` / `address matches, no UID on the site` / `model
   choice`). For a company without a site: the list of searches tried.

### Right pane

The draft as a letter: `To`, `Subject`, body, and a footer with `Opens in
Outlook · 1,640 of 2,000 link characters` and the prompt version. A company
without a draft shows a short empty state ("No draft — there is no address to
write to").

## 3. Actions

All three are buttons of one `<form method="post" action="/decide/{run}/{uid}">`;
`name="action"` says what happened, the radio `to` says to which address.

| Action | Control | Server | Feedback |
|---|---|---|---|
| **Send** | primary button, key `S` | writes the ledger row (`sent`, address, time) **first**, then answers with the recorded state whose `<head>` meta-refreshes to the `mailto:` and shows the same link as a fallback | toast "Recorded as sent · Outlook opens the draft"; after 0.5 s the stage slides to the next undecided company |
| **Skip** | four small buttons: Not a fit · No address · Foreign group · Distributor | ledger row `skipped` + reason | toast with the reason; slide on. Undo: the decided card shows the recorded state with an Undo button (an `undone` row after it in the ledger, which is a log; both stay) |
| **Never again** | quiet button → browser `confirm()` with the consequence | suppression row (permanent) + ledger `never` | toast; slide on. No undo, said before the click |

Send is disabled on a company without an address and on an already decided
company. Why record before opening the mail: "contacted once, ever" depends on
the ledger, so it must not depend on a link being followed; `<a ping>` was
rejected (Firefox ships with it off).

## 4. Moving between companies

- Order: recommendation `send` first, then `hold`, then `skip`; inside a
  group the run order. Best cases while attention is fresh.
- `→` / `←` or the edge arrows move one slide; `/review/{run}` opens the
  first undecided company. Decided slides stay reachable and show their
  recorded banner instead of the action bar.
- After a decision the track slides on by itself (350 ms; instant under
  `prefers-reduced-motion`).
- Keys are ignored while a form field has focus. Skip and Never again have no
  single-key shortcut on purpose.

## 5. States

| State | What changes |
|---|---|
| send / hold / skip recommendation | chip colour and word; reason sentence. Skip and Never again are always offered; Send only where a checked draft exists — a hold or a skip has none, so the card says why in place of the letter (M7 open point 2) |
| No site found | chip `SKIP`, reason names the searches tried; company line shows the register head clause; Write to shows the SHAB lead with its date and the LinkedIn lead marked `unverified lead` ("check it yourself; nothing is sent from here"); Send disabled; right pane empty state |
| Site found, no address | as above with the site link and evidence; SHAB name if any |
| Constructed address | `constructed` tag; hint names the pattern and page; the generic address as the second row |
| Third-party address | `third_party` tag; chip `HOLD`; hint says the domain differs from the verified site |
| mailto too long | footer of the letter: "2,340 link characters, above the 2,000 Outlook opens reliably — Send records and opens a mail with address and subject; copy the text" |
| Already decided | banner (sent / skipped with Undo / never) in place of the bottom bar |
| End of queue | last slide: "All 10 reviewed" with sent / skipped / never / sent this month, the command to draw another batch |

## 6. Accessibility and keyboard

Semantic controls (radio rows are `<label>`s, actions are `<button>`s), Tab
order equals reading order, visible focus ring, `aria-live="polite"` on the
stage so the slide change is announced, toast has `role="status"`. Colour never
carries meaning alone: the verdict is a word, the address kind is a word.
Contrast ≥ 7:1 for body text in both themes. Motion only on the track and the
button hover, both under `prefers-reduced-motion`.

## 7. Deliberately left out

A list view of the batch · scores and confidence bars · editing the draft in
the page (Outlook is the editor) · a monthly cap or progress toward one ·
Hold as a user action (it is a recommendation; outcomes are send, skip,
never) · undo for Send and Never again · product name, logo, icons,
gradients.

## 8. Tokens and type

Borrowed from the author's existing slide theme so the tool shares one visual
language with their other work:
Lato 400/700/900; `ink #0b1220`, `text #1e293b`, `muted #64748b`, `faint
#94a3b8`, `line #e2e8f0`, `navy #1e40af`, `blue #2563eb`, `tint #eef2ff`,
`card #f8fafc`, `good #d1fae5 / #047857`; one addition for Hold: `warn
#fef3c7 / #92400e`. Dark counterparts are defined per token in the prototype
(`:root`, `prefers-color-scheme`, `[data-theme]` pattern). Type scale: name
30 px/900, verdict chip 13 px caps, body 15 px, letter body 14.5 px/1.6,
labels 11.5 px caps with 0.16 em tracking.

## 9. Implementation notes for the template

- One Jinja2 template `review.html` renders one company per URL,
  `/review/{run}/{n}` (changed in M7 from a sliding track of every company
  in one page: one page per company keeps the action form tied to the
  company on screen and the page working without script). The edge arrows
  are links; a short fade stands in for the slide.
- The small script handles arrow keys, `S`, the Never-again confirm and the
  toast. Everything works without it (each card is reachable by URL, every
  action is a form POST, and Never again asks on a page of its own).
- POST routes require `Sec-Fetch-Site: same-origin` or `none`; the server
  builds the `mailto:` with `urllib.parse.quote(text, safe="")` after
  normalising line breaks to `\r\n`.
