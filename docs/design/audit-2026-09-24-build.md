# company-reach — build audit after M8

**Audit dates:** 2026-09-24 to 2026-09-25
**Scope:** `main` at 34f9981 (all eight milestones merged). The audit also covered the local databases (aggregates only), one isolated live end-to-end run (Gossau SG), the golden evaluations against the configured endpoint, and a dedicated study of the German invitation.
**Risk tier:** medium. The tool contacts real people in their own name, but it never sends mail itself. A human reads every mail, and personal data stays on one machine.
**System classification:** an LLM-augmented deterministic workflow. Code does all routing. The model decides only site choice, page choice, extracted content, scores and the middle of the draft, and each of those sits behind a deterministic check.
**Verdict:** **Not ready for the first real Send.** It becomes a **Conditional Go** once the P0 list in §9 is done, an estimated two to four days of work. It is not yet usable as a general tool for anyone but its author.

Local evidence (gitignored, may contain real names): `data/audit-2026-09-24/`. This file contains no personal data. Examples use fictional companies and people.

## 1. Executive summary

**What works.** The architecture is sound and unusually well explained:
- typed errors are kept apart from findings;
- a parent/child LangGraph uses a wrapper that isolates each company's failure;
- SQLite is the record;
- every model output passes a deterministic check;
- page text is delimited as data;
- sending stays human, behind an ethics gate.

The 513 offline tests pass. `ruff` is clean. `doctor` is green against the endpoint. On one company at a time, the tool does what it promises: it finds the site, picks a contact, and drafts a grounded German mail with a working survey link.

**What does not work yet.** Four problems stand between the tool and its purpose.

1. **It burns candidates when search degrades.**
   - The baseline-engine guard can never fire: two of its three engines (mojeek, startpage) are inactive in the pinned SearXNG image.
   - A run's own queries got DuckDuckGo (CAPTCHA) and Brave (HTTP 429) suspended within one batch of ten.
   - Candidate sites that answered 403 or were unreachable are recorded as "no website".
   - The `searches` table is never written, so none of this can be seen afterwards (#20).
   - In the live run, 6 of 10 top-scored companies were written off as having no website. Those companies are now in `seen` and will never be drawn again.
2. **The invitation says untrue things.**
   - The fixed data-protection sentence is false on three of the five contact paths:
     - it names the website as the source of a guessed `info@`;
     - it names SHAB as the source of an address SHAB never publishes;
     - it says "Ihren Namen" when nobody was named.
   - The mail never says who is writing: 0 of 21 stored drafts name the sender or OST.
   - The model invented an institution once (the university of the company's own city) and left a literal "[Hochschule]" once. Both passed `check_draft`.
3. **It does not recruit at the scale the thesis needs.**
   - A run stops at the first batch that yields one sendable company, which gives 3 to 5 sends per run.
   - At the project's own response assumptions, 60 completed surveys need about 1,570 invitations. That means roughly 26,000 pooled companies (15 to 20 municipalities), 300 to 520 runs and about 80 reviewer hours.
   - Nothing controls company size: of five real "send" cards, one was a 1,240-person group and none was in the 10–49 band.
4. **A few reliability and privacy defects would surface on the first real day.**
   - The LLM timeout is never applied, so a silent endpoint hung the evaluation for 44 minutes.
   - `forget <e-mail>` finds nothing when a person writes from another address.
   - `retry` re-collects the data of a company that was forgotten.
   - `run --goal` drafts without the "about me" text.
   - A failed `run` prints nothing at all.
   - The author's `.env` arms the paid Serper fallback with a comment as its key.

**The German invitation.** The owner's view that it is "too cold" is confirmed. "Too long" is only partly confirmed:
- The coldness comes mostly from the code-built frame: an anonymous sender, a full-name greeting without Frau/Herr, a legal sentence at the end, no thanks and nothing offered back.
- At 116–144 words the length is not extreme. What makes it read long is one 80–110-word block with the request at the end.
- A blind jury of five, three of them recipient personas, ranked a warm, local version first and the shortest version last on every ballot.

§7 gives a replacement that passes the real checks, plus an A/B test of the length question.

**One decision only the owner can take.** The public git history (commits 639f28d, 253c9fa, 9022c3b, d3d4061) holds:
- the names of real golden-set companies next to the owner's ratings;
- the private project and repository names and local paths;
- the university LLM endpoint host.

HEAD is clean. Removing these from history requires a rewrite and a force-push (§9, P0-0).

## 2. Does it do its job?

| Question | Answer | Evidence |
|---|---|---|
| Does one company go from register entry to a reviewable card? | Yes | Live run: site, profile, contact and draft for 3 of 10; `enrich` one company in 73 s |
| Are the drafts grounded and safe from injected links? | Yes for links and addresses; no for invented sender facts and placeholders | check_draft held every URL/e-mail rule; "[Hochschule]" and an invented university passed |
| Is "no website" a trustworthy finding? | No | Guard dead, engines suspended by the run itself, 403 treated as absence, no search log |
| Is the chosen person the right one? | Usually; wrong in edge cases | Departed SHAB officers can be chosen; company names can pass as persons; no size or group check |
| Is the mail something an SME owner answers? | Less often than it could be | Anonymous sender, false privacy line, no thanks/results/deadline; persona estimates open 35–75 %, click 5–6 % |
| Can it reach 60–120 completed surveys? | Not as it runs today | ~1,570 invitations for 60 completes (central case); one municipality yields ~9 |
| Does deletion on request work? | Partly | Works by UID; fails by e-mail from another address, after purge, in backups |
| Is it safe to leave running? | Mostly | Never sends; but a silent endpoint hangs forever, and a crash strands a batch |

**Funnel, central case.** Measured values come from St. Gallen, Gossau and the real run. Response rates come from LEARNINGS §5.

| Step | Rate | Basis |
|---|---|---|
| kept of pooled | 79 % | 4,335 / 5,467 and 751 / 947 |
| score ≥ 7 of kept | 10–20 % | St. Gallen 10.4 % (1,500 scored), Gossau 20 % (100 scored) |
| site found, of drawn | 45–75 % | 5 of 9 (real run), 4 of 10 (live run, degraded search), 15 of 20 (v0 by hand) |
| send, given a site | 75–95 % | 5/5 and 3/4 |
| personal address, of sends | 5–20 % | 1/5 and 0/3; the rest is a shared inbox |
| starts, personal / inbox | 8–15 % / 2–6 % | LEARNINGS §5 |
| completes per invitation | 1.6–7 % | derived; central 3.8 % |

| Target | Invitations | Pooled AG+GmbH | Runs (3–5 sends) | Reviewer hours |
|---|---|---|---|---|
| 60 completes | 855 – 1,569 – 3,727 | 7.6k – 26k – 139k | 171 – 314/523 – 1,242 | 34 – 81 – 250 |
| 120 completes | 1,709 – 3,138 – 7,453 | 15k – 52k – 278k | 342 – 628/1,046 – 2,484 | 67 – 161 – 495 |

One reminder would cut the number of invitations needed by an estimated quarter to a third, but the ledger forbids it today. Both the reminder and the pool's legal-form frame (AG and GmbH only, about 70 % of the St. Gallen register) are research-design decisions to take with the supervisor.

## 3. Scope, evidence and limitations

- **Read:** every source file, prompt, test, design and plan document; the three databases through read-only connections (the live one, the backup made before run m6-e2e-1 was deleted on 2026-09-24, and the isolated audit run).
- **Ran:**
  - the full offline suite (513 passed, 5 skipped);
  - about 150 probe scripts and pytest files written for the audit, under `data/audit-2026-09-24/evidence/`;
  - one isolated live run: pool → screen → criteria → score → run → retry → resume → enrich → review page → forget → purge → doctor;
  - the golden evaluations.
- **Process:**
  - 11 subsystem reviewers, each finding verified by an independent adversarial agent, and a completeness critic with 4 gap finders;
  - a 16-agent study of the German invitation: research, anatomy, 3 personas, 4 rewrites, 5 blind judges, synthesis, and a deterministic check.
  - 189 reviewer findings plus 11 of the orchestrator's own. Of these, 126 were independently confirmed, 1 is uncertain and 0 were refuted. The other 73 are low-severity findings that were not separately verified. Final severities of the verified findings: 13 high, 44 medium, 59 low.
- **Limitations:**
  - The golden evaluations stopped after scoring because the endpoint stalled (see §6), so site choice, extraction, drafts and the poisoned page were not measured.
  - The live run itself contributed to the search-engine suspension it observed.
  - No real mail was sent, and no survey response exists to validate any response-rate estimate.
  - Legal statements are an engineer's reading of revDSG and UWG, not legal advice. The university's data-protection office should confirm them.

## 4. Architecture and agent fit

The degree of agency is right. Everything that can be a rule is a rule: screening, tiers, contact order, recommendation, checks, the gates. The model is used where text has to be read or written, and a check follows every such call. LangGraph was a learning choice, and it is used soundly: a wrapper node instead of a subgraph, `Send` fan-out, a recursion limit, no checkpointer, and SQLite as the record.

Weak points in the orchestration:
- **Crash window.** `draw_batch` records `seen` before any `results` row exists. A crash in between strands the whole batch: no new run draws it, and `retry` sees nothing (`graph.py:134`).
- **Loop target.** The loop's exit condition is "at least one send" (`graph.py:100`), not a target number of candidates.
- **Silent runs.** A run prints nothing for 2 to 25 minutes, and on failure prints nothing at all (`cli.py:182`).
- **Two records of a run.** `runs` rows are never finished, and `run` never writes one. The manifest holds the real record, and it lacks the criteria and any cost figure.
- **One database, two worlds.** The Docker review page and host commands share one WAL SQLite file across Docker Desktop's VM file sharing, which SQLite does not support.

## 5. Scorecard

Evidence coverage 7 of 7 dimensions; average maturity 2.0. Scale: 0 absent, 1 ad hoc, 2 defined, 3 verified, 4 operationalised.

| ID | Dimension | Maturity | Confidence | Highest severity | Key evidence |
|---|---|---:|---|---|---|
| A1 | Architecture and orchestration | 3 | High | high | Wrapper isolation, typed errors, resume, `seen`/`results` crash window |
| A2 | Tools, contracts, action integrity | 2 | High | high | Pydantic contracts and checks; dead search guard, 403 read as absence, SSRF on redirect, Serper armed |
| A3 | Prompts, context, memory | 2 | High | high | Versioned prompts, delimited data; criteria regenerated per `score`, `role` undelimited, invented institution |
| A4 | Reliability and data integrity | 2 | High | high | Idempotent upserts, retry; LLM timeout not applied, stranded batch, charset, WAL across the VM |
| A5 | Security, privacy, governance | 2 | High | high | Human send, gates, loopback, SecretStr; public-history leak, false privacy line, forget gaps, tracing env |
| A6 | Evaluation and observability | 1 | High | high | Golden set exists; one trial at zero margin, most of the pipeline unevaluated, no search log, no progress, no cost |
| A7 | Human, product, operational fit | 2 | High | high | A thoughtful review page; throughput, size, no reminder, no redraft, "sent" is final |

Gates: sending is human, and A5 is at 2 with the send gate enforced outside the prompt. For persistent writes, A2 and A4 are at 2. The verdict is held back by confirmed high-severity findings without a compensating control: the false privacy sentence, the dead search guard, the missing LLM timeout and the deletion gaps. A6 below 2 blocks any claim that quality is measured.

## 6. Verified findings that matter most

All of the findings below were confirmed by an independent verifier. The severity shown is the verifier's.

### High

| # | Finding | Where |
|---|---|---|
| H1 | Privacy sentence false on three contact paths (guessed info@, SHAB name + site inbox, nobody named) | `tools/invitation.py:28` |
| H2 | Baseline-engine guard can never fire: mojeek and startpage are `inactive` in the pinned SearXNG, so total throttling returns `[]` as an answer | `tools/search.py:83`, `searxng/settings.yml:30` |
| H3 | Silent or junk-answering engines are not "unresponsive"; Brave junk defeats the empty-result retry | `tools/search.py:86` |
| H4 | An unreachable or 403 candidate is recorded as "no website"; `get_home` has no caller | `nodes/find_site.py:397` |
| H5 | The LLM timeout is never applied; a silent endpoint blocks `llm.ask` forever (observed: 44 min) | `tools/llm.py:112` |
| H6 | `forget <e-mail>` searches only `contacts`; a reply from another address, or any request after purge, deletes nothing | `forget.py:51` |
| H7 | `retry` re-collects the data of a company that was forgotten and suppressed | `graph.py:372` |
| H8 | A crash between `record_seen` and `record_result` strands the batch permanently | `graph.py:134` |
| H9 | `run --goal` blanks `about_me`; drafts go out without anyone identified as writing | `cli.py:173` |
| H10 | The score cache ignores the criteria, which are regenerated by every `score` command; the pool is ranked against mixed rule sets | `nodes/score_pool.py:131` |
| H11 | `.env.example`'s `SERPER_API_KEY=   # optional…` is read as a key; company and person names go to Serper on any SearXNG error (the author's `.env` has the same shape) | `.env.example:14`, `tools/search.py:142` |
| H12 | No size gate, and no size on the card: of five real "send" cards one was a 1,240-person group and none was in the 10–49 band; `size_signal` and the score reason are stored but never shown | `nodes/recommend.py:27`, `review/cards.py:203` |

### Medium, grouped

- **Invitation text.**
  - The model invents or misstates the institution.
  - "[Hochschule]" passes `check_draft`, and so do English text, phone numbers, a second greeting and ß.
  - The subject line is never checked.
  - A failed draft stays sendable on hold and error cards.
  - A redraft that raises leaves the rejected draft on the card.
  - The mail lacks the controller's identity and does not say that answers are linked to the company.
- **Contacts.**
  - Departed SHAB officers can be chosen (K2).
  - The company name, or any single word on the page, passes as a person.
  - An address on a subdomain or site-builder host makes the real inbox look "third party".
  - Swiss freemail and group addresses are held as hostile plants.
  - `role` enters the drafting prompt undelimited (K4).
- **Reading.**
  - Pages without an HTTP charset lose their umlauts.
  - Impressum footers are dropped when the main text has 80+ words.
  - `pick_pages` has no floor that keeps Impressum/Kontakt.
  - A page redirecting to another domain loses its Impressum.
  - Five angle brackets rebuild the data-block marker.
- **Search and fetch.**
  - The run's own load suspends the engines.
  - `searches` is never written (#20).
  - Redirects bypass the SSRF guard.
  - The per-host delay is not enforced under concurrency.
- **Review page.**
  - After a Send, a redraft, retry or purge points `ledger.draft_id` at text never sent, at nothing, or at another company.
  - Send mails whatever draft is in the database at click time.
  - An address already mailed, or put on the never-again list, is offered again for another company.
  - "Sent" is final even when no mail left.
  - The card shows neither size nor departure, nor where a person was found.
  - A wrong contact can only be skipped, not fixed.
- **Scoring and pool.**
  - `screen_reason` NULL means both "kept" and "never screened".
  - The guard in `run` ignores the prompt version, so a `score.md` bump reads as "pool exhausted".
  - Scoring plus the draw threshold tilt the sample towards AGs.
  - There is no size gate, and `foreign_group` misses Swiss groups.
- **Campaign scale.**
  - Nothing reports pooled, scored, drawable, drawn, sent or undecided per municipality.
  - A second town has no municipality filter; a goal or model change makes `run` read "pool exhausted".
  - `forget` at campaign size would read about 2.8 GB per request.
- **Operations and privacy.**
  - `langsmith_tracing` is never read: tracing follows the shell environment.
  - `forget` neither cleans nor reports backups and logs under `data/`.
  - The Docker app runs as root, is never rebuilt, and reads its environment only at creation.
  - `doctor` checks 3 of 7 needed prompts, no SearXNG, and accepts the placeholder survey URL.
  - CI actions declare the node20 runtime, which a reviewer reported as being retired. CI was still green on 2026-09-24 (`gh run list`), so this is a future risk, not a break.
- **Evaluation.**
  - One scoring trial passes at zero margin.
  - The metric is top-k and bias, not the production decision (score ≥ 7): bias +0.07 hides a mean absolute error of 2.16.
  - Search, contact choice, recommend, the loop and end-to-end have no evaluation.
  - The draft evaluation is close to a tautology.
  - Evaluations write to the production database.

The full list, with evidence, failure scenario and fix for each finding: `data/audit-2026-09-24/consolidated.json`, `findings-run1.md`, `findings-run2.md`.

## 7. The German invitation

**Verdict on "too cold, too long".**
- *Too cold* is confirmed, and mostly caused by the code frame and the inputs, not the model:
  - 0 of 21 drafts name the sender or OST, and 13 say an unnamed "an der Universität";
  - the greeting is "Guten Tag + full name";
  - the mail ends on a 30-word legal notice;
  - 0 of 12 v3 drafts thank the reader or offer the results;
  - 17 of 21 go to a shared inbox with nothing that helps reception forward them.
- *Too long* is partly true:
  - 806–988 characters is at the upper edge, not extreme, and in this range experiments find a small, inconsistent effect of length.
  - What reads long is one 81–108-word block of 22-word sentences, with the request at about 72 % of the mail, below a phone's first screen.

**What moves response, by strength of evidence.**
1. One reminder. It is the strongest single lever: +7.7 points on top of +9.6 in Sauermann & Roach 2013.
2. Personalisation: a name plus a company-specific clause, OR 1.24.
3. The sender's standing: name, programme, university, supervisor.
4. Offering the results: OR 1.36 for e-mail surveys.
5. An honest, early statement of the time needed.
6. A modest plea for help in the body, not in the subject. "Umfrage" in the subject lowered response.
7. Keeping the data-protection text to one plain paragraph. A separate emphatic block cut participation among small firms.

**What the study recommends ("frame@1", jury winner B with grafts from C and D).** Code writes almost everything. The model writes one sentence of at most 20 words, starting "Ich schreibe Ihnen, weil…", saying what this company makes. Example (fictional; a shared inbox with a named person):

```
Subject: Für Frau Dr. Beispiel-Keller: Masterarbeit an der OST

Zuhanden Frau Dr. Beispiel-Keller – besten Dank fürs Weiterleiten

Guten Tag Frau Dr. Beispiel-Keller

Ich heisse Lena Brunner und studiere an der OST in St. Gallen. Für meine
Masterarbeit wäre ich froh um Ihre Hilfe: Hätten Sie 15 Minuten für einen
Fragebogen?

Es geht darum, wie KMU zu Kunden und Lieferanten kommen. Ich schreibe Ihnen,
weil Muster Medtech im Auftrag von Medizintechnik-Firmen Präzisionsteile fertigt.

Als Dank können Sie am Schluss die Ergebnisse anfordern.
Zum Fragebogen (ohne Anmeldung, offen bis Freitag, 30. Oktober):
https://umfrage.example-hochschule.ch/kmu/?c=CHE000000046&l=de
Der Link enthält die UID Ihrer Firma; veröffentlicht werden nur zusammengefasste Ergebnisse.

Ihren Namen und diese Adresse habe ich von Ihrer Website und nutze beides nur
für diese Anfrage. Ich schreibe Ihnen nur dieses eine Mal. Ein kurzes «Nein»
genügt, dann lösche ich Ihren Namen.

Vielen Dank und freundliche Grüsse
Lena Brunner
Masterstudentin, OST Ostschweizer Fachhochschule
Betreut von Prof. Dr. Hans Vorbild
```

- **The request moves to the start.** It now sits at characters 138–218 instead of about 72 % of the mail, and sentences average 9 words.
- **The privacy paragraph is true for each contact kind.** There are seven variants, and none claims a source it did not use.
- **The failure classes are gone by construction.** No institution can be invented, no placeholder can appear, and the gender cannot be wrong: a masculine role on a website never sets "Herr", and the card offers a Frau/Herr/none toggle.
- **The real `check_draft` finds no real defect in the five examples.** It raises exactly the two rules the design changes on purpose, the greeting format and the privacy sentence.

**It is not shorter: 939–1,059 characters.** The jury placed the shortest version last on all five ballots. The owner's length hypothesis should therefore be tested rather than assumed. The plan: split companies by `sha256(uid)` parity into "voll" and "kurz" (785–903 characters, without the results line and the UID line; the survey's first page carries both), then compare start rates through the UID already in the link. No second mail and no tracking are needed. With around 100 mails per arm, only a large difference is detectable, so treat this as a pilot.

**Preconditions the owner must confirm before any of this text goes into code:**
- the form really takes 15 minutes (time three people);
- no login is needed;
- the results can be requested on the last page;
- the supervisor agrees to be named;
- the mail leaves from the OST mailbox;
- a closing date is set;
- the ethics approval covers a survey, this wording, the UID-tagged link, the arm split and, later, one reminder.

Full deliverables (the prompt `draft.md` v4, the frame, five example mails, code changes with the tests to write first, the measurement plan): `data/audit-2026-09-24/invitation/`.

## 8. Modernity (checked 2026-09-25)

The stack is current. No dependency is deprecated or has an open advisory against the locked version. Material points:

| Component | Locked | Status | Recommendation |
|---|---|---|---|
| langgraph | 1.2.11 | current (1.2.12 out) | bump at leisure |
| langchain-openai / -core, openai | 1.6.2 / 1.6.3, 3.16.2 | current (1.6.6 / 1.6.5, 3.19.2 out) | bump together; add a dependency audit to CI |
| langsmith (transitive) | 0.13.0 | environment variables switch tracing on regardless of the tool's setting | wrap calls in `tracing_context(enabled=settings.langsmith_tracing)` |
| fastapi[standard] | 0.141.1 | current, heavy: pulls fastapi-cloud-cli and sentry-sdk | `fastapi[standard-no-fastapi-cloud-cli]` or fastapi + uvicorn |
| httpx | 0.28.1 | valid; 1.0 in development | pin `httpx<1` |
| lxml | 6.1.3 | current; the floor `>=6.1` admits versions before an XML fix | floor `>=6.1.3` |
| protego | 0.6.2 | ≥0.6.2 has the ReDoS fix; 0.7.0 out | bump when convenient |
| SearXNG image | 2026.9.20 | mojeek and startpage `inactive` upstream | enable them explicitly, or change `baseline_engines` |
| GitHub Actions | checkout@v4, setup-uv@v6 | declare node20, reported as being retired; CI still green on 2026-09-24 | move to the current majors when convenient |

## 9. Prioritised roadmap

Effort: S under half a day, M one to two days, L more. Each item is written test-first, as the project does.

### P0 — before the first real Send

| # | Action | Effort | Acceptance |
|---|---|---|---|
| P0-0 | **Owner's decision:** remove the milestone pages, endpoint host and private names from public history (filter-repo + force-push, then ask GitHub to purge PR refs; or recreate the repo from a filtered history). Add a pre-push denylist hook. | M | `git log --all -p` on the public repo shows none of them; the hook blocks a test commit |
| P0-1 | Fix `.env` and `.env.example` (comments on their own lines); a validator turns empty or `#…` secrets into None; `doctor` prints whether Serper is armed | S | `Settings().serper_api_key is None` with the example file; a doctor line |
| P0-2 | Search trust: make the baseline engines real (enable mojeek/startpage or change `baseline_engines`); treat "every engine silent or all results off-topic" as `SearchError`; treat a 403/unreachable candidate as an error, not an absence; write one `searches` row per query (#20); lower search pressure (per-company pacing) | M | A throttled SearXNG (respx) yields error rows, never "no website"; `searches` has rows after a run; the card shows the queries |
| P0-3 | Apply the LLM timeout (pass it to `ChatOpenAI` / wrap in `asyncio.wait_for`); back off between attempts | S | A test with a never-answering endpoint fails within the configured seconds |
| P0-4 | Invitation frame@1 (§7): sender fields in `profile.toml`, code-written subject, greeting, routing line, privacy paragraph per contact kind, closing; the model writes one sentence; `check_draft` checks the subject, placeholders, ß, language and the new frame | M | The five example mails pass; tests for each privacy row; no "Ihren Namen" when nobody is named |
| P0-5 | `redraft <run>` for undecided sends whose link does not match the current `survey_url` (no search, no fetch) | S | After changing `survey_url`, every card is sendable again in one command |
| P0-6 | Deletion that holds: `forget <e-mail>` also matches profiles, ledger and suppression; `retry` and `enrich` skip suppressed UIDs; the review page refuses an address that is suppressed or already mailed; `forget` reports `.db`/`.log` copies | M | Reply-from-another-address, after-purge and sister-company cases pass |
| P0-7 | Small correctness: `run --goal` keeps `about_me`; a failed `run` prints its cause and records it; the card's Send carries the draft id and refuses on mismatch; a failed draft is never sendable | S | Tests for each |
| P0-8 | Crash safety: write `seen` and a pending `results` row in one transaction, or let `retry` treat seen-without-result as errored | S | Killing a run mid-batch leaves every company retryable |

### P1 — before scaling beyond a pilot

- **Throughput.**
  - `run --target N`: draw until N sendable, the pool is exhausted, or a batch cap is reached.
  - Several municipalities in one pool.
  - Progress lines per company.
  - A funnel estimate in `doctor`.
- **Sample validity.**
  - A size signal that is checked: headcount or a range, never a founding year.
  - A "too large" / "part of a group" hold.
  - Size, score reason and SHAB date shown on the card.
  - Reviewer skip reasons for size and group.
  - A decision with the supervisor on AG/GmbH only versus including sole proprietorships.
- **Follow-up.**
  - One reminder, if the ethics approval allows it.
  - Ledger statuses `bounced` and `not_sent`.
  - An import of the survey's per-UID start and complete dates, to measure the funnel and the A/B.
- **Scoring integrity.**
  - Criteria written once per goal and stored.
  - A criteria hash in the score key.
  - The guard in `run` using the same predicate as `draw_batch`.
- **Contact quality.**
  - SHAB rows merged per person with departures applied (K2).
  - A person must not be the company name or a single word.
  - Subdomain and site-builder hosts handled.
  - `role` delimited or verified.
- **Reading quality.**
  - Charset detection.
  - Keep the Impressum footer.
  - A deterministic floor for Impressum/Kontakt in `pick_pages`.
  - SSRF check on every redirect hop.
- **Evaluation.**
  - Repeated trials with the spread reported.
  - The production decision (≥ 7) as a metric.
  - Contact choice and recommend evaluations.
  - Evaluations write to a temporary database.
  - Results appended to a log.
- **Operations.**
  - Run the review page under `uv` alongside the CLI, or both in Docker, never split across the VM boundary.
  - Non-root image.
  - `langsmith_tracing` enforced in code.
  - `doctor` covering SearXNG, every prompt and the placeholder URL.

### P2 — later

CI on current action majors; French and Italian (language detection, frames, screen rules); a `legal_forms` setting; blocklist additions; the modernity bumps in §8; remove the dead prompts, the dead `PLAYWRIGHT_URL` and the unused `runs` columns; UTC month in "sent this month"; clipped letters on small laptop screens; run-id validation.

## 10. Release gates and next reassessment

**Conditional Go** for supervised use by its author when:
- P0-1 to P0-8 are merged with their tests;
- the owner has decided P0-0;
- the preconditions in §7 are confirmed;
- a fresh isolated run of about 30 companies shows no "no website" decided under throttled search, and the search log explains every skip.

Reassess after the first 40 real sends. Compare the start rate by arm and contact kind with the funnel in §2, then decide the reminder and the scale-up (P1).
