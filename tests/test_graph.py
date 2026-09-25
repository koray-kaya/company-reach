import operator
from typing import get_type_hints

import pytest
from langgraph.graph import END

from company_reach import graph as graph_module
from company_reach.errors import LlmError, SearchError
from company_reach.graph import (
    RECURSION_LIMIT,
    ReachState,
    build_graph,
    build_stub_child,
    collect,
    initial_state,
    need_another_batch,
    retry_errors,
    run_graph,
)
from company_reach.models import CompanyRecord, CompanyResult, Score
from company_reach.profile import goal_hash
from company_reach.tools import llm
from company_reach.tools.db import (
    connect,
    errored_uids,
    init_db,
    upsert_companies,
    upsert_scores,
)


def test_results_carries_an_add_reducer():
    """Several enrich_company nodes write `results` in the same superstep.
    Without a reducer LangGraph treats concurrent writes to one key as a
    conflict; operator.add concatenates the lists instead."""
    hints = get_type_hints(ReachState, include_extras=True)
    assert hints["results"].__metadata__ == (operator.add,)


def test_results_holds_company_results():
    hints = get_type_hints(ReachState, include_extras=True)
    annotated_type = hints["results"].__origin__
    assert annotated_type == list[CompanyResult]


def test_state_carries_the_loop_controls():
    hints = get_type_hints(ReachState, include_extras=True)
    for key in ("batches_drawn", "pool_exhausted", "sendable_count", "batch_uids"):
        assert key in hints


# --- collect and need_another_batch -----------------------------------------


def _row(conn, uid: str, run_id: str, recommendation: str | None, error=None) -> None:
    conn.execute(
        "insert into results (run_id, uid, recommendation, error_kind, finished_at) "
        "values (?,?,?,?,'2026-09-20T00:00:00+00:00')",
        (run_id, uid, recommendation, error),
    )


def _state(**over) -> dict:
    base = dict(
        run_id="r1",
        goal="g",
        about_me="a",
        municipality="3203",
        batch_size=10,
        seed=0,
        pool_count=0,
        kept_count=0,
        criteria=None,
        batch_uids=[],
        batches_drawn=1,
        pool_exhausted=False,
        results=[],
        sendable_count=0,
    )
    return base | over


def test_collect_counts_from_the_table_not_from_state(settings):
    """The audit's third P0: a resumed run has an empty `results` list in
    state and a full table. Counting state would draw a fresh batch."""
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        _row(conn, "CHE000000001", "r1", "send")
        _row(conn, "CHE000000002", "r1", "send")

    out = collect(_state(results=[]), settings=settings)
    assert out["sendable_count"] == 2


def test_collect_ignores_other_runs(settings):
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        _row(conn, "CHE000000001", "r1", "send")
        _row(conn, "CHE000000002", "r2", "send")

    assert collect(_state(), settings=settings)["sendable_count"] == 1


def test_only_send_is_sendable(settings):
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        _row(conn, "CHE000000001", "r1", "skip")
        _row(conn, "CHE000000002", "r1", "hold")
        _row(conn, "CHE000000003", "r1", None, error="fetch")

    assert collect(_state(), settings=settings)["sendable_count"] == 0


def test_another_batch_when_nothing_sendable_and_room_left(settings):
    state = _state(sendable_count=0, batches_drawn=1, pool_exhausted=False)
    assert need_another_batch(state, settings=settings) == "draw_batch"


def test_a_sendable_candidate_ends_the_run(settings):
    state = _state(sendable_count=1, batches_drawn=1, pool_exhausted=False)
    assert need_another_batch(state, settings=settings) == END


def test_an_exhausted_pool_ends_the_run_even_on_the_first_batch(settings):
    state = _state(sendable_count=0, batches_drawn=0, pool_exhausted=True)
    assert need_another_batch(state, settings=settings) == END


def test_the_batch_limit_ends_the_run(settings):
    """max_batches_per_run is 3; the third batch is the last."""
    state = _state(sendable_count=0, batches_drawn=3, pool_exhausted=False)
    assert need_another_batch(state, settings=settings) == END


# --- the whole loop ----------------------------------------------------------


def _seed(settings, scores: dict[str, int]) -> None:
    """Companies with the given scores, under the key draw_batch will use."""
    init_db(settings.db_path)
    version, _ = llm.load_prompt("score")
    with connect(settings.db_path) as conn:
        upsert_companies(
            conn,
            [
                CompanyRecord(
                    uid=uid,
                    name=f"Firma {uid[-2:]} AG",
                    legal_form="0106",
                    municipality="3203",
                    purpose="Betrieb einer Schreinerei.",
                    purpose_head="Betrieb einer Schreinerei.",
                )
                for uid in scores
            ],
            "import",
        )
        upsert_scores(
            conn,
            [Score(uid=uid, score=n, reason="x") for uid, n in scores.items()],
            goal_hash=goal_hash("g"),
            prompt_version=version,
            model=settings.llm_model,
        )


class ChildByUid:
    """Behaves differently per company: send, skip, or raise."""

    def __init__(self, behaviour: dict[str, str]):
        self.behaviour = behaviour
        self.seen: list[str] = []

    async def ainvoke(self, state, config=None):
        uid = state["uid"]
        self.seen.append(uid)
        what = self.behaviour[uid]
        if what == "raise":
            raise LlmError("the model said no")
        return {"recommendation": what, "reason": "because"}


def _start(settings, **over) -> dict:
    return (
        initial_state(
            run_id="r1", goal="g", about_me="a", municipality="3203", settings=settings
        )
        | over
    )


async def test_one_raising_child_does_not_lose_the_other(settings):
    """Issue #3's demo. Two companies, one child raises: two results, one
    sendable. Without the wrapper the raising task would cancel its sibling."""
    _seed(settings, {"CHE000000001": 9, "CHE000000002": 7})
    child = ChildByUid({"CHE000000001": "raise", "CHE000000002": "send"})

    out = await run_graph(_start(settings), settings=settings, dry=True, child=child)

    assert len(out["results"]) == 2
    assert out["sendable_count"] == 1
    kinds = {r.uid: r.error_kind for r in out["results"]}
    assert kinds["CHE000000001"] == "llm"
    assert kinds["CHE000000002"] is None


async def test_an_exhausted_pool_ends_without_a_child_call(settings):
    """Nothing clears the threshold: the empty batch routes straight to
    collect, and no Send is issued."""
    _seed(settings, {"CHE000000001": 2})
    child = ChildByUid({})

    out = await run_graph(_start(settings), settings=settings, dry=True, child=child)

    assert child.seen == []
    assert out["pool_exhausted"] is True
    assert out["sendable_count"] == 0


async def test_the_loop_draws_again_when_nothing_is_sendable(settings):
    """Three companies, batch_size 1, all skipped: the loop keeps drawing
    until the batch budget of three is spent."""
    _seed(settings, {"CHE000000001": 9, "CHE000000002": 8, "CHE000000003": 7})
    child = ChildByUid(dict.fromkeys([f"CHE00000000{n}" for n in (1, 2, 3)], "skip"))

    out = await run_graph(
        _start(settings, batch_size=1), settings=settings, dry=True, child=child
    )

    assert len(child.seen) == 3
    assert out["batches_drawn"] == 3
    assert out["sendable_count"] == 0


async def test_a_rerun_draws_no_new_companies(settings):
    """Same run_id: the recorded batches come back, the finished children are
    skipped, and `seen` does not grow."""
    _seed(settings, {"CHE000000001": 9, "CHE000000002": 7})
    first = ChildByUid({"CHE000000001": "skip", "CHE000000002": "skip"})
    await run_graph(_start(settings), settings=settings, dry=True, child=first)

    with connect(settings.db_path) as conn:
        after_first = conn.execute("select count(*) from seen").fetchone()[0]

    second = ChildByUid({})  # a call would raise KeyError
    out = await run_graph(_start(settings), settings=settings, dry=True, child=second)

    assert second.seen == []
    with connect(settings.db_path) as conn:
        assert conn.execute("select count(*) from seen").fetchone()[0] == after_first
    assert len(out["results"]) == 2  # the stored ones, so the count stays whole


def test_the_recursion_limit_is_set_explicitly():
    """LangGraph's default is 1000, so an unset limit is not a defence."""
    assert RECURSION_LIMIT == 40


async def test_run_graph_passes_the_recursion_limit(settings, monkeypatch):
    captured: dict = {}

    class Spy:
        async def ainvoke(self, state, config=None):
            captured.update(config or {})
            return state

    monkeypatch.setattr("company_reach.graph.build_graph", lambda **kw: Spy())
    await run_graph(_start(settings), settings=settings, dry=True, child=ChildByUid({}))
    assert captured["recursion_limit"] == RECURSION_LIMIT


async def test_the_stub_child_runs_through_the_real_graph(settings):
    """What `run --dry` will do in Task 7: a real compiled child graph, one
    node, recommending skip. Proves the wrapper's contract holds against
    LangGraph itself and not only against a test double."""
    _seed(settings, {"CHE000000001": 9})

    out = await run_graph(
        _start(settings), settings=settings, dry=True, child=build_stub_child()
    )

    assert len(out["results"]) == 1
    assert out["results"][0].recommendation == "skip"
    assert out["results"][0].error_kind is None
    assert out["sendable_count"] == 0


def test_the_dry_graph_leaves_the_search_probe_out(settings):
    """--dry never searches, so the pre-flight for search has nothing to
    protect — and wiring it in would put a network call inside the one
    command whose point is that it needs none."""
    dry = build_graph(settings=settings, child=build_stub_child(), dry=True)
    assert "probe_search" not in dry.get_graph().nodes


def test_a_real_run_keeps_the_search_probe(settings):
    real = build_graph(settings=settings, child=build_stub_child(), dry=False)
    assert "probe_search" in real.get_graph().nodes


# --- retry (the audit's P1, audit-2026-09-19.md:217) -------------------------


def _seen(settings) -> list[tuple]:
    with connect(settings.db_path) as conn:
        return [tuple(r) for r in conn.execute("select * from seen order by uid")]


async def test_search_failing_for_two_of_ten_is_recovered_by_retry(settings):
    """The acceptance, as written: with search failing for 2 of 10 children,
    those 2 get `error`, are not redrawn, and are re-enriched by `retry`;
    `seen` is unchanged throughout."""
    uids = [f"CHE00000001{n}" for n in range(10)]
    _seed(settings, dict.fromkeys(uids, 9))
    failing = set(uids[:2])

    class SearchDown(ChildByUid):
        async def ainvoke(self, state, config=None):
            self.seen.append(state["uid"])
            if state["uid"] in failing:
                raise SearchError("every baseline engine unresponsive")
            return {"recommendation": "send", "reason": "because"}

    first = SearchDown({})
    out = await run_graph(
        _start(settings, batch_size=10), settings=settings, dry=True, child=first
    )
    errored = {r.uid for r in out["results"] if r.error_kind == "search"}
    assert errored == failing
    assert out["batches_drawn"] == 1  # a candidate ended the run; nothing redrawn
    seen_before = _seen(settings)

    second = ChildByUid(dict.fromkeys(uids, "send"))
    results = await retry_errors("r1", settings=settings, child=second, dry=True)

    assert sorted(second.seen) == sorted(failing)  # exactly those, nobody else
    assert {r.uid for r in results} == failing
    assert all(r.error_kind is None for r in results)
    assert _seen(settings) == seen_before
    with connect(settings.db_path) as conn:
        left = conn.execute(
            "select count(*) from results where run_id='r1' and error_kind is not null"
        ).fetchone()[0]
    assert left == 0


async def test_retry_with_nothing_to_retry_calls_no_child(settings):
    _seed(settings, {"CHE000000001": 9})
    child = ChildByUid({})
    assert await retry_errors("r1", settings=settings, child=child, dry=True) == []
    assert child.seen == []


async def test_retry_probes_search_before_touching_a_company(settings, monkeypatch):
    """The same order as a run: if search is down, retrying would only turn
    the same errors into the same errors, or worse, into findings."""
    _seed(settings, {"CHE000000001": 9})
    with connect(settings.db_path) as conn:
        conn.execute(
            "insert into results (run_id, uid, error_kind, error_text, finished_at)"
            " values ('r1','CHE000000001','search','x','2026-09-24T00:00:00+00:00')"
        )

    async def down(state, *, settings):
        raise SearchError("probe returned nothing")

    monkeypatch.setattr(graph_module, "probe_search", down)
    child = ChildByUid({"CHE000000001": "send"})
    with pytest.raises(SearchError):
        await retry_errors("r1", settings=settings, child=child)
    assert child.seen == []


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
