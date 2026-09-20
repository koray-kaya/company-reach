# M3 — Parent Graph Implementation Plan

Issue #3. Branch `feat/3-parent-graph`. Closes the audit's condition 1.

M3 turns four independent commands into one graph. Nothing new is asked of a
model: `llm.ask` already carries the semaphore, the retries, the truncation
check and the provenance, and M3 adds no call of its own. What M3 adds is the
loop — draw a batch, fan out one child per company, collect, decide whether to
draw again — and the three composition fixes the audit found in it.

Everything below the wrapper is a stub. `probe_search` returns true,
`enrich_company` runs a child graph whose single node recommends `skip`. The
point of the milestone is that the shape is right and provably right, so that
M4 and M5 can fill the child in without touching the parent.

## What the audit found, and what each fix is

All three are `High / P0` in `docs/design/audit-2026-09-19.md` §A1. They are
not three bugs; they are three faces of the same mistake — treating a compiled
subgraph as if it were a function.

**1. A compiled subgraph loses writes to `results`.** LangGraph's
`StateGraph._get_updates` keeps only `[(k, v) for k, v in input.items() if k in
output_keys]`. The child state has no `results` key, so the child's output is
filtered away without an error and `sendable_count` is always 0. The parent
then loops three times and reports nothing. *Fix:* a plain async function node
`enrich_company` that awaits the child and returns
`{"results": [CompanyResult(...)]}` itself.

**2. The declared error rule is not implementable.** With `direct` edges a node
that sets `error` does not end the child; the next node runs on empty input.
*Fix:* child nodes raise typed errors; the wrapper catches them. This is the
same wrapper as fix 1, which is why they are one task here.

**3. `collect` reads state, but resume repopulates only the database.** On a
rerun of the same `run_id` where every child is skipped, `results` in state is
empty, `need_another_batch` fires, and a fresh batch is drawn — contradicting
the resume rule. *Fix:* `collect` counts from the `results` **table** for the
`run_id`.

A fourth finding, `Medium / P1` — sync nodes running in a thread pool with a
module-level sqlite connection — **is already fixed.** M2's `tools/db.py` opens
one connection per call, with WAL and `busy_timeout`. Nothing to do; the plan
records it so the audit item can be closed with evidence.

## Decisions taken with Koray on 2026-09-20

1. **`draw_batch` applies a minimum score, `draw_min_score`, default 5.**
   Below it a company is never drawn, and `pool_exhausted` means "nothing left
   that clears the bar" rather than "nothing left at all".

   Measured on the 30-company golden set against the current score set
   (`goal_hash 5e6426a7`, prompt version 1, 275 scores):

   | threshold | hand ≥ 6 kept | hand 4–5 kept | hand ≤ 3 kept | pool kept |
   |---:|---|---|---|---|
   | 5 | 4/4 | 5/7 | 8/19 | 33% (92/275) |
   | 6 | 3/4 | 4/7 | 6/19 | 26% (72/275) |
   | 7 | 3/4 | 4/7 | 3/19 | 20% (55/275) |

   The decision sits between 5 and 6: the one company Koray scored 6 was scored
   5 by the model, so a threshold of 6 drops it while 7 removes no further good
   candidate and halves the junk. 6 is therefore the worst of the three — it
   pays 7's price without buying its cleanliness.

   5 was chosen because in M3 everything below the wrapper is a stub, so a junk
   company costs nothing today, whereas a lost candidate is a missing survey
   respondent. Retune in M4/M5 against real cost. Two cautions travel with the
   number: only 4 golden companies are hand-rated ≥ 6, and the measured noise
   floor (45/50 exact agreement between identical runs) means ±1 on a single
   score is noise.

2. **An errored company is recoverable in a later run, never in the same one.**
   `seen` keeps `uid` as primary key, so a company is not redrawn inside a run
   — that would loop. A different `run_id` may redraw it if its `results` row
   carries an `error_kind`: a company that errored was never contacted and no
   draft exists, so there is nothing to protect it from. This closes audit A4's
   "`seen` + `error` burns companies" without changing the schema.

3. **`batch_size` default 10**, from the design's "review starts once ten
   candidates are ready" (`design.md:31`). It joins `settings.py`, which today
   has `score_batch_size` (50, for scoring) but no batch size for drawing.

### Settled by the design already — not reopened

- Loop exit: `sendable_count == 0 and batches_drawn < max_batches_per_run and
  not pool_exhausted`. `max_batches_per_run = 3` is already in `settings.py`.
- `Recommendation` is `send | hold | skip` with a reason; *sendable* means
  `send`. Error kinds are `search | fetch | llm | other`.
- `manifest.json` at `data/runs/<run_id>/manifest.json` holds goal, criteria,
  seed, model, prompt versions with sha, settings, counts, cost per node and
  the git commit, written at start and at end (`graph.spec.yaml:246`).
- `recursion_limit=40`, passed explicitly in `ainvoke(config=...)`; the
  LangGraph default is 1000.

## One open thread this plan corrects

`CLAUDE.local.md` carries three golden-set labels "the goal still does not
explain": hand 5 / model 0, hand 6 / model 2, hand 4 / model 9. Only the last
pair exists in the current score set. Those notes were written against an
earlier scoring run, so the open thread cannot be carried into M3 as written.
Task 8 re-derives it from current data or drops it.

## Global constraints

- Test first. Write the failing test, run it, see it fail, then write the code.
- Files English, conventional commits ≤ 60 chars.
- No new model call anywhere in M3.
- Every parent node stays a plain function callable without the graph.
- No checkpointer, no `interrupt()`, no memory — the flow needs none.
- Real company data stays in `data/`; fixtures use fictional companies.

## File structure

```
pyproject.toml        # + langgraph (not a dependency yet)
src/company_reach/
  graph.py            # new: ReachState, nodes wired, routers, run()
  manifest.py         # new: manifest.json at start and end
  models.py           # + Recommendation, CompanyResult
  settings.py         # + batch_size, draw_min_score
  nodes/
    probe_search.py   # new: stub until M4
    enrich_company.py # new: the wrapper — the P0 fix
  tools/db.py         # + draw_batch, record_result, count_sendable
tests/
  test_draw_batch.py  # new
  test_enrich.py      # new
  test_graph.py       # new — the issue's demo
  test_manifest.py    # new
```

---

### Task 1: The dependency, state, result types, and the two settings

**Files:** `pyproject.toml`, `models.py`, `settings.py`, `graph.py` (state only).
**Test:** `tests/test_graph.py` (state shape only for now).

`langgraph` is not installed — M1 and M2 needed no graph, and the project's
rule is to add the established library when a step needs it, not before. This
is that step: `uv add langgraph`. It arrives with `langchain-core`, which
`langchain-openai` already pulls in.

**Interfaces**

```python
Recommendation = Literal["send", "hold", "skip"]
ErrorKind = Literal["search", "fetch", "llm", "other"]

class CompanyResult(BaseModel):
    uid: str
    recommendation: Recommendation | None = None
    reason: str | None = None
    error_kind: ErrorKind | None = None
    error_text: str | None = None

class ReachState(TypedDict):
    run_id: str
    goal: str
    about_me: str
    municipality: str
    batch_size: int
    seed: int
    pool_count: int
    kept_count: int
    criteria: SelectionCriteria | None
    batch_uids: list[str]          # replaced each loop
    batches_drawn: int
    pool_exhausted: bool
    results: Annotated[list[CompanyResult], operator.add]
    sendable_count: int
```

`Annotated[..., operator.add]` is the reducer: several `enrich_company` nodes
write `results` in the same superstep, and without a reducer LangGraph would
treat concurrent writes to one key as a conflict. `operator.add` on lists
concatenates, so ten children produce ten entries.

- [ ] **Step 1** — failing tests: `CompanyResult` rejects an unknown
      `error_kind`; `ReachState` carries `results` with an `operator.add`
      reducer (assert on `ReachState.__annotations__`).
- [ ] **Step 2** — run, see them fail.
- [ ] **Step 3** — write the models, add `batch_size: int = 10` and
      `draw_min_score: int = 5` to `Settings` with the measurement in a
      comment, define `ReachState`.
- [ ] **Step 4** — green, `ruff check`.

---

### Task 2: `draw_batch`

**Files:** `tools/db.py` (`draw_batch`, `record_seen`), `graph.py`.
**Test:** `tests/test_draw_batch.py`

This is where both decisions live. The query:

```sql
select c.uid, c.name, ...
  from companies c
  join scores s
    on s.uid = c.uid and s.goal_hash = ?
   and s.prompt_version = ? and s.model = ?
 where c.screen_reason is null
   and s.score >= ?                                 -- decision 1
   and (c.uid not in (select uid from seen)
        or c.uid in (select uid from results         -- decision 2
                      where error_kind is not null
                        and run_id <> ?))
 order by s.score desc
 limit ?
```

No tie-breaking, deliberately: scoring order was already shuffled with the
run's seed, so equal scores sit in random order (M2 plan, "Decisions taken
with Koray"). Adding `order by ..., uid` here would undo that and bias every
batch towards low UIDs, which are the oldest firms in the canton.

`c.screen_reason is null` matches `unscored_companies` — screened-out
companies never enter any stage.

Reuse on rerun: if `seen` already holds rows for this `run_id` at
`batch_no = batches_drawn + 1`, return those uids instead of querying. A rerun
must repeat the batch it drew, not draw a fresh one.

`pool_exhausted` is set when the query returns nothing.

- [ ] **Step 1** — failing tests, on an in-memory database with fictional
      companies: drawn in descending score; a company scoring below
      `draw_min_score` is never drawn; a `seen` company is not redrawn; a
      `seen` company whose `results` row has an `error_kind` from *another*
      run **is** redrawn; the same company with an error from *this* run is
      **not**; a screened-out company is never drawn; an empty result sets
      `pool_exhausted`; a rerun of the same `run_id` returns the recorded
      batch.
- [ ] **Step 2** — run, see them fail.
- [ ] **Step 3** — write `draw_batch` and `record_seen`.
- [ ] **Step 4** — green.

---

### Task 3: `enrich_company` — the wrapper

**Files:** `nodes/enrich_company.py`, `tools/db.py` (`record_result`).
**Test:** `tests/test_enrich.py`

The P0 fix, and the reason M3 exists. A plain async function, not a compiled
subgraph used as a node:

```python
async def enrich_company(state: ChildInput) -> dict:
    try:
        out = await child_graph.ainvoke({...})
        result = CompanyResult(uid=uid, recommendation=out["recommendation"], ...)
    except (SearchError, FetchError, LlmError) as e:
        result = CompanyResult(uid=uid, error_kind=_kind(e), error_text=str(e)[:500])
    except Exception as e:                      # nothing escapes
        result = CompanyResult(uid=uid, error_kind="other", error_text=str(e)[:500])
    record_result(result, run_id)               # the table is the record
    return {"results": [result]}
```

Two properties the tests must pin, because both fail silently otherwise:

- **It returns normally on any exception.** LangGraph cancels sibling `Send`
  tasks when one raises (`pregel/_runner.py`, `_should_stop_others`). "One
  failure does not stop the others" is true only because the wrapper never
  raises.
- **It writes `{"results": [...]}` itself.** This is the key the parent owns.

On rerun it skips a uid whose `results` row exists without an `error_kind` —
that company is done — and still returns its stored result, so the superstep's
accounting stays whole.

The child for M3 is a one-node stub graph that returns
`recommendation="skip"`. A test double raises instead.

- [ ] **Step 1** — failing tests: a raising child yields
      `error_kind="other"` and no exception escapes; a typed `LlmError` maps
      to `error_kind="llm"`; the returned dict has exactly the key `results`
      with one `CompanyResult`; the row lands in the `results` table; a rerun
      over a finished uid does not re-invoke the child but still returns its
      result.
- [ ] **Step 2** — run, see them fail.
- [ ] **Step 3** — write the wrapper and `record_result`.
- [ ] **Step 4** — green.

---

### Task 4: `collect` and `need_another_batch`

**Files:** `graph.py`, `tools/db.py` (`count_sendable`).
**Test:** `tests/test_graph.py`

```python
def collect(state: ReachState) -> dict:
    with connect(settings.db_path) as conn:
        n = count_sendable(conn, state["run_id"])   # the TABLE, not state
    return {"sendable_count": n}

def need_another_batch(state: ReachState) -> str:
    if state["sendable_count"] == 0 \
       and state["batches_drawn"] < settings.max_batches_per_run \
       and not state["pool_exhausted"]:
        return "draw_batch"
    return "__end__"
```

`count_sendable` is `select count(*) from results where run_id = ? and
recommendation = 'send'`. Counting from state is fix 3's bug: a resumed run
has an empty `results` list in state and a full table.

- [ ] **Step 1** — failing tests: `collect` counts from the table and ignores
      state; a run whose results are all `skip` gets `sendable_count = 0`;
      `need_another_batch` returns `draw_batch` only under all three
      conditions; `pool_exhausted` ends the run even at `batches_drawn = 0`.
- [ ] **Step 2** — run, see them fail.
- [ ] **Step 3** — write both.
- [ ] **Step 4** — green.

---

### Task 5: wire the graph

**Files:** `graph.py`, `nodes/probe_search.py`.
**Test:** `tests/test_graph.py` — the issue's demo

```
load_pool → screen_pool → write_criteria → score_pool → probe_search
    → draw_batch → fan_out ⇉ enrich_company ⇉ collect
                                 ↑                  │
                                 └── need_another_batch
```

`fan_out` returns `[Send("enrich_company", {...}) for uid in batch_uids]`, and
`[]` when the batch is empty — routed straight to `collect` so an empty batch
cannot hang the graph.

`probe_search` is a stub returning true until M4; it exists now so that the
edge is real and M4 only replaces a body.

`recursion_limit=40` is passed in `ainvoke(config=...)`. The default is 1000,
so an unset limit is not a defence — a routing bug would spin a thousand
supersteps before failing.

- [ ] **Step 1** — failing tests, the demo from issue #3: one raising child +
      one sending child → `len(results) == 2` and `sendable_count == 1`; a
      rerun with every company already finished draws no second batch; an
      empty batch routes to `collect` without a `Send`; the compiled graph
      carries `recursion_limit=40`.
- [ ] **Step 2** — run, see them fail.
- [ ] **Step 3** — build the `StateGraph`, wire the edges and the two routers.
- [ ] **Step 4** — green, and the whole suite still offline in seconds.

---

### Task 6: `manifest.py`

**Files:** `manifest.py`.
**Test:** `tests/test_manifest.py`

`data/runs/<run_id>/manifest.json`, written at start and rewritten at end:
goal, `goal_hash`, criteria, seed, model, prompt versions with their sha,
the settings that shaped the run (`batch_size`, `draw_min_score`,
`max_batches_per_run`, `llm_*`), counts, and the git commit. The secret never
enters it — `Settings` holds `SecretStr`, and the dump must be checked for it.

Its job is that a run can be explained six months later without the database.

- [ ] **Step 1** — failing tests: written at start with `status="running"`;
      rewritten at end with counts and `status`; contains the prompt sha; a
      `SecretStr` value never appears anywhere in the JSON.
- [ ] **Step 2** — run, see them fail.
- [ ] **Step 3** — write it.
- [ ] **Step 4** — green.

---

### Task 7: `run --dry`

**Files:** `cli.py`.
**Test:** `tests/test_graph.py` (CLI path)

`company-reach run --dry` runs the graph with the stub child: draws a batch,
loops, prints the report. `--dry` is what makes M3 demonstrable before M4
exists — no search, no fetch, no model call beyond the pool stages that
already work.

Prints one block: pool, kept, scored, batches drawn, results, sendable, and
where the manifest is.

- [ ] **Step 1** — failing test: `run --dry` exits 0 on a seeded test database
      and prints the counts.
- [ ] **Step 2** — run, see it fail.
- [ ] **Step 3** — write the command.
- [ ] **Step 4** — green.

---

### Task 8: explanation, open threads, PR

- [ ] `docs/milestones/m3-parent-graph.md` — Turkish, local only (the
      directory is gitignored since 2026-09-20). Same shape as the M2 page:
      map, data journey, file by file, the new concepts with their general
      names (reducer, superstep, fan-out, `Send`, why a subgraph is not a
      function), things to break on purpose, self-test questions.
- [ ] Re-derive the "three unexplained labels" thread against the current
      score set, or drop it, and update `CLAUDE.local.md` — including
      `llm_max_tokens`, which is 50000 since `253c9fa` and still recorded as
      32000.
- [ ] Close audit condition 1 with evidence: the three P0 findings and the P1
      sqlite finding, each with the test that pins it.
- [ ] PR, review, merge.

## Open points, settled 2026-09-20

- **`run --dry` starts from a populated database.** It skips `load_pool` and
  `score_pool` and begins at `probe_search` → `draw_batch`. The command is
  then fully offline and runs in seconds, which is what makes it testable and
  demonstrable before M4 exists. The cost is that it is meaningless on an
  empty database, so it must fail with a message naming `pool`, `screen` and
  `score` as the commands to run first — not with a zero-row report that
  looks like a working run.
- **The stub child graph lives in `graph.py`** until M4 gives it nodes, so it
  is moved once rather than twice.

## Known rough edge

`batches_drawn` is incremented by `draw_batch`, which also decides
`pool_exhausted`. A node that both advances a counter and sets an exit flag is
doing two things, and the loop's correctness depends on the order. The test
suite pins the behaviour; if M4 makes this harder to follow, split it.

## Self-review

- Every task writes its failing test first and runs it.
- No task introduces a model call.
- Each of the audit's three P0 findings has a task and a test that fails
  without the fix.
- The suite stays offline and under a few seconds.
