# company-reach

Finds companies worth talking to in the Swiss commercial register, finds the
right person in each, and drafts a short personal invitation for a human to
send. First use: recruiting SME survey respondents for a master's thesis —
a 15-minute online form, decided 2026-09-20 (it was interviews before). A general tool: the user's own goal and "about me" are input, nothing
in the code is specific to one user.

## Status
**Designed and audited, being built in public.** The design is
`docs/design/2026-09-19-company-reach-design.md` (spec: `graph.spec.yaml`,
checks: `validation-report.md`, audit: `audit-2026-09-19.md`, review page:
`ux/`); research in `docs/research/`. Build order and the learning loop:
`IMPLEMENTATION.md` (milestones M1–M8). The author's hand sketch is in
`docs/sketches/`. What an earlier prototype taught us: `LEARNINGS.md` —
lessons only, its code is not ported.

Decided (2026-09-19), details in the design doc:
- Source is LINDAS only. Candidate selection is fully automatic; review
  starts once ten candidates are ready, one company per screen.
- LangGraph StateGraph: parent graph; a wrapper node runs the child graph per
  company (Send fan-out, try/except). Review page outside the graph. No
  checkpointer; SQLite is the record; errors are retried, findings kept.
- LLM through an OpenAI-compatible endpoint (base_url + key + model);
  structured-output method is a setting; `doctor` probes before a run.
- Page text is data: delimited in prompts, deterministic checks afterwards.
- Contacts: site → SHAB name → generic address with name → LinkedIn lead
  (found by web search, marked unverified, never scraped).
- Sending stays human (`mailto:`); no mail cap — the page shows the count.

## How we build
- Step by step: the code is written with an AI assistant and explained
  milestone by milestone (which library, why, how it is used); a human
  reviews every change before it merges. Prefer the plain, readable solution
  over the clever one.
- Use the established library for a job rather than hand-rolling its
  equivalent: langchain-openai for model calls, LangGraph for the graph,
  FastAPI for the review page, trafilatura for page text. Reaching for httpx
  where a maintained client exists costs transferable knowledge and buys
  nothing. Keep *our own* code plain — the simplicity rule applies to what we
  write, not to whether we depend on the standard tool.
- Prompts live in `src/company_reach/prompts/`, versioned, easy to read and
  edit; inside the package so an installed copy finds them.
- All files in English, with one exception: `docs/milestones/` is written in
  Turkish and kept out of git. Those pages explain a finished milestone to the
  repository's owner, have no other reader, and quote the hand-labelled golden
  set; library names and settled technical terms stay in English inside them.

## Hard rules
- **This repository is public.** Company and person data only in `data/`
  (gitignored). Named people are personal data under the Swiss revDSG —
  never in git, never in examples; fixtures use fictional companies.
- Secrets only in `.env` (gitignored).
- Never send mail automatically, never scrape LinkedIn.
- main via short-lived branch → PR → review → merge.
- Per-developer notes go in `CLAUDE.local.md` (gitignored).

## Process
- Tracking: GitHub Issues and milestones M1–M8.
- Commits: conventional, English, <=60-char subject.

## Map
`IMPLEMENTATION.md` milestones and the explain loop · `docs/design/` design,
spec, validation, audit, `ux/` · `docs/research/` · `docs/plans/` task plans
per milestone · `LEARNINGS.md` prototype lessons · `docs/sketches/` ·
`data/` and `docs/milestones/` local only.
