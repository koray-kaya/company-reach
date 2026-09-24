from pathlib import Path

import pytest

from company_reach.models import CompanyRecord, Score
from company_reach.tools.db import (
    connect,
    draw_batch,
    init_db,
    record_decision,
    record_seen,
    suppress,
    upsert_companies,
    upsert_scores,
)

KEY = dict(goal_hash="g1", prompt_version="1", model="m1")


def rec(uid: str) -> CompanyRecord:
    return CompanyRecord(
        uid=uid,
        name=f"Firma {uid[-2:]} AG",
        legal_form="0106",
        municipality="3203",
        purpose="Betrieb einer Schreinerei.",
        purpose_head="Betrieb einer Schreinerei.",
    )


def uid(n: int) -> str:
    return f"CHE{n:09d}"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """Five companies, scores 9, 7, 5, 3, 1 — one either side of the default
    threshold of five, and one exactly on it."""
    path = tmp_path / "t.db"
    init_db(path)
    with connect(path) as conn:
        upsert_companies(conn, [rec(uid(n)) for n in range(1, 6)], "import")
        upsert_scores(
            conn,
            [
                Score(uid=uid(1), score=9, reason="x"),
                Score(uid=uid(2), score=7, reason="x"),
                Score(uid=uid(3), score=5, reason="x"),
                Score(uid=uid(4), score=3, reason="x"),
                Score(uid=uid(5), score=1, reason="x"),
            ],
            **KEY,
        )
    return path


def draw(conn, run_id="r1", batch_no=1, min_score=5, limit=10) -> list[str]:
    return draw_batch(
        conn,
        run_id=run_id,
        batch_no=batch_no,
        min_score=min_score,
        limit=limit,
        **KEY,
    )


def fail(conn, company_uid: str, run_id: str) -> None:
    conn.execute(
        "insert into results (run_id, uid, error_kind, error_text, finished_at) "
        "values (?,?,'fetch','boom','2026-09-20T00:00:00+00:00')",
        (run_id, company_uid),
    )


def finish(conn, company_uid: str, run_id: str) -> None:
    conn.execute(
        "insert into results (run_id, uid, recommendation, finished_at) "
        "values (?,?,'send','2026-09-20T00:00:00+00:00')",
        (run_id, company_uid),
    )


def test_drawn_in_descending_score(db: Path):
    with connect(db) as conn:
        assert draw(conn) == [uid(1), uid(2), uid(3)]


def test_below_the_threshold_is_never_drawn(db: Path):
    """Scores 3 and 1 stay out; 5 is on the bar and comes in."""
    with connect(db) as conn:
        drawn = draw(conn)
        assert uid(3) in drawn
        assert uid(4) not in drawn and uid(5) not in drawn


def test_limit_takes_the_top_of_the_order(db: Path):
    with connect(db) as conn:
        assert draw(conn, limit=2) == [uid(1), uid(2)]


def test_a_seen_company_is_not_redrawn(db: Path):
    with connect(db) as conn:
        record_seen(conn, [uid(1)], run_id="r1", batch_no=1)
        assert draw(conn, batch_no=2) == [uid(2), uid(3)]


def test_a_screened_out_company_is_never_drawn(db: Path):
    with connect(db) as conn:
        conn.execute(
            "update companies set screen_reason = 'in Liquidation' where uid = ?",
            (uid(1),),
        )
        assert draw(conn) == [uid(2), uid(3)]


def test_an_error_from_another_run_puts_a_company_back(db: Path):
    """A company that errored was never contacted and has no draft, so a
    later run may try it again."""
    with connect(db) as conn:
        record_seen(conn, [uid(1)], run_id="r1", batch_no=1)
        fail(conn, uid(1), run_id="r1")
        assert uid(1) in draw(conn, run_id="r2")


def test_an_error_in_this_run_does_not(db: Path):
    """Redrawing inside the same run would loop on the same failure."""
    with connect(db) as conn:
        record_seen(conn, [uid(1)], run_id="r1", batch_no=1)
        fail(conn, uid(1), run_id="r1")
        assert uid(1) not in draw(conn, run_id="r1", batch_no=2)


def test_a_finished_company_stays_out_even_across_runs(db: Path):
    with connect(db) as conn:
        record_seen(conn, [uid(1)], run_id="r1", batch_no=1)
        finish(conn, uid(1), run_id="r1")
        assert uid(1) not in draw(conn, run_id="r2")


def test_a_rerun_returns_the_recorded_batch(db: Path):
    """Same run_id, same batch_no: repeat what was drawn, do not draw more."""
    with connect(db) as conn:
        record_seen(conn, [uid(2), uid(3)], run_id="r1", batch_no=1)
        assert draw(conn, run_id="r1", batch_no=1) == [uid(2), uid(3)]


def test_nothing_left_returns_empty(db: Path):
    """The caller reads this as pool_exhausted."""
    with connect(db) as conn:
        record_seen(conn, [uid(1), uid(2), uid(3)], run_id="r1", batch_no=1)
        assert draw(conn, run_id="r1", batch_no=2) == []


def test_a_recovered_company_is_not_drawn_twice_by_the_same_run(db: Path):
    """The trap: r1 fails on a company, r2 recovers it. Its results row from
    r1 still carries an error, so without `seen.run_id` being updated on the
    redraw, r2's next batch would draw it again, and again."""
    with connect(db) as conn:
        record_seen(conn, [uid(1)], run_id="r1", batch_no=1)
        fail(conn, uid(1), run_id="r1")

        first = draw(conn, run_id="r2", batch_no=1)
        assert uid(1) in first
        record_seen(conn, first, run_id="r2", batch_no=1)

        assert uid(1) not in draw(conn, run_id="r2", batch_no=2)


def test_a_company_finished_after_an_error_is_never_drawn_again(db: Path):
    """r1 fails on a company and r2 finishes it. r1's error row is still
    there, so a rule that only asks "has it ever errored?" would put it back
    in r3 — and a company finished in r2 may already have been written to."""
    with connect(db) as conn:
        record_seen(conn, [uid(1)], run_id="r1", batch_no=1)
        fail(conn, uid(1), run_id="r1")
        record_seen(conn, [uid(1)], run_id="r2", batch_no=1)
        finish(conn, uid(1), run_id="r2")
        assert uid(1) not in draw(conn, run_id="r3")


def test_a_company_in_the_ledger_is_never_drawn(db: Path):
    """Any decision at all — a v0 send, a skip — keeps a company out."""
    with connect(db) as conn:
        record_decision(conn, uid(1), "sent", note="v0")
        record_decision(conn, uid(2), "skipped")
        assert draw(conn, run_id="r9") == [uid(3)]


def test_a_suppressed_company_is_never_drawn(db: Path):
    with connect(db) as conn:
        suppress(conn, uid(1), reason="never again")
        assert uid(1) not in draw(conn, run_id="r9")
