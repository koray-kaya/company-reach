# Audit fixes — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the verified findings of the post-M8 audit so the first real invitations can go out, then make the tool fit the scale of the survey.

**Architecture:** Seven phases, each a short-lived branch and one PR. Phases A–D come before the first real Send; E–G come after a pilot. Phase A is planned below step by step. Phases B–G are planned at task level: files, behaviour, the failing test, acceptance. Each one gets its step-level plan when it starts, once its open point is answered (`IMPLEMENTATION.md` §1).

**Tech stack:** Python 3.13, uv, LangGraph, langchain-openai, httpx, SQLite, FastAPI, pytest + respx. New dependencies: none in Phase A; `tldextract` in Phase E (open point 5).

**Spec:** `docs/design/audit-2026-09-24-build.md`, the post-M8 audit. It is kept local, uncommitted, until Phase H is done. Local evidence for every finding (gitignored): `data/audit-2026-09-24/consolidated.json`. The German invitation study: `data/audit-2026-09-24/invitation/`.

## Global constraints

- Test first. Run the failing test before writing code (`CLAUDE.local.md`).
- Conventional commits, at most 60 characters, in English.
- All files are in English. Fixtures use fictional companies and people. No real company or person data, no private names or paths, and no endpoint URL anywhere in git.
- Use the established library for a job (langchain-openai, LangGraph, FastAPI, trafilatura). Keep our own code plain.
- Prompts live in `src/company_reach/prompts/` and carry a version header. Bump the version on every change.
- Never send mail automatically. Never request LinkedIn.
- `main` changes only through a short-lived branch, a PR and Koray's review. Each phase is one PR.
- `uv run pytest -q`, `uv run ruff check .` and `uv run ruff format --check .` pass at the end of every task.

## Decided on 2026-09-25, with Koray

1. **Company size is out of scope.** No source says how many people work at a company, so the 10–49 band cannot be detected. The audit findings on size (H12 and gap round 1) are closed as "won't fix". The sample is described in the thesis rather than filtered in code.
2. **Brave Search API replaces Serper as the paid search provider.** The key is in `.env` as `BRAVE_SEARCH_API_KEY`. The lessons of an earlier project that used Brave carry over:
   - Treat a Brave 5xx as an error, not as `[]`. Silent `[]` was a recorded debt there.
   - Log the number of queries per company.
   - Verify that a captured profile belongs to the company.

   Terms, checked on 2026-09-25:
   - $5 per 1,000 requests, with $5 of free credit a month, so about 1,000 free queries a month.
   - 50 requests per second.
   - The standard terms forbid storing results beyond transient operational use. So for a Brave query we store the query and counts, never the result list (Phase B, task B6).

## Open points

All were answered on 2026-09-25. Koray chose Brave as the secondary provider (point 1) and accepted the recommendation on points 2–6. Point 7 waits for his explicit go, because it is irreversible.

| # | Question | Phase | Recommendation |
|---|---|---|---|
| 1 | When is Brave asked? | B | **Decided: Brave is secondary.** SearXNG goes first. Brave is asked (a) whenever SearXNG errors or answers nothing, and (b) once more before any "no website" verdict. A company is written off only if Brave also finds no candidate. Estimated cost for 60 completed surveys (about 3,000 companies drawn) is 1,500–4,500 Brave queries: about $0–20 beyond the monthly credit. The alternative, Brave first, costs about 12,000 queries (~$60) and removes SearXNG's self-throttling entirely. |
| 2 | Is it acceptable that Brave-sourced candidate lists are not stored (terms)? | B | Yes. Store the query, the provider, the count and the chosen site. The chosen site is our own verified finding, fetched from the site itself. |
| 3 | Run the "voll / kurz" length A/B from the first real send? | C | Yes. It tests your "too long" hypothesis with the survey's own UID, with no second mail. |
| 4 | After a Send, may a company be marked `not_sent` (the mail client never opened) or `bounced`, and then be sent to another address? | D | Yes. A mail nobody received is not a contact. `sent` stays final once it is not reversed within the same session. |
| 5 | Use `tldextract` (public suffix list) for registered domains? | E | Yes. It fixes subdomain and site-builder hosts (`firma.wixsite.com`, `mail.firma.ch`) without a hand-kept list. |
| 6 | Where does the review page run? | G | Under `uv` next to the CLI, and drop the Docker `app` service. Today the page and the CLI share one SQLite WAL file across Docker Desktop's VM boundary, which SQLite does not support. |
| 7 | Repository hygiene (details kept locally): rewrite and force-push, or recreate the repository? | H | Recreate. Waits for Koray's explicit go. |

## Phases

| Phase | Closes | Before first Send? | Effort | Needs |
|---|---|---|---|---|
| **A** Stop silent failures | H5, H8, H9, H11, K1, K21, graph-cli medium (no progress) | yes | ~1 day | — |
| **B** Trustworthy search (Brave) | H2, H3, H4, #20, SSRF redirect, redirecting candidates, self-throttling | yes | ~2 days | open points 1, 2 |
| **C** Invitation frame@1 and redraft | H1/K3, invented institution, placeholder, K4, subject unchecked, failed draft sendable, K15 | yes | ~2 days | open point 3; Koray fills `[sender]` and `[invitation]` |
| **D** Deletion and ledger integrity | H6, H7, address-level suppression, draft identity on Send, ledger snapshot, K8, backups in forget | yes | ~1.5 days | open point 4 |
| — | **Pilot: 30–40 real sends** after the ethics check | — | — | A–D merged |
| **E** Contact and reading quality | K2, person = company name, charset, Impressum footer, pick_pages floor, subdomains, marker hardening | no | ~2 days | open point 5 |
| **F** Scoring integrity and throughput | H10, guard predicate, screen at import, `run --target`, several municipalities, `status` | no | ~2 days | — |
| **G** Evaluation and operations | eval design, evals on a temp DB, tracing enforced, Docker, CI, dependency hygiene, dead code | no | ~1.5 days | open point 6 |
| **H** Repository hygiene (Koray) | local audit notes | as soon as decided | ~2 h | open point 7 |

## Review focus

These five inputs are not exercised by any task's own tests. Each is pinned by a test in the phase named.

1. **Ctrl-C mid-batch, then the same `--run-id` again.** Every drawn company ends either finished or retryable; none is lost. Pinned in A4.
2. **SearXNG suspended and the Brave quota exhausted (HTTP 402 or 429) at the same time.** The companies become search errors, never "no website". Pinned in B3.
3. **`survey_url` changes after drafts exist.** `redraft` restores every undecided send card, and no card ever offers an old link. Pinned in C6.
4. **A deletion request from an address the tool never stored.** `forget` explains how to find the company (the `c=` UID in the quoted link) and exits non-zero rather than printing "0 companies" as success. Pinned in D1.
5. **An invalid Brave key (401).** `doctor` fails, and a run stops at the probe instead of recording companies as having no website. Pinned in B2 and A5.

---

## Phase A — stop silent failures

Branch `fix/audit-a-silent-failures`. Every task below is independent of open points.

### Task A1: the LLM timeout reaches the request; back off between attempts

Audit H5: `ChatOpenAI` gets no timeout, so the OpenAI SDK sends `timeout=None` per request and overrides the injected httpx client's timeout. A silent endpoint held the golden evaluation for 44 minutes.

**Files:**
- Modify: `src/company_reach/tools/llm.py` (`_client`, `ask`)
- Modify: `src/company_reach/settings.py` (new `llm_retry_pause_s`)
- Modify: `tests/conftest.py` (set `LLM_RETRY_PAUSE_S=0`)
- Test: `tests/test_llm.py`

**Interfaces:**
- Produces: `Settings.llm_retry_pause_s: float = 2.0`. `llm.ask` raises `LlmError` within about `2 × llm_timeout_seconds + llm_retry_pause_s` on a silent endpoint.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_llm.py — add at the end
import asyncio
import time


async def test_a_silent_endpoint_times_out(settings):
    """Audit H5: the timeout never reached the request, and a silent
    endpoint held a run for 44 minutes. One second here must mean one."""

    async def never_answer(reader, writer):
        await reader.read()  # returns when the client gives up and closes
        writer.close()

    server = await asyncio.start_server(never_answer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    s = settings.model_copy(
        update={"llm_base_url": f"http://127.0.0.1:{port}/v1", "llm_timeout_seconds": 1.0}
    )
    started = time.monotonic()
    async with server:
        with pytest.raises(LlmError):
            await llm.ask("criteria", SelectionCriteria, settings=s, goal="g")
    assert time.monotonic() - started < 10


@respx.mock
async def test_a_rejected_request_is_not_retried(settings):
    """A 400 says the request is wrong. Asking again the same way only
    doubles the wait."""
    route = respx.post(URL).mock(return_value=httpx.Response(400, json={"error": {"message": "bad"}}))
    with pytest.raises(LlmError):
        await llm.ask("criteria", SelectionCriteria, settings=settings, goal="g")
    assert route.call_count == 1
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_llm.py -k "silent_endpoint or rejected_request" -v`
Expected: the first test hangs until pytest's own limit or fails on the 10-second assertion. The second fails with `call_count == 2`.

- [ ] **Step 3: Implement**

In `settings.py`, next to `llm_timeout_seconds`:

```python
    # Between the two attempts of one model call. A transient overload
    # rarely clears in the same second; tests set it to 0.
    llm_retry_pause_s: float = 2.0
```

In `tests/conftest.py` inside the `settings` fixture:

```python
    monkeypatch.setenv("LLM_RETRY_PAUSE_S", "0")
```

In `llm.py`, pass the timeout to the model client, and stop retrying requests the endpoint rejected:

```python
from openai import APIStatusError, LengthFinishReasonError

def _client(settings, max_tokens, effort, http_client) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        temperature=0,
        max_tokens=max_tokens,
        reasoning_effort=effort,
        http_async_client=http_client,
        # The SDK sends its own per-request timeout, and None overrides the
        # injected client's. Audit H5: without this a silent endpoint hangs.
        timeout=settings.llm_timeout_seconds,
        max_retries=0,
    )
```

and in `ask`, inside the attempt loop:

```python
            for attempt in range(2):
                if attempt:
                    await asyncio.sleep(settings.llm_retry_pause_s)
                started = time.monotonic()
                try:
                    answer = await chain.ainvoke(text)
                except LengthFinishReasonError as e:
                    raise _truncated(prompt_name, budget, effort) from e
                except APIStatusError as e:
                    if 400 <= e.status_code < 500 and e.status_code not in (408, 429):
                        raise LlmError(f"{prompt_name}: endpoint rejected the request: {e}") from e
                    last = e
                    continue
                except Exception as e:  # transport, timeout, endpoint error
                    last = e
                    continue
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_llm.py -v`
Expected: all pass, and the silent-endpoint test finishes in about 2 seconds.

- [ ] **Step 5: Commit**

```bash
git add src/company_reach/tools/llm.py src/company_reach/settings.py tests/conftest.py tests/test_llm.py
git commit -m "fix: the LLM timeout reaches the request"
```

### Task A2: an empty or commented secret is no secret

Audit H11: `SERPER_API_KEY=   # optional…` is read as the key `# optional…`, which arms the paid fallback. Koray's `.env` has this shape; verified on 2026-09-25.

**Files:**
- Modify: `src/company_reach/settings.py`
- Modify: `.env.example` (every comment on its own line)
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces: `Settings._blank_is_none`, applied to `serper_api_key` and `playwright_url`. Phase B adds `brave_search_api_key` to the same validator.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_settings.py — add
import pytest


@pytest.mark.parametrize("raw", ["", "   ", "# optional, used only when SearXNG errors"])
def test_an_empty_or_commented_key_is_no_key(monkeypatch, tmp_path: Path, raw):
    """Audit H11: python-dotenv keeps a trailing comment as the value, and
    a key that is not None arms the paid fallback."""
    monkeypatch.setenv("LLM_API_KEY", "abc")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("SERPER_API_KEY", raw)
    assert Settings(_env_file=None, data_dir=tmp_path).serper_api_key is None


def test_the_example_env_arms_no_fallback(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    s = Settings(_env_file=".env.example", data_dir=tmp_path)
    assert s.serper_api_key is None
    assert s.playwright_url is None
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_settings.py -v`
Expected: the comment case and the example-file test fail, because the key reads `'# optional…'`.

- [ ] **Step 3: Implement**

```python
from pydantic import SecretStr, field_validator

    @field_validator("serper_api_key", "playwright_url", mode="before")
    @classmethod
    def _blank_is_none(cls, value):
        """An unset optional key is None, however the .env line was
        written. `KEY=   # comment` reaches us as the comment."""
        if isinstance(value, str) and (not value.strip() or value.strip().startswith("#")):
            return None
        return value
```

In `.env.example`, move each trailing comment onto the line above its key. For example:

```
# Optional. Used only when SearXNG errors.
SERPER_API_KEY=
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_settings.py tests/test_search.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/company_reach/settings.py .env.example tests/test_settings.py
git commit -m "fix: a commented .env key no longer arms Serper"
```

### Task A3: a failed run says why; `--goal` keeps "about me"

Audit K1: `raise typer.Exit(1) from error` prints nothing. Audit H9: `run --goal` blanks `about_me`, so every draft of that run is written without anyone identified as writing.

**Files:**
- Modify: `src/company_reach/cli.py` (`run`)
- Modify: `src/company_reach/manifest.py` (`finish_manifest` gains `reason`)
- Test: `tests/test_cli.py`, `tests/test_manifest.py`

**Interfaces:**
- Produces: `finish_manifest(run_id, *, settings, status, counts, criteria=None, reason: str | None = None)`. The manifest carries `"reason"` when the run failed.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py — add
from company_reach.errors import SearchError


def test_a_failed_run_says_why(settings, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    _seed_scored_pool(settings, {"CHE000000001": 9})

    async def boom(*args, **kwargs):
        raise SearchError("SearXNG unreachable: connection refused")

    monkeypatch.setattr(cli, "run_graph", boom)
    r = runner.invoke(cli.app, ["run", "--goal", "make and sell", "--run-id", "r1"])

    assert r.exit_code == 1
    assert "SearXNG unreachable" in r.output
    manifest = json.loads(manifest_path("r1", settings=settings).read_text())
    assert "SearXNG unreachable" in manifest["reason"]


def test_run_with_a_goal_keeps_about_me(settings, monkeypatch):
    """Audit H9: --goal blanked about_me, and the drafts said nothing about
    who writes."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    settings.profile_path.write_text(
        'goal = "Firms that make things."\n'
        'about_me = "Eine Studentin der Beispiel-Hochschule."\n'
        'survey_url = "https://survey.example/form"\n'
    )
    _seed_scored_pool(settings, {"CHE000000001": 9})
    captured: dict = {}

    async def fake_run_graph(state, **kwargs):
        captured.update(state)
        return state | {"pool_exhausted": True}

    monkeypatch.setattr(cli, "run_graph", fake_run_graph)
    r = runner.invoke(cli.app, ["run", "--dry", "--goal", "make and sell", "--run-id", "r2"])

    assert r.exit_code == 0, r.output
    assert captured["about_me"] == "Eine Studentin der Beispiel-Hochschule."
```

```python
# tests/test_manifest.py — add
def test_a_failed_run_records_its_reason(settings: Settings):
    start_manifest("r1", settings=settings, goal="g", seed=0)
    finish_manifest("r1", settings=settings, status="failed", counts={}, reason="SearchError: down")
    data = json.loads(manifest_path("r1", settings=settings).read_text())
    assert data["reason"] == "SearchError: down"
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_cli.py -k "failed_run_says_why or keeps_about_me" tests/test_manifest.py -v`
Expected: no reason in the output; `about_me == ""`; `finish_manifest` does not accept `reason`.

- [ ] **Step 3: Implement**

In `manifest.py`:

```python
def finish_manifest(run_id, *, settings, status, counts, criteria=None, reason=None) -> Path:
    fields: dict[str, Any] = {"status": status, "finished_at": now(), "counts": counts}
    if reason:
        fields["reason"] = reason
    ...
```

In `cli.py`, in `run`, set `about_me=load_profile(s.profile_path).about_me,` unconditionally. Write the failure path as:

```python
    except Exception as error:
        reason = f"{type(error).__name__}: {error}"
        finish_manifest(rid, settings=s, status="failed", counts={}, reason=reason)
        typer.echo(f"run {rid} failed — {reason}", err=True)
        raise typer.Exit(1) from error
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_cli.py tests/test_manifest.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/company_reach/cli.py src/company_reach/manifest.py tests/test_cli.py tests/test_manifest.py
git commit -m "fix: a failed run prints its cause, --goal keeps about_me"
```

### Task A4: a drawn batch survives a crash

Audit H8: `draw_batch` writes `seen` before any `results` row. A crash in between strands the batch: no new run draws it, and `retry` finds nothing. The fix gives every drawn company an `interrupted` result row in the same transaction as `seen`. The child replaces that row when it finishes. `retry` and `draw_batch` already treat any error row as unfinished.

**Files:**
- Modify: `src/company_reach/models.py` (`ErrorKind` gains `"interrupted"`)
- Modify: `src/company_reach/tools/db.py` (new `record_pending`)
- Modify: `src/company_reach/graph.py` (`draw_batch`)
- Test: `tests/test_graph.py`

**Interfaces:**
- Produces: `db.record_pending(conn, uids: list[str], *, run_id: str) -> None` (INSERT OR IGNORE, so finished rows are kept on a rerun). `ErrorKind` includes `"interrupted"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_graph.py — add
from company_reach.tools.db import errored_uids


def test_a_drawn_batch_is_retryable_before_any_child_finishes(settings):
    """Audit H8: seen was written before any result row, so a crash in
    between stranded the batch for good."""
    _seed(settings, {"CHE000000001": 9, "CHE000000002": 8})
    out = graph_module.draw_batch(_start(settings), settings=settings)
    with connect(settings.db_path) as conn:
        assert errored_uids(conn, "r1") == sorted(out["batch_uids"])


async def test_a_finished_run_leaves_nothing_interrupted(settings):
    _seed(settings, {"CHE000000001": 9, "CHE000000002": 7})
    child = ChildByUid({"CHE000000001": "skip", "CHE000000002": "send"})
    await run_graph(_start(settings), settings=settings, dry=True, child=child)
    with connect(settings.db_path) as conn:
        n = conn.execute(
            "select count(*) from results where error_kind = 'interrupted'"
        ).fetchone()[0]
    assert n == 0
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_graph.py -k "retryable_before or leaves_nothing_interrupted" -v`
Expected: the first fails, because `errored_uids` returns `[]`.

- [ ] **Step 3: Implement**

```python
# models.py
ErrorKind = Literal["search", "fetch", "llm", "shab", "interrupted", "other"]
```

```python
# tools/db.py
def record_pending(conn: sqlite3.Connection, uids: list[str], *, run_id: str) -> None:
    """A result row for every company of a batch, written with `seen` in one
    transaction. It reads as an error until the child replaces it, so a
    crash leaves the company retryable instead of drawn and forgotten."""
    conn.executemany(
        """INSERT OR IGNORE INTO results (run_id, uid, recommendation, reason,
             error_kind, error_text, finished_at) VALUES (?,?,?,?,?,?,?)""",
        [
            (run_id, uid, None, None, "interrupted",
             "drawn, but the run stopped before this company finished", now())
            for uid in uids
        ],
    )
```

In `graph.py`, `draw_batch`, inside the existing `with connect(...)` block:

```python
        if uids:
            record_seen(conn, uids, run_id=state["run_id"], batch_no=batch_no)
            record_pending(conn, uids, run_id=state["run_id"])
```

- [ ] **Step 4: Run the whole graph and draw suites**

Run: `uv run pytest tests/test_graph.py tests/test_draw_batch.py tests/test_enrich.py tests/test_review_cards.py -v`
Expected: all pass. The rerun test still calls no child, because finished rows are kept.

- [ ] **Step 5: Commit**

```bash
git add src/company_reach/models.py src/company_reach/tools/db.py src/company_reach/graph.py tests/test_graph.py
git commit -m "fix: a crash no longer strands a drawn batch"
```

### Task A5: doctor checks what a run needs

Audit K21: `doctor` loads 3 of the 7 prompts a run uses and never probes SearXNG. It cannot see that two of the three baseline engines do not exist. It reports the placeholder `survey_url` as ok, and does not show `SENDING_APPROVED` or the paid fallback.

**Files:**
- Modify: `src/company_reach/tools/doctor.py`
- Test: `tests/test_doctor.py` (the existing `test_all_checks_pass` gains the search mocks and a non-placeholder profile)

**Interfaces:**
- Consumes: `tools.search.search`, `nodes.probe_search.PROBE_QUERY`, `settings.baseline_engines`
- Produces: checks named `search` and `engines`. The `settings` line shows `sending_approved=` and `paid_fallback=`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_doctor.py — add
SEARX = "http://searxng:8080"


def _search_ok():
    respx.get(f"{SEARX}/search").mock(return_value=httpx.Response(200, json={
        "results": [{"url": "https://www.example-register.ch/", "title": "t", "content": "c", "engine": "duckduckgo"}],
        "unresponsive_engines": [],
    }))


def _config(enabled: list[str]):
    respx.get(f"{SEARX}/config").mock(return_value=httpx.Response(200, json={
        "engines": [{"name": n, "enabled": True} for n in enabled]
    }))


@respx.mock
async def test_a_missing_baseline_engine_fails(settings):
    """Round-1 live run: mojeek and startpage were inactive in the pinned
    SearXNG, so the guard for "every baseline engine is down" could never fire."""
    respx.post(URL).mock(side_effect=[probe_ok(), probe_truncated()])
    _search_ok()
    _config(["duckduckgo", "brave"])
    checks = {c.name: c for c in await run_checks(settings)}
    assert checks["engines"].ok is False
    assert "mojeek" in checks["engines"].detail


@respx.mock
async def test_the_placeholder_survey_url_fails(settings):
    respx.post(URL).mock(side_effect=[probe_ok(), probe_truncated()])
    _search_ok()
    _config(["duckduckgo", "mojeek", "brave"])
    checks = {c.name: c for c in await run_checks(settings)}
    assert checks["profile"].ok is False
    assert "placeholder" in checks["profile"].detail


def test_every_prompt_a_run_uses_is_loaded():
    from company_reach.tools import doctor
    assert set(doctor._PROMPTS) >= {"criteria", "score", "pick_site", "pick_pages", "extract", "draft"}
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_doctor.py -v`
Expected: there is no `engines` check, the profile check is ok, and `_PROMPTS` has 3 entries.

- [ ] **Step 3: Implement**

```python
_PROMPTS = ("criteria", "score", "doctor", "pick_site", "pick_pages", "extract", "draft")


async def _search_check(settings: Settings) -> Check:
    try:
        results = await search(PROBE_QUERY, settings=settings, limit=3)
    except SearchError as e:
        return Check("search", False, str(e)[:200])
    if not results:
        return Check("search", False, "the probe query returned nothing; engines may be suspended")
    engines = sorted({r.engine for r in results if r.engine}) or ["unknown"]
    return Check("search", True, f"{len(results)} results from {', '.join(engines)}")


async def _engines_check(settings: Settings) -> Check:
    """The engines the no-website guard relies on must exist and be enabled."""
    url = f"{settings.searxng_url.rstrip('/')}/config"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            config = (await client.get(url)).json()
    except (httpx.HTTPError, ValueError) as e:
        return Check("engines", False, f"cannot read {url}: {e}")
    enabled = {e["name"] for e in config.get("engines", []) if e.get("enabled")}
    wanted = [n.strip() for n in settings.baseline_engines.split(",") if n.strip()]
    missing = [n for n in wanted if n not in enabled]
    if missing:
        return Check("engines", False, f"baseline engines not enabled: {', '.join(missing)}")
    return Check("engines", True, ", ".join(wanted))
```

In `_profile_check`, after the empty check:

```python
    host = urlsplit(profile.survey_url).hostname or ""
    if host == "example" or host.endswith(".example"):
        return Check("profile", False,
                     f"survey_url is a placeholder ({host}); drafts would be unsendable")
```

In `_settings_check`, add `sending_approved={settings.sending_approved}` and `paid_fallback={'serper' if settings.serper_api_key else 'none'}`. Phase B changes the second value to `brave`. Add both new checks to `run_checks`, and move `_search_ok`/`_config` into the existing `test_all_checks_pass` together with a real-looking fictional `survey_url` (`https://umfrage.beispiel-hochschule.ch/kmu`).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_doctor.py tests/test_cli.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/company_reach/tools/doctor.py tests/test_doctor.py
git commit -m "fix: doctor checks search, engines and every prompt"
```

### Task A6: a run shows each company as it finishes

The audit found that `run` is silent for 2 to 25 minutes and prints the run id only at the end. The design already named the mechanism: LangGraph's `stream_mode="updates"`.

**Files:**
- Modify: `src/company_reach/graph.py` (`run_graph`)
- Modify: `src/company_reach/cli.py` (`run`, new `_echo_result`)
- Test: `tests/test_graph.py` (update `test_run_graph_passes_the_recursion_limit` for `astream`)

**Interfaces:**
- Produces: `run_graph(state, *, settings, child, dry=False, on_result: Callable[[CompanyResult], None] | None = None) -> ReachState`

- [ ] **Step 1: Write the failing test and update the spy**

```python
# tests/test_graph.py — add
async def test_each_company_is_reported_as_it_finishes(settings):
    _seed(settings, {"CHE000000001": 9, "CHE000000002": 7})
    child = ChildByUid({"CHE000000001": "skip", "CHE000000002": "send"})
    reported: list = []
    out = await run_graph(
        _start(settings), settings=settings, dry=True, child=child, on_result=reported.append
    )
    assert sorted(r.uid for r in reported) == ["CHE000000001", "CHE000000002"]
    assert len(out["results"]) == 2
```

Replace the `Spy` in `test_run_graph_passes_the_recursion_limit` with:

```python
    class Spy:
        async def astream(self, state, config=None, stream_mode=None):
            captured.update(config or {})
            yield ("values", state)
```

- [ ] **Step 2: Run it and see it fail**

Run: `uv run pytest tests/test_graph.py -k "reported_as_it_finishes or recursion_limit" -v`
Expected: `run_graph` does not accept `on_result`, and the spy has no `ainvoke`.

- [ ] **Step 3: Implement**

```python
async def run_graph(state, *, settings, child, dry=False, on_result=None) -> ReachState:
    """Streams rather than waiting for the end: "values" carries the state
    after each superstep (the last one is what ainvoke returned), and
    "updates" carries each finished company as it lands."""
    graph = build_graph(settings=settings, child=child, dry=dry)
    final = state
    async for mode, chunk in graph.astream(
        state, config={"recursion_limit": RECURSION_LIMIT}, stream_mode=["updates", "values"]
    ):
        if mode == "values":
            final = chunk
        elif on_result is not None:
            for node, update in chunk.items():
                if node == "enrich_company" and update:
                    for result in update.get("results", []):
                        on_result(result)
    return final
```

In `cli.py`, print the run id and the resume hint before the graph starts. Then pass the reporter:

```python
def _echo_result(result: CompanyResult) -> None:
    outcome = (
        f"{result.error_kind} error: {result.error_text}"
        if result.error_kind
        else f"{result.recommendation}: {result.reason}"
    )
    typer.echo(f"  {result.uid}  {outcome}")

    typer.echo(f"run {rid} — if it stops, run again with --run-id {rid}")
    out = asyncio.run(run_graph(state, settings=s, child=child, dry=dry, on_result=_echo_result))
```

- [ ] **Step 4: Run the suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/company_reach/graph.py src/company_reach/cli.py tests/test_graph.py
git commit -m "feat: run prints each company as it finishes"
```

**Phase A acceptance:**
- `uv run pytest -q` is green.
- `uv run company-reach doctor` with the current `.env` shows `paid_fallback=none` and fails `engines` (mojeek and startpage) and `profile` if the URL is still a placeholder.
- A `run` against a stopped SearXNG prints the cause and exits 1.

---

## Phase B — trustworthy search, with Brave

Branch `fix/audit-b-search`. Needs open points 1 and 2.

| Task | What | The failing test first | Files |
|---|---|---|---|
| B1 | `brave_search_api_key: SecretStr \| None` (added to `_blank_is_none`); remove `serper_api_key` and `_serper`; `.env.example` documents the Brave key | `test_settings::test_the_brave_key_is_read` and `test_an_empty_brave_key_is_no_key` | `settings.py`, `.env.example`, `PRIVACY.md` (Serper → Brave) |
| B2 | `tools/search.py::_brave`: GET `https://api.search.brave.com/res/v1/web/search`, header `X-Subscription-Token`, params `q`, `count`, `country=CH`, `search_lang=de`. 200 maps to `Result(engine="brave")`. 401/403 raise `SearchError("Brave rejected the key")`. 402/429/5xx/timeout raise `SearchError`, never `[]` (the earlier project's lesson) | `test_search::test_brave_results_map_to_result`, `test_brave_429_is_an_error_not_empty`, `test_brave_401_names_the_key`, `test_brave_is_never_asked_without_a_key` (respx) | `tools/search.py`, `tests/test_search.py` |
| B3 | Policy (open point 1): `search()` asks Brave on a SearXNG error. `find_site.search_results` asks Brave once more before any "no website" verdict, and in place of the 30-second pause when SearXNG was silent. "No website" needs a provider that answered and found no candidate | `test_find_site::test_no_site_needs_brave_to_agree`, `test_silence_asks_brave_instead_of_waiting`, `test_both_down_is_a_search_error` (review focus 2) | `tools/search.py`, `nodes/find_site.py` |
| B4 | The baseline engines exist: `searxng/settings.yml` sets `inactive: false` for mojeek and startpage (upstream marks them inactive). Pacing: `search_concurrency=1`, `search_gap_s=2.0` for SearXNG | `test_search::test_settings_yml_activates_the_baseline_engines` (parses the YAML); A5's `engines` check against the live container | `searxng/settings.yml`, `settings.py` |
| B5 | A candidate that answered 403 or could not be reached is an error, not an absence. If every candidate failed with an HTTP or transport error, raise `FetchError` (retryable) | `test_find_site::test_every_candidate_refused_is_an_error` (two candidates, both 403) | `nodes/find_site.py`, `tools/candidate_pages.py` |
| B6 | One `searches` row per query: run, uid, query, provider, result count, unresponsive engines, error, and top URLs **for SearXNG only** (Brave terms, open point 2). `retry --no-site <run>` re-enriches the "no website" skips of a run. The review card lists the queries with provider and outcome (#20) | `test_db::test_record_search_keeps_no_brave_urls`, `test_find_site::test_every_query_is_logged`, `test_cli::test_retry_no_site_redoes_the_skips`, `test_review_cards::test_a_no_site_card_shows_the_search_log` | `schema.sql`, `tools/db.py`, `tools/search.py`, `nodes/find_site.py`, `graph.py`, `cli.py`, `review/cards.py`, `review.html` |
| B7 | SSRF on every redirect hop: `follow_redirects=False`, then a loop of at most 5 hops, each through `_refuse`; robots.txt goes through the same path. `Page.final_url` records where the site landed, and the candidate is the final root (a redirect to another domain keeps its Impressum) | `test_fetcher::test_a_redirect_to_a_private_address_is_refused`, `test_a_redirect_to_another_domain_is_followed_and_recorded` | `tools/fetcher.py`, `nodes/find_site.py` |

**Acceptance:**
- An isolated run of about 30 companies with SearXNG throttled (block the container's network halfway) records no "no website" without a Brave answer behind it.
- `searches` explains every skip.
- Brave usage is printed per run: the number of queries.

## Phase C — the invitation (frame@1) and `redraft`

Branch `fix/audit-c-invitation`. Needs open point 3. Koray fills `[sender]` and `[invitation]` in `profile.toml`. Every sentence whose fact is not confirmed stays off through a boolean: results offer, no login, closing date, supervisor. The design, the exact German text and the test list are in `data/audit-2026-09-24/invitation/final_frame.md` and `code_changes.md`. A reference implementation is in `evidence/synthesis/frame.py`.

| Task | What | The failing test first |
|---|---|---|
| C1 | `profile.toml` `[sender]` (name, affiliation, school_short, place, supervisor) and `[invitation]` (topic, minutes, closes, offer_results, no_login, reminder, experiment). Scoring still runs without them | `test_profile::test_sender_and_invitation_sections_load`, `test_a_profile_without_sender_still_loads` |
| C2 | `tools/invitation.py`: `split_name`, `salutation` (Frau/Herr only from a stated source; a masculine role on a website never sets Herr), `greeting`, `routing_line`, `subject`, `opening`, `link_block`, `privacy(contact, inv)` with the seven rows, `closing`. Remove `privacy_sentence` | table-driven `test_invitation::test_split_name`, `test_privacy_text_per_kind` (never "Ihren Namen" when nobody is named; never SHAB as an address source), `test_a_masculine_site_role_never_sets_herr` |
| C3 | `DraftAnswer` becomes one field, `sentence`. `draft.md` version 4 (the model writes one sentence beginning "Ich schreibe Ihnen, weil", at most 20 words). `draft.py` assembles the mail. `role` enters the prompt inside a data block (K4) | `test_models::test_draft_answer_asks_only_for_the_sentence`, `test_draft::test_the_role_is_delimited_as_data`, `test_the_mail_is_assembled_in_frame_order` |
| C4 | `check_draft`: checks the subject; the sentence rule; placeholders (`[`, `<`, `XY`, `...`); ß; an English-sentence heuristic; the new head (routing plus greeting) and privacy text; defanged links and unlisted TLDs. A draft that raises on the second attempt deletes the first. A draft that failed is never sendable (the card gates Send on the latest check, not on a row existing) | `test_check_draft::test_a_placeholder_is_a_problem` (the "[Hochschule]" case), `test_a_subject_with_a_link_is_a_problem`, `test_a_raising_redraft_leaves_no_draft`, `test_review_cards::test_a_failed_draft_is_not_sendable` |
| C5 | The review card offers a Frau / Herr / none toggle when the salutation is unknown ("Anrede prüfen"). It re-assembles the mail without a model call. `third_party` address rows are disabled. The kind hint applies to the selected row | `test_review_app::test_the_salutation_toggle_rebuilds_the_mail`, `test_a_third_party_row_cannot_be_sent` |
| C6 | `company-reach redraft <run> [--uid]`: for undecided send cards whose link does not match `survey_url`, rerun only `draft` and `check_draft` from SQLite. No search, no fetch | `test_cli::test_redraft_makes_stale_cards_sendable` (review focus 3) |
| C7 | The A/B arm: `sha256(uid)` parity picks `voll` or `kurz`. `kurz` has no results line and no UID line; the survey's first page carries both. `Draft.frame_version` and `Draft.arm` are copied to the ledger's `sent` row | `test_invitation::test_the_arm_is_stable_for_a_uid`, `test_ledger::test_sent_records_frame_and_arm` |

**Acceptance:**
- The five fictional example mails from the study pass the new `check_draft` byte for byte.
- `redraft` restores every placeholder card of a run.
- Koray reads ten real drafts on the page before the pilot.

## Phase D — deletion and ledger integrity

Branch `fix/audit-d-ledger`. Needs open point 4.

| Task | What | The failing test first |
|---|---|---|
| D1 | `forget <e-mail>` matches contacts, profile persons, ledger addresses and suppression, with an exact JSON match instead of `LIKE` and normalised case and IDN. When nothing matches, it says how to find the company by the `c=` UID in the quoted link and exits 2 | `test_forget::test_forget_by_an_address_only_in_a_profile`, `test_forget_after_purge_still_suppresses`, `test_an_unknown_address_explains_and_exits_2` (review focus 4) |
| D2 | `retry` and `enrich` skip suppressed UIDs, companies marked never, and companies already sent | `test_graph::test_retry_skips_a_forgotten_company` (audit H7), `test_cli::test_enrich_refuses_a_suppressed_uid` |
| D3 | Address-level checks on the card and in `send()`: an address that is suppressed, or already sent for another company, is marked and refused | `test_review_app::test_an_address_already_mailed_is_refused`, `test_a_suppressed_address_is_refused` |
| D4 | Send carries `draft_id` and a hash of the body. A mismatch with the current draft refuses the send with "the draft changed; reload". The `sent` row snapshots subject, body hash, address kind, source, prompt and frame version, so a later redraft, retry or purge cannot repoint it | `test_review_app::test_a_changed_draft_is_not_sent`, `test_ledger::test_a_sent_row_survives_a_redraft` |
| D5 | Open point 4: `not_sent` (an undo on the recorded page, same session) and `bounced` (from the card). Both reopen the company to another address; `sent` stays final otherwise | `test_review_app::test_not_sent_reopens_the_card`, `test_bounced_allows_another_address` |
| D6 | `purge` covers companies never in `seen` (enrich, evals: K8). `forget` lists `.db` and `.log` copies under `data/` (backups included) | `test_purge::test_an_enriched_uid_is_purged`, `test_forget::test_a_backup_database_is_reported` |

**Acceptance:**
- The day-one walkthrough of the audit (§2 of the report) passes by hand on a fictional run: a reply "bitte löschen" from another address, a bounce, a sister company sharing an inbox.

## Phase E — contact and reading quality (after the pilot)

Needs open point 5 (`tldextract`).

| Task | What | The failing test first |
|---|---|---|
| E1 | SHAB rows merged per person, newest notice decides, departures applied (K2) | `test_find_contact::test_a_person_struck_out_later_is_not_chosen` (the probe from the audit) |
| E2 | A person must not equal the company name, its short name or a single word | `test_checks::test_the_company_name_is_not_a_person` |
| E3 | Charset: decode from the HTTP header, else the HTML meta charset, else `charset_normalizer` (already in the lock) | `test_fetcher::test_a_latin1_page_without_header_keeps_its_umlauts` |
| E4 | Keep the Impressum footer: when trafilatura's text lacks the footer or address blocks, append the `html2txt` of the footer | `test_textify::test_the_footer_survives_a_long_main_text` |
| E5 | `pick_pages` floor: the home page plus any Impressum, Kontakt and Über-uns URLs found by `candidate_pages` patterns are always read; the model chooses the rest | `test_pick_pages::test_the_impressum_is_always_read` |
| E6 | `registered_domain` through `tldextract`: subdomains and site-builder hosts; the inbox on the apex domain is on-site | `test_urls::test_a_subdomain_site_keeps_its_apex_inbox` |
| E7 | Data-block markers carry a random nonce per prompt, so five angle brackets cannot rebuild one | `test_untrusted::test_a_page_cannot_forge_the_end_marker` |

## Phase F — scoring integrity and throughput (after the pilot)

| Task | What | The failing test first |
|---|---|---|
| F1 | Criteria are written once per goal and stored (a `criteria` table keyed by goal hash). `score` reuses them, and `--new-criteria` rewrites them. The criteria hash joins the score key (H10) | `test_score_pool::test_two_score_passes_use_one_criteria`, `test_a_new_criteria_rescores` |
| F2 | The guard in `run` uses the same predicate as `draw_batch` and says why nothing is drawable (prompt version, threshold, model) | `test_cli::test_a_prompt_bump_says_rescore` |
| F3 | `screen_reason` is computed in `upsert_companies` at import; `screen` re-applies the rules to all rows and fails on zero rows | `test_pool_nodes::test_an_import_is_screened` |
| F4 | `run --target N`: draw until N sendable, the pool is exhausted, or a batch cap is reached. `RECURSION_LIMIT` is derived from the cap | `test_graph::test_the_loop_runs_until_the_target` |
| F5 | Several municipalities in one pool; `company-reach status` shows pooled, scored, drawable, drawn, sent and undecided per municipality | `test_cli::test_status_counts_per_municipality` |
| F6 | `run --dry` works on a temporary copy of the database, so a demonstration never marks real companies as seen (found in Phase A's final review) | `test_cli::test_a_dry_run_leaves_the_real_database_untouched` |

## Phase G — evaluation and operations (after the pilot)

Needs open point 6.

| Task | What | The failing test first |
|---|---|---|
| G0 | Phase A review minors: `about_me` into `start_manifest`; `run --goal` without a profile exits cleanly; `_engines_check` parses inside its `try` and checks the status; tests pin 429/5xx retries; doctor fixtures clear shell variables; one shared `is_placeholder_url` | one test each |
| G1 | Evaluations write to a temporary database, never `data/company_reach.db` | `test_prompts` fixture asserts the path |
| G2 | Scoring evaluation: three trials, spread reported, the production decision (score ≥ 7 against human ≥ 6: precision, recall) asserted with a margin; results appended to `data/evals/results.jsonl` | the evaluation itself (opt-in) |
| G3 | A contact-choice and recommend evaluation on fictional fixtures (no model) | `test_find_contact` table over the golden subset |
| G4 | `langsmith_tracing` enforced in code with `tracing_context(enabled=...)` | `test_llm::test_tracing_stays_off_when_the_env_says_on` |
| G5 | Open point 6: remove the Docker `app` service (or run the CLI in it too); non-root image if it stays | — |
| G6 | CI on the current action majors, a `pip-audit` step, a job timeout. Dependencies: `fastapi[standard-no-fastapi-cloud-cli]`, `httpx<1`, `lxml>=6.1.3`, bumps from the audit §8 | CI itself |
| G7 | Remove dead code: `PLAYWRIGHT_URL`, the unused prompts (`translate`, `translate_tr`, `german_probe`), unfinished `runs` rows (finish them or drop the table); record `needs_js` per company | `test_manifest::test_no_dead_prompt_is_listed` |

## Phase H — repository hygiene (Koray)

Open point 7. The steps and the reasons are kept in the local audit notes (`data/audit-2026-09-24/`), not in this public file. Nothing is done without Koray's explicit go.

## Backlog: low findings not in a phase

The rest of the 59 verified low findings are listed with their fixes in `consolidated.json`. The ones worth doing when their file is next touched:
- UTC month in "sent this month";
- run-id validation;
- a letter clipped on small laptop screens;
- the `c/o` street tier;
- the page-UID regex boundary;
- the LinkedIn lead matching on one word;
- the blocklist additions;
- the cache has no age limit and no `--refetch`;
- the harvested-link noise (feeds, `wp-json`, PDFs);
- the `purpose_head` cap of 300;
- the FR/IT liquidation variants.

## Not in this plan

- **Company size** (decided 2026-09-25).
- **French and Italian invitations.** The tool stays German-speaking Switzerland.
- **A reminder mail.** It waits for the ethics answer. Phase C's `reminder` flag and D's ledger fields make it a small change later.
