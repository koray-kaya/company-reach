# M2 — LLM Layer and Scoring Implementation Plan

**Goal:** `company-reach doctor` verifies the endpoint before any run, and
`company-reach score --goal "…"` turns the screened pool into rows in
`scores`, incrementally and resumably.

**Architecture:** `tools/llm.py` is the only module that talks to a model:
it loads a versioned prompt file, renders it with `string.Template`, calls the
OpenAI-compatible endpoint through `langchain-openai` with structured output,
and returns a validated Pydantic object plus provenance. `nodes/write_criteria.py`
turns the goal into a `SelectionCriteria`; `nodes/score_pool.py` scores
companies in batches of 50 behind a semaphore, skipping anything already scored
for `(goal_hash, prompt_version, model)`. `tools/doctor.py` probes the endpoint
and the database before a run. No LangGraph yet — that is M3.

**Tech stack added:** `langchain-openai`, `tomllib` (stdlib) for `profile.toml`.

**Spec:** design §§ 3.1, 5 (`write_criteria`, `score_pool`), 8 (`scores`,
`runs`), 10 (settings); audit A2 (structured-output setting + `doctor`), A3
(criteria as data), A4 (score cache).

---

## Measurements this plan is built on

Taken 2026-09-20 against the school hub (`api.llmhub.infs.ai`, vLLM,
`GLM-5.3-Flash`), municipality 3203:

| Question | Answer |
|---|---|
| `response_format: json_schema` (strict) | works |
| `json_object`, function calling | both work |
| 50 companies, one call, thinking on | 121 s, 4,741 completion tokens, 50/50 returned, 0 missing or invented UIDs |
| Same, thinking off (`reasoning_effort: low`) | 37.6 s, 1,465 tokens |
| Thinking off vs on, same 50 companies | 38/50 identical; the 12 that differ are **all** scored higher with thinking off — it is less able to separate the bottom of the pool |
| 10 concurrent requests | 6/10 succeeded, 4 × HTTP 500 after 600 s; per-request latency 330–590 s |
| 3 concurrent requests | first wave 213 / 233 / 306 s clean; **second wave returned truncated JSON** — `max_tokens=8000` was not enough under load |
| Throughput, any concurrency | ~0.5 companies/s — the server is compute-bound, concurrency buys ~20% |

**Truncation is the real hazard, not concurrency.** A strict `json_schema`
constrains which tokens may be generated; it does not stop generation being
cut off at the cap, and a cut-off answer is invalid JSON. The measured usage
for 50 companies at `max` effort was 4,741–4,951 tokens, but a loaded server
exceeded 8,000. `llm_max_tokens` is therefore **32,000** — six to seven times
the measured need, and far below the model's 128K output limit. The cap stays
finite so that a runaway generation fails in minutes rather than occupying a
shared university GPU for half an hour, and the `finish_reason` check stays
regardless, because raising a cap lowers the chance of truncation without
removing it.

**Decisions that follow.** Thinking stays **on** for scoring (a one-time cost,
sharper discrimination). `LLM_CONCURRENCY=3`. `max_tokens` must be generous —
a 20-token limit returned empty content because reasoning consumed it, and
that failure mode is silent, so `doctor` and `llm.ask` both check
`finish_reason`.

## Decisions taken with Koray on 2026-09-20

1. **The goal is sector-shaped, not size-shaped.** Headcount is not in the
   register; asking the model to infer it invites confident nonsense. The goal
   names manufacturing and technical services; size is established later from
   the website (M5) and applied as a rule in `recommend` (M6).
2. **Target sectors: manufacturing + technical services** (~1,200 of the 4,335
   screened companies). Measured pool composition: trade 45.3%, consulting
   31.8%, technical services 16.2%, manufacturing 16.0%, software 9.9%;
   42.9% of companies fall in more than one bucket, which is why the model
   reads the purpose rather than a keyword list deciding.
3. **Scoring is incremental, not exhaustive.** `score --limit N` scores N
   unscored companies and stops. Work can start after the first few hundred;
   more can be scored later. The score cache makes this free of waste.
4. **Scoring order is a seeded shuffle.** Without it the first N scored are
   the N lowest UIDs, and Swiss UIDs run in registration order — the sample
   would be the oldest firms in the canton. One shuffle at scoring time makes
   the scored subset representative.
5. **`draw_batch` (M3) does no tie-breaking.** Plain `order by score desc`.
   Because scoring order was shuffled, equal scores already sit in random
   order, so the simple query is unbiased. This is why (4) lives in
   `score_pool` and not in `draw_batch`.

## Global constraints

- Prompt files in `prompts/`, each with a version header; the version is
  stored with every score so a prompt change invalidates the cache.
- Page/company text is **data**: rendered inside delimiters, never
  concatenated into instructions.
- Every LLM call records provenance: model, prompt name + version, token
  counts, `finish_reason`, duration.
- `profile.toml` is gitignored; `profile.toml.example` is committed.
- Files English (except `docs/milestones/`). Conventional commits, ≤ 60 chars.
- Tests offline by default; model tests are opt-in (`RUN_LLM_EVALS=1`).

---

## File structure

| File | Responsibility |
|---|---|
| `prompts/criteria.md` | goal → `SelectionCriteria`; version header |
| `prompts/score.md` | criteria + 50 companies → list of scores |
| `src/company_reach/models.py` (extend) | `SelectionCriteria`, `Score`, `ScoreBatch` |
| `src/company_reach/errors.py` | `LlmError`, `DoctorError` |
| `src/company_reach/tools/llm.py` | `ask()`, prompt loading, provenance, semaphore |
| `src/company_reach/tools/doctor.py` | endpoint, schema, token-budget and DB checks |
| `src/company_reach/profile.py` | read `profile.toml`, `goal_hash()` |
| `src/company_reach/nodes/write_criteria.py` | goal → criteria, persisted on the run |
| `src/company_reach/nodes/score_pool.py` | shuffle · batch · cache · UID-set check |
| `src/company_reach/cli.py` (extend) | `doctor`, `criteria`, `score` |
| `tests/fixtures/llm_*.json` | recorded endpoint responses |
| `tests/fixtures/golden/scoring.jsonl` | hand-labelled companies |
| `tests/test_llm.py`, `test_doctor.py`, `test_score_pool.py`, `test_prompts.py` | |

---

### Task 1: Models, errors, and the prompt loader

**Files:** extend `models.py`; create `errors.py`, `prompts/criteria.md`,
`prompts/score.md`, `tools/llm.py` (loader half only).
**Test:** `tests/test_prompt_loading.py`

**Interfaces**

```python
class SelectionCriteria(BaseModel):
    must: list[str]              # a company must satisfy all of these
    must_not: list[str]
    positive_signals: list[str]

class Score(BaseModel):
    uid: str
    score: int                   # 0-10, validated range
    reason: str                  # <= 25 words, enforced by a post-check

class ScoreBatch(BaseModel):
    scores: list[Score]

def load_prompt(name: str) -> tuple[str, str]:   # (version, template text)
def render(name: str, **variables) -> tuple[str, str]
```

Prompt file shape — the header is parsed, not decorative:

```
---
version: 1
---
<template text with $placeholders>
```

- [ ] **Step 1** — failing tests: header parsed; missing variable raises;
      unknown prompt name raises; `$` inside delimited data is not substituted
      (use `string.Template.safe_substitute` for the data block only).
- [ ] **Step 2** — run, see them fail.
- [ ] **Step 3** — write `errors.py`, the three models, `load_prompt`/`render`,
      and the two prompt files.
- [ ] **Step 4** — tests pass. `uv run ruff check . && uv run ruff format .`
- [ ] **Step 5** — commit `feat: prompt files and output models`.

**`prompts/score.md` sketch** (the wording is reviewed with Koray before Task 4):

```
---
version: 1
---
You are helping select companies to approach for a research interview.

## The goal
$goal

## Selection criteria
$criteria

## How to score
Give each company 0-10 for how well its register purpose fits the goal.
The purpose text says what the company set out to do, not what it does today,
and it never states headcount. Do not guess headcount. Score only what the
text supports; when the text is too vague to tell, score 3-5 rather than
inventing a reason.
Reason: at most 25 words, English.
Return exactly one entry per company, reusing the uid exactly as given.

## Companies
<<<COMPANIES
$companies
COMPANIES
```

---

### Task 2: `llm.ask` — one call, validated, with provenance

**Files:** `tools/llm.py` · **Test:** `tests/test_llm.py`

**Interface**

```python
@dataclass(frozen=True)
class Provenance:
    model: str; prompt: str; prompt_version: str
    prompt_tokens: int; completion_tokens: int
    finish_reason: str; seconds: float

async def ask(
    prompt_name: str,
    output_model: type[BaseModelT],
    *,
    settings: Settings,
    max_tokens: int | None = None,    # None → settings.llm_max_tokens
    thinking: bool | None = None,     # None → settings.llm_thinking
    **variables,
) -> tuple[BaseModelT, Provenance]
```

Behaviour:

- `langchain-openai` `ChatOpenAI(...).with_structured_output(output_model,
  method=settings.llm_structured_method, include_raw=True)`.
- `include_raw=True` so a schema violation is a `LlmError` carrying the raw
  answer, not a silent `None` (audit A2).
- `finish_reason == "length"` → `LlmError` naming the token budget. This is the
  measured silent-failure mode of a reasoning model.
- One retry on a transport error or a parse failure, then `LlmError`.
- A module-level `asyncio.Semaphore(settings.llm_concurrency)` guards every
  call.

**New settings:** `llm_reasoning_effort: Literal["low","high","max"]`,
`llm_max_tokens: int = 32000`; `llm_concurrency` default changes 5 → 3.

GLM-5.3 cannot have thinking switched off — the chat template always opens a
`<think>` block — but `reasoning_effort` selects one of three levels and the
endpoint **defaults to `max`**. Every measurement above was therefore taken at
the most expensive setting. The chosen default is recorded in Task 6 after
the three levels are compared on top-10 overlap.

- [ ] **Step 1** — failing tests with `respx`: happy path returns model +
      provenance; `finish_reason="length"` raises; malformed JSON raises after
      one retry (assert two calls); the semaphore serialises to at most
      `llm_concurrency` in flight.
- [ ] **Step 2** — run, see them fail.
- [ ] **Step 3** — implement.
- [ ] **Step 4** — tests pass.
- [ ] **Step 5** — commit `feat: llm.ask with structured output`.

---

### Task 3: `doctor`

**Files:** `tools/doctor.py`, `cli.py` (+`doctor`) · **Test:** `tests/test_doctor.py`

Checks, each reported as one line, all run even if an earlier one fails:

| Check | Fails when |
|---|---|
| settings load | a required key is missing |
| endpoint reachable | connection error / non-200 |
| model answers | no content |
| structured output honours the schema | the answer does not validate |
| token budget | a deliberately long answer returns `finish_reason="length"` |
| database writable | `init_db` + a write to a temp table fails |
| `profile.toml` present | missing goal |

Exit code 0 when all pass, 1 otherwise — the Unix convention, so it can gate a
script.

- [ ] **Step 1** — failing tests, all endpoint calls mocked; one test asserts
      exit code 1 and that later checks still ran.
- [ ] **Step 2–4** — implement, pass, lint.
- [ ] **Step 5** — run against the real endpoint; expect every line to pass.
- [ ] **Step 6** — commit `feat: doctor command`.

---

### Task 4: criteria from the goal

**Files:** `profile.py`, `nodes/write_criteria.py`, `cli.py` (+`criteria`)
**Test:** `tests/test_write_criteria.py`

`profile.toml.example`:

```toml
goal = """..."""          # written with Koray before this task
about_me = """..."""      # used in M6 for the invitation
```

`write_criteria(goal, *, settings) -> SelectionCriteria` calls
`llm.ask("criteria", SelectionCriteria, goal=goal)` and stores the result on
the `runs` row as JSON (audit A3: criteria are data, inspectable and editable,
not hidden inside a prompt). `company-reach criteria --goal "…"` prints them
for review before any scoring is paid for.

`goal_hash(goal)` = first 12 hex of sha256 of the normalised goal; it keys the
score cache.

- [ ] **Step 1** — failing tests: criteria persisted on the run row;
      `goal_hash` stable across whitespace changes; `--goal` overrides
      `profile.toml`.
- [ ] **Step 2–4** — implement, pass, lint.
- [ ] **Step 5** — run for real, read the criteria with Koray, adjust
      `prompts/criteria.md` if they do not match the intent.
- [ ] **Step 6** — commit `feat: criteria from goal`.

---

### Task 5: `score_pool`

**Files:** `nodes/score_pool.py`, `cli.py` (+`score`)
**Test:** `tests/test_score_pool.py`

```python
async def score_pool(
    run_id: str, goal: str, criteria: SelectionCriteria, *,
    settings: Settings, limit: int | None = None, seed: int = 0,
) -> ScoreReport      # scored, cached, failed_batches, seconds
```

Steps inside:

1. Select candidates: `screen_reason IS NULL` **and** not already scored for
   `(goal_hash, prompt_version, model)` — the cache (audit A4).
2. `random.Random(seed).shuffle(candidates)` — decision (4) above.
3. Take `limit` (default `settings.score_limit = 500`).
4. Chunk into batches of `settings.score_batch_size` (50).
5. Run batches concurrently behind `llm.ask`'s semaphore.
6. Per batch: compare the returned UID set with the sent set; re-send the
   difference **once**; drop duplicates; validate `0 <= score <= 10`; drop and
   log entries whose uid was never sent.
7. Write rows; a failed batch is logged and counted, never fatal.

- [ ] **Step 1** — failing tests, all mocked: cache skips already-scored rows;
      same seed → same selection, different seed → different; a batch that
      returns 48 of 50 triggers exactly one re-send; an invented uid is
      dropped; a score of 11 is rejected; one failing batch does not abort the
      others.
- [ ] **Step 2–4** — implement, pass, lint.
- [ ] **Step 5** — commit `feat: score_pool with cache and shuffle`.

---

### Task 6: first real scoring run

- [ ] `company-reach doctor`
- [ ] `company-reach criteria` — read them with Koray.
- [ ] `company-reach score --limit 200` — expect ~7 minutes at 3 concurrent.
- [ ] Inspect: score distribution, the top 20 by score, and 10 random
      companies scored 0-2. Confirm the ranking is defensible before scoring
      more.
- [ ] `company-reach score --limit 200` again — expect 0 re-scored (cache) and
      200 new.
- [ ] Commit any prompt correction as `fix: …` with the measurement in the
      message.

---

### Task 7: golden set and the eval test

**Files:** `tests/fixtures/golden/scoring.jsonl`, `tests/test_prompts.py`

A golden set is a small set of items whose right answer a human fixed in
advance; it is how a prompt change is shown to be an improvement rather than a
different set of mistakes.

- [ ] Draw 30 companies stratified by the first run's scores (10 high, 10
      middle, 10 low).
- [ ] **Koray labels them** — fictionalised names and purposes before
      committing, because the repository is public.
- [ ] Test (opt-in, `RUN_LLM_EVALS=1`): score the 30, report Spearman
      correlation with the labels and the top-10 overlap. **Top-10 overlap is
      the number that matters** — M3 draws from the top, so stability there
      decides whether a prompt change is safe. Mean absolute error over the
      whole set does not.
- [ ] Record the first measurement in the milestone page as the baseline.
- [ ] Commit `test: scoring golden set`.

---

### Task 8: explanation and PR

- [ ] `docs/milestones/m2-llm-layer-and-scoring.md`, Turkish, following the
      template in `IMPLEMENTATION.md` §5 (map → data journey → file by file →
      new concepts with their general names → try it → test yourself → not yet
      → open threads).
- [ ] New concepts to introduce properly: structured output and why a schema
      beats "answer in JSON"; prompt versioning and why the cache key includes
      it; a reasoning model and its silent truncation; semaphore and
      concurrency; golden set; prompt injection and why company text sits
      inside delimiters.
- [ ] Update `README.md` status and the run section.
- [ ] PR "feat: M2 LLM layer and scoring", `Closes #2`.

---

## Open points to settle before Task 4

1. **The goal sentence itself.** Sector decided (manufacturing + technical
   services); the exact wording is written with Koray and goes in
   `profile.toml`.
2. **`about_me`.** Needed in M6, but `profile.toml` is created here, so a
   placeholder is fine for now.
3. **Golden-set labelling effort.** 30 companies, a few minutes each.

## Known rough edge

`prompts/` sits at the repository root, not inside the package, because the
point of the directory is that the prompts are easy to find and edit. It is
resolved from `__file__`, which works for the editable install this project
uses and for the "clone and run" path M8 targets, but a wheel installed from
PyPI would not carry it. `load_prompt` fails with the path it looked in, so
the failure is legible rather than mysterious. Revisit only if the tool ever
ships as a wheel.

## Self-review

- **Spec coverage:** audit A2 (`llm_structured_method` setting, `include_raw`,
  `doctor` probe) ✓ Task 2–3; A3 (criteria as data on the run row) ✓ Task 4;
  A4 (score cache keyed on goal, prompt version and model) ✓ Task 5.
- **Not in M2 by design:** LangGraph, `draw_batch`, search, fetching, the
  child graph.
- **Risk carried forward:** the goal excludes headcount deliberately, so the
  pool after scoring still contains one-person firms. They are removed in
  M5/M6 from website evidence, not here.
