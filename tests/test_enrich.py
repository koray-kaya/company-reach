from pathlib import Path

import pytest

from company_reach.errors import FetchError, LlmError, SearchError, ShabError
from company_reach.models import CompanyResult
from company_reach.nodes.enrich_company import enrich_company
from company_reach.settings import Settings
from company_reach.tools.db import connect, init_db, record_result, result_for

UID = "CHE000000001"


class Child:
    """Stands in for the compiled child graph: anything with `ainvoke`."""

    def __init__(self, out: dict | None = None, raises: Exception | None = None):
        self.out = out or {"recommendation": "send", "reason": "fits"}
        self.raises = raises
        self.calls = 0

    async def ainvoke(self, state, config=None):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.out


@pytest.fixture
def db_settings(settings: Settings) -> Settings:
    init_db(settings.db_path)
    return settings


def state(uid: str = UID) -> dict:
    return {"run_id": "r1", "goal": "g", "about_me": "a", "uid": uid}


async def run(child: Child, settings: Settings, uid: str = UID) -> dict:
    return await enrich_company(state(uid), child=child, settings=settings)


def stored(settings: Settings, uid: str = UID) -> dict | None:
    with connect(settings.db_path) as conn:
        row = conn.execute(
            "select * from results where run_id = 'r1' and uid = ?", (uid,)
        ).fetchone()
    return dict(row) if row else None


async def test_returns_the_results_key_and_nothing_else(db_settings: Settings):
    """The parent owns `results`. A compiled subgraph used as a node would
    write nothing here, which is the audit's first P0 finding."""
    out = await run(Child(), db_settings)
    assert set(out) == {"results"}
    assert len(out["results"]) == 1
    assert out["results"][0].recommendation == "send"


async def test_the_row_lands_in_the_results_table(db_settings: Settings):
    await run(Child(), db_settings)
    assert stored(db_settings)["recommendation"] == "send"


async def test_an_unexpected_exception_does_not_escape(db_settings: Settings):
    """LangGraph cancels the sibling Send tasks when one raises. 'One failure
    does not stop the others' holds only because this never raises."""
    out = await run(Child(raises=ZeroDivisionError("boom")), db_settings)
    result = out["results"][0]
    assert result.error_kind == "other"
    assert "boom" in result.error_text
    assert result.recommendation is None


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (LlmError("no answer"), "llm"),
        (SearchError("no results"), "search"),
        (FetchError("404"), "fetch"),
        (ShabError("503"), "shab"),
    ],
)
async def test_typed_errors_keep_their_kind(
    db_settings: Settings, error: Exception, kind: str
):
    out = await run(Child(raises=error), db_settings)
    assert out["results"][0].error_kind == kind
    assert stored(db_settings)["error_kind"] == kind


async def test_a_long_error_is_truncated(db_settings: Settings):
    out = await run(Child(raises=LlmError("x" * 5000)), db_settings)
    assert len(out["results"][0].error_text) <= 500


async def test_a_finished_company_is_not_run_again(db_settings: Settings):
    """Resume: the child is skipped, but the stored result is still returned
    so the superstep's accounting stays whole."""
    first = Child()
    await run(first, db_settings)

    second = Child(out={"recommendation": "skip", "reason": "changed"})
    out = await run(second, db_settings)

    assert second.calls == 0
    assert out["results"][0].recommendation == "send"


async def test_an_errored_company_is_run_again(db_settings: Settings):
    await run(Child(raises=LlmError("boom")), db_settings)

    second = Child()
    out = await run(second, db_settings)

    assert second.calls == 1
    assert out["results"][0].recommendation == "send"
    assert stored(db_settings)["error_kind"] is None


async def test_the_child_receives_what_it_needs(db_settings: Settings):
    seen: dict = {}

    class Recorder(Child):
        async def ainvoke(self, state, config=None):
            seen.update(state)
            return await super().ainvoke(state, config)

    await run(Recorder(), db_settings)
    assert seen["uid"] == UID
    assert seen["run_id"] == "r1"
    assert seen["goal"] == "g"
    assert seen["about_me"] == "a"


def test_results_table_keeps_one_row_per_run_and_company(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    with connect(db) as conn:
        pk = conn.execute("select sql from sqlite_master where name='results'")
        assert "PRIMARY KEY (run_id, uid)" in pk.fetchone()[0]


async def test_the_previous_error_reaches_the_child(db_settings: Settings):
    """A retried company's child is told how it failed last time, so a
    refusal that repeats can be told from one that happened once."""
    with connect(db_settings.db_path) as conn:
        conn.execute(
            "insert into results (run_id, uid, error_kind, error_text, finished_at)"
            " values ('r1', ?, 'fetch', 'every candidate refused us (HTTP 403)', 't')",
            (UID,),
        )
    seen: dict = {}

    class Remembers(Child):
        async def ainvoke(self, state, config=None):
            seen.update(state)
            return await super().ainvoke(state, config)

    await run(Remembers(), db_settings)
    assert seen["previous_error"] == "every candidate refused us (HTTP 403)"


async def test_a_first_attempt_has_no_previous_error(db_settings: Settings):
    seen: dict = {}

    class Remembers(Child):
        async def ainvoke(self, state, config=None):
            seen.update(state)
            return await super().ainvoke(state, config)

    await run(Remembers(), db_settings)
    assert seen["previous_error"] is None


async def test_the_pages_that_needed_javascript_are_counted(db_settings: Settings):
    """Audit: read_pages recorded which pages were JavaScript shells, and
    the number was gone when the run ended — the one measurement that
    would say whether a browser fetcher is worth building."""
    shells = ["https://muster.ch/", "https://muster.ch/team"]
    child = Child({"recommendation": "hold", "reason": "r", "needs_js": shells})

    [result] = (await run(child, db_settings))["results"]

    assert result.needs_js == 2
    assert stored(db_settings)["needs_js"] == 2
    with connect(db_settings.db_path) as conn:
        assert result_for(conn, UID, run_id="r1").needs_js == 2


async def test_a_verdict_written_later_keeps_the_count(db_settings: Settings):
    """`redraft` rewrites a company's verdict without reading its pages
    again; it must not wipe what the run counted."""
    await run(
        Child({"recommendation": "send", "reason": "r", "needs_js": ["u"]}), db_settings
    )
    with connect(db_settings.db_path) as conn:
        record_result(
            conn, CompanyResult(uid=UID, recommendation="hold", reason="x"), run_id="r1"
        )
    assert stored(db_settings)["needs_js"] == 1
