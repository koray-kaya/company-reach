# #14 — Golden Site Fixtures Implementation Plan

Issue #14. Branch `eval/14-golden-site-fixtures`, cut from `main` after M4
(PR #13) merged.

M4 tests how `find_site` decides, offline and with fictional companies. It
does not measure how often it decides *right*. This plan captures real
candidates for the twenty v0 companies once, saves them under
`data/golden/sites/`, and grades site choice against them without touching
the network again.

## What we have

| Source | Holds |
|---|---|
| `data/v0/batch3_sites.json`, `batch4_sites.json` | 20 UIDs → the site v0 found by hand, or `null`. 13 sites, 7 none. |
| `data/company_reach.db` → `companies` | All 20 records, with street, postal code and city — what `verify` and the prompt need. |
| `data/v0/<uid>/site.json` | Page text for the chosen site only; no rejected candidates. Not reused. |

## Decisions

1. **Twenty cases, not fifteen.** The design's "≥ 13/15" was written for the
   15 known sites. The v0 data has 13 sites and 7 companies without one, and
   the seven are the valuable half: the easy way for a site finder to fail
   is to invent a site for every company. The result is reported as two
   numbers: *sites right X/13* and *no-site right Y/7*. The design's bar
   (13/15, about 87 %) is quoted next to it for comparison, not asserted.

2. **v0's answers are a starting point, not the truth.** They were found by
   hand and never re-checked. Two look doubtful already: a `.de` domain and
   a `.com` whose contact addresses belong to another firm — either could be
   a group site rather than the company's own. And a v0 `null` means v0 did
   not find one, not that none exists. Task 3 lists every place the capture
   disagrees with v0, and Koray decides each one once. The decision goes into
   `expected.jsonl` with a one-line `note`.

3. **Two numbers, because there are two jobs.** Search chooses three
   candidates; the model picks from them. If the right site is not among the
   three, the model cannot be blamed for missing it. So the report has:
   - *candidate recall* — for the 13 site cases, was the expected site one
     of the three candidates? This measures search.
   - *site choice* — given the saved candidates, did the decision match?
     When the expected site is not among them, "none" is the right answer.
     This measures the prompt.

4. **The evaluation grades the production decision, not the raw model.** It
   calls the same `_ask_model` and `_decide` that `find_site` calls, so the
   quote check and the tier label are part of what is measured.

5. **One network pass captures what #15 and #16 need too.** Alongside the
   three candidates the capture saves the raw search results (every URL
   before dedupe and the cap — #15 needs the directories) and the unpruned
   page list of the expected site (#16 needs the sitemaps). Capturing twice
   would mean two runs whose search results differ.

6. **Captured once, then frozen.** The capture script refuses to overwrite
   an existing `data/golden/sites/` unless given `--force`. Search results
   change from day to day; a golden set that moves with them measures
   nothing.

## Layout

```
data/golden/sites/
  expected.jsonl          uid, name, expected (domain | "none"), note
  <uid>/search.json       queries and raw results, as search() returned them
  <uid>/candidates.json   the three candidates: url + the text find_site sent
  <uid>/pages.json        expected site's page list before pruning (sites only)
```

All of it is real company data, so all of it stays under the gitignored
`data/`.

## Tasks

### Task 1 — Extract `read_candidates` from `find_site`

The loop that fetches each candidate's home and `/impressum` and joins their
text moves into its own function, `read_candidates(candidates, fetcher)`.
`find_site` calls it; the capture calls it too, so the saved text is exactly
what production sends. Pure refactor: existing tests pass unchanged.

The same for `list_pages`: split off the part before `prune_page_urls`, so
the capture can save the unpruned list.

### Task 2 — Capture script

`scripts/capture_golden_sites.py`, run with `uv run`. For each of the 20
UIDs: load the record from the database, run the search step, read the
candidates, and for site cases list the expected site's pages. Writes the
layout above, plus `expected.jsonl` seeded from v0.

A script rather than a CLI command: it runs once, and the tool's users have
no golden set. It uses the real `Settings`, the real fetcher (so the cache,
robots.txt and delays apply) and search with its Serper fallback.

### Task 3 — Compare with v0, Koray labels the disagreements

The script ends by printing one line per company: v0's answer, whether it
is among the candidates, and the candidates. Every line where the two
disagree — expected site missing from the candidates, a v0 `null` where a
candidate looks like the company's own, a doubtful v0 domain — goes to
Koray. His decision is written into `expected.jsonl`. **Checkpoint:** no
evaluation runs before this is done.

### Task 4 — The evaluation

`test_site_choice_matches_the_golden_set` in `tests/test_prompts.py`, behind
`RUN_LLM_EVALS=1` like the scoring test. It runs the twenty cases twice
(n=2) and prints candidate recall, *sites right* and *no-site right* per
run, the spread, and each miss by UID.

Asserted always: every chosen site's quote is found verbatim in its page.
That is deterministic and the production code relies on it.

Asserted after the first measurement: the two site-choice numbers do not
fall below a `SITE_BASELINE`, written in the same way as the scoring
test's `BASELINE`. Whatever the first run shows is the baseline — if it is
below the design's bar, that is a finding about the prompt and goes into
the PR, not into a lower bar.

### Task 5 — Pull request

The PR states the numbers, the spread, how many v0 answers Koray corrected,
and that the fixtures for #15 and #16 are in place. It closes #14; the
audit item for site choice is closed with it.

## Not in scope

- Changing `pick_site.md`. If the number is low, that is the next issue.
- `data/evals/results.jsonl`. The scoring test does not write it either; it
  lands for both at once, or not at all.
- A redacted, fictional subset for the public repository — M8.

## What changed on the way (2026-09-21)

The first capture could not become a golden set, and fixing why took most of
the day. Recorded here because the branch does more than the tasks above.

- **Search was feeding noise.** Through SearXNG, Bing answered every query
  with unrelated pages, and because engines are interleaved it filled the
  three candidate slots. Only 8 of 13 v0 sites were among the candidates.
  Bing is off; domain guesses now come first; a deep result becomes its
  site's root; the cap is ten, as in the earlier prototype, not three. On
  the saved results that raised the count to 12 of 13 before any recapture.
- **Pages are read the prototype's way.** Impressum from the home page's
  own link, then the usual Impressum paths, then Kontakt or Datenschutz;
  an About page and the schema.org block too; each part with its own length
  in the prompt, so a long home page can no longer push the Impressum out.
  Candidates are read at the same time. (`tools/candidate_pages.py`)
- **textify returned JavaScript** for Wix pages and CSS for some
  directories: its fallback kept `<script>` and `<style>`. Fixed.
- **Quotes stitched with "..." were rejected** although every piece was on
  the page. Three of the first run's four misses were this. Each piece is
  now checked on its own.
- **v0 was wrong four times.** Two sites it found belong to other firms
  with a similar name in another town — exactly the trap the prompt warns
  about — and two companies it called site-less have one. The
  labels are Koray's, with a note per company, in `expected.jsonl`.

Result: 14 companies with a site and 6 without; the right site is among the
candidates for all 14. Site choice 20/20 and 19/20 (design bar 13/15). The
one miss moves between runs: a site with no address and no UID on it.

Found and not fixed here: when throttled engines return an empty list
instead of an error, the empty result reads as "no website". Four companies
came back empty during one capture. That is the finding-versus-error rule
leaking, and it belongs in its own issue.
