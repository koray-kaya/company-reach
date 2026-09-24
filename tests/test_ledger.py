"""The ledger: every decision a reviewer made, as a log.

A company's state is its latest row; "contacted" is any `sent` row, ever.
Undoing a skip adds a row rather than deleting one, so the funnel the thesis
reports keeps its history (M7 open point 4).
"""

import sqlite3
from pathlib import Path

import pytest

from company_reach.tools.db import (
    connect,
    decision_for,
    init_db,
    is_suppressed,
    record_decision,
    sent_this_month,
    suppress,
    was_contacted,
)

UID = "CHE000000046"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    init_db(path)
    return path


def test_the_latest_decision_is_the_state(db: Path):
    with connect(db) as conn:
        record_decision(conn, UID, "skipped", note="Not a fit")
        assert decision_for(conn, UID)["status"] == "skipped"


def test_undoing_a_skip_adds_a_row_and_leaves_the_company_undecided(db: Path):
    with connect(db) as conn:
        record_decision(conn, UID, "skipped", note="Not a fit")
        record_decision(conn, UID, "undone")
        assert decision_for(conn, UID) is None
        rows = conn.execute("select status from ledger where uid = ?", (UID,))
        assert [r["status"] for r in rows] == ["skipped", "undone"]


def test_a_company_nobody_decided_has_no_state(db: Path):
    with connect(db) as conn:
        assert decision_for(conn, UID) is None
        assert was_contacted(conn, UID) is False


def test_contacted_means_any_sent_row_ever(db: Path):
    with connect(db) as conn:
        record_decision(conn, UID, "sent", address="info@muster.ch")
        record_decision(conn, UID, "undone")  # the page never offers this
        assert was_contacted(conn, UID) is True


def test_an_unknown_status_is_refused(db: Path):
    with connect(db) as conn, pytest.raises(ValueError, match="status"):
        record_decision(conn, UID, "maybe")


def test_this_months_count_counts_sent_rows_of_this_calendar_month(db: Path):
    with connect(db) as conn:
        record_decision(
            conn, "CHE000000001", "sent", decided_at="2026-09-01T08:00:00+00:00"
        )
        record_decision(
            conn, "CHE000000002", "sent", decided_at="2026-09-30T23:00:00+00:00"
        )
        record_decision(
            conn, "CHE000000003", "sent", decided_at="2026-08-31T23:00:00+00:00"
        )
        record_decision(
            conn, "CHE000000004", "skipped", decided_at="2026-09-10T08:00:00+00:00"
        )
        assert sent_this_month(conn, today="2026-09-24") == 2


def test_suppression_is_by_uid_or_address(db: Path):
    with connect(db) as conn:
        suppress(conn, UID, reason="never again")
        suppress(conn, "Anna.Muster@Muster.ch", reason="asked to be deleted")
        assert is_suppressed(conn, UID)
        assert is_suppressed(conn, "anna.muster@muster.ch")  # case folded
        assert not is_suppressed(conn, "info@muster.ch")


def test_an_older_ledger_with_one_row_per_company_is_replaced(tmp_path: Path):
    # the M1 shape; every database so far holds it empty
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE ledger (uid TEXT PRIMARY KEY, status TEXT NOT NULL,
             address TEXT, draft_id INTEGER, decided_at TEXT NOT NULL, note TEXT)"""
    )
    conn.close()

    init_db(path)
    with connect(path) as c:
        record_decision(c, UID, "skipped")
        record_decision(c, UID, "undone")  # two rows for one uid: a log now


def test_an_older_ledger_that_holds_decisions_is_not_dropped(tmp_path: Path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE ledger (uid TEXT PRIMARY KEY, status TEXT NOT NULL,
             address TEXT, draft_id INTEGER, decided_at TEXT NOT NULL, note TEXT)"""
    )
    conn.execute("insert into ledger values ('CHE000000046','sent',null,null,'x',null)")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="ledger"):
        init_db(path)
