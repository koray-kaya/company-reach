import operator
from typing import get_type_hints

from langgraph.graph import END

from company_reach.graph import ReachState, collect, need_another_batch
from company_reach.models import CompanyResult
from company_reach.tools.db import connect, init_db


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
