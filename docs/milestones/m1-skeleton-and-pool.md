# M1 — Skeleton and Pool

What was built, why each library is there, and the five things worth
understanding before M2. Plan: `docs/plans/2026-09-19-m1-skeleton-and-pool.md`.

## What exists now

Two commands work end to end against the live register:

```bash
uv run company-reach pool --municipality 3203 --run-id first
# → 5467 companies stored for municipality 3203 (run first)

uv run company-reach screen --run-id first
# → kept 4335, dropped 1132
```

Measured on 2026-09-20, municipality 3203 (St. Gallen): 5,467 companies, all
UIDs unique, 96.7% carrying the boilerplate purpose tail. 18 tests, all
offline. No LangGraph, no LLM, no web search — those start at M2.

## Libraries used and why

- **uv** — project, virtual environment, lockfile and Python 3.13 itself, in
  one tool. Chosen over pip+venv+pip-tools because it replaces all three and
  `uv.lock` pins the exact tree for a fresh clone.
- **pydantic** — `CompanyRecord` validates on construction, so every record
  that exists is well-formed. The same class becomes an LLM output schema in
  M2; one definition, two jobs.
- **pydantic-settings** — `.env` into one typed object. Chosen over
  `os.environ` because it converts types, fails at startup rather than
  mid-run, and keeps secrets in `SecretStr` (never in `repr()` or a log).
- **httpx** — the SPARQL POST. Chosen over `requests` for its explicit
  timeout object and because `respx` mocks it cleanly in tests.
- **sqlite3** (stdlib) — one file, no server, and WAL lets the M7 review page
  read while a run writes.
- **typer** — turns a function signature into a command with `--help`. A
  parameter with a default becomes an option, one without becomes a
  positional argument.
- **respx** + **pytest** — every deterministic test runs offline.
- **ruff** — lint and format. Note `extend-exclude = ["*.md"]` in
  `pyproject.toml`: ruff 0.16 formats Python fenced in Markdown and rewrote
  the design docs on first run.

## Worth understanding

1. **PRAGMA order, then transaction control** — `tools/db.py:22-27`.
   `autocommit=False` opens a transaction before the first statement, and
   `PRAGMA journal_mode=WAL` cannot run inside one. So the connection opens
   in autocommit mode, the three PRAGMAs run, and only then does
   `conn.autocommit = False` take over. The plan had this the other way
   round; the test caught it.

2. **`with conn:` commits, it does not close** — `tools/db.py:45-48`.
   Success commits, an exception rolls back, and neither closes the
   connection; the `finally` does. All writes inside one `with` block are
   all-or-nothing.

3. **A finding is not an error** — `tools/lindas.py:68` and `112`. Three
   attempts, then `LindasError`; and zero companies also raises. If both
   returned an empty list, "this municipality has no companies" and "LINDAS
   is down" would be indistinguishable, and the pool would silently be
   written empty. The same distinction is a P1 audit condition for M4.

4. **Screening labels, it never deletes** — `screen.py:26-33` decides,
   `nodes/screen_pool.py` writes `screen_reason` onto the existing row.
   Survivors are not a list anywhere: they are the rows where
   `screen_reason IS NULL`. Every row is rewritten on each run, so changing a
   rule and re-running clears stale labels.

5. **The head clause carries the meaning** — `screen.py:9,21-23`. 96.7% of
   purpose texts end in a notary boilerplate tail that mentions
   `Grundstücke`. Cutting it before the rules run is what stops a joinery
   being read as a property firm. `Dienstleistung` and `Entwicklung` are
   deliberately absent from `_OPERATING` (`screen.py:13`): property firms use
   both (LEARNINGS §2).

## Where to look, in reading order

```
settings.py          (47 lines)  what is configurable
models.py            (34 lines)  what a company record is
screen.py            (33 lines)  the two rules
schema.sql           (36 lines)  the 12 tables; M1 fills one
tools/db.py          (85 lines)  connect · transaction · upsert
tools/lindas.py     (116 lines)  build_query · _post · _record · fetch_companies
nodes/load_pool.py   (11 lines)  lindas + db, three lines
nodes/screen_pool.py (28 lines)  read · decide · label
cli.py               (39 lines)  two commands
```

`load_pool.py` is the one place that splits `Settings` and hands each layer
only what it needs — `lindas.py` never imports `Settings` at all, which is
why its tests can point it at a fake URL.

## Check this

```bash
cp .env.example .env                       # any placeholder key works in M1
uv run pytest -q                           # 18 passed, offline
uv run company-reach --help
uv run company-reach pool --municipality 3203 --run-id first
uv run company-reach screen --run-id first
```

Then look at the data:

```bash
sqlite3 data/company_reach.db \
  "select coalesce(screen_reason,'(kept)'), count(*) from companies group by 1;" \
  -header -column
```

Expect roughly `(kept) 4335 · property only 799 · in liquidation 333`. Run
`pool` a second time: the row count stays 5,467 — `ON CONFLICT(uid) DO UPDATE`
(`tools/db.py:78`) makes re-running safe.

Worth reading once: `tests/test_lindas.py`, to see how `respx` answers the
SPARQL POST with a fixture so the test never touches the network.

## Open threads

- `llm_api_key` and `llm_model` are required by `Settings`, so `pool` needs a
  `.env` although it makes no model call. Left as is by decision on
  2026-09-20; revisit with `doctor` in M2.
- v0 kept 3,993 of 5,462; the same municipality now keeps 4,335 of 5,467.
  The two rules here are more permissive than v0's. Acceptable — over-keeping
  is the cheap error, and M2 scores the rest — but worth confirming against
  v0's full rule set.
- The research note records `schema:description` as missing on 3.2% of
  records; municipality 3203 returned it on 100% on 2026-09-20. `_record`
  still treats it as optional, and the fixture keeps a company without one.
- The phase-2 diagram in `docs/design/diagrams/` is stale (noted in the
  audit) and was not touched here.
