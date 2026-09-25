"""The ledger: every decision a reviewer made, as a log.

A company's state is its latest row; "contacted" is any `sent` row, ever,
that no `not_sent` or `bounced` row took back. Undoing a skip adds a row
rather than deleting one, so the funnel the thesis reports keeps its history
(M7 open point 4).
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


def test_sent_records_frame_and_arm(db: Path):
    # what the survey's answers are compared by, joined on the UID
    with connect(db) as conn:
        record_decision(
            conn,
            UID,
            "sent",
            address="info@muster.ch",
            frame_version="frame@1",
            arm="kurz",
            contact_kind="generic/site/named",
        )
        row = conn.execute(
            "select frame_version, arm, contact_kind from ledger"
        ).fetchone()
    assert tuple(row) == ("frame@1", "kurz", "generic/site/named")


def test_a_sent_row_keeps_frame_and_kind_after_forget(settings):
    """`forget` deletes drafts and contacts; the ledger's frame, arm and
    contact kind survive it, with no personal data in them."""
    from review_seed import SEND, seed

    from company_reach.forget import forget

    seed(settings.db_path)
    with connect(settings.db_path) as conn:
        record_decision(
            conn,
            SEND,
            "sent",
            address="info@muster-metallbau.ch",
            frame_version="frame@1",
            arm="voll",
            contact_kind="generic/site/named",
        )
    forget(settings, SEND)
    with connect(settings.db_path) as conn:
        row = conn.execute(
            "select address, frame_version, arm, contact_kind from ledger"
        ).fetchone()
    assert tuple(row) == (None, "frame@1", "voll", "generic/site/named")


def test_a_sent_row_survives_a_redraft(settings):
    """The ledger is the record of what was sent (audit: a redraft made
    `draft_id` point to text never sent). A redraft replaces the company's
    draft; the sent row keeps the subject, the body's hash and the prompt
    version that went out, and its draft_id never comes to name other text."""
    import hashlib
    import re
    from dataclasses import replace

    from fastapi.testclient import TestClient
    from fictional_profile import profile_text
    from review_seed import PROV, RUN, SEND, SURVEY, seed

    from company_reach.models import Draft
    from company_reach.review.app import create_app
    from company_reach.tools.db import record_draft

    seed(settings.db_path)
    settings.profile_path.write_text(profile_text(SURVEY))
    client = TestClient(
        create_app(settings.model_copy(update={"sending_approved": True})),
        base_url="http://127.0.0.1",
        follow_redirects=False,
    )
    html = client.get(f"/review/{RUN}/0").text
    card = dict(re.findall(r'name="(draft_id|body_sha256)" value="([^"]*)"', html))
    r = client.post(
        f"/decide/{RUN}/{SEND}?n=0",
        data={"action": "send", "to": "info@muster-metallbau.ch", **card},
        headers={"Sec-Fetch-Site": "same-origin"},
    )
    assert r.status_code == 200

    with connect(settings.db_path) as conn:
        sent = conn.execute("select subject, body from drafts").fetchone()
        record_draft(
            conn,
            RUN,
            SEND,
            Draft(
                subject="Ein anderer Betreff",
                body="Ein anderer Text.",
                model_text="x",
                link=f"{SURVEY}/?c={SEND}&l=de",
                mailto_fits=True,
                frame_version="frame@1",
            ),
            contact_id=None,
            provenance=replace(PROV, prompt_version="5"),
        )
        row = conn.execute("select * from ledger where status = 'sent'").fetchone()
        joined = conn.execute(
            "select body from drafts where id = ?", (row["draft_id"],)
        ).fetchone()
    assert row["subject"] == sent["subject"]
    assert row["body_sha256"] == hashlib.sha256(sent["body"].encode()).hexdigest()
    assert row["prompt_version"] == PROV.prompt_version
    assert joined is None or joined["body"] == sent["body"]


@pytest.mark.parametrize("how", ["forget", "purge"])
def test_the_snapshot_loses_the_subject_and_keeps_the_hash(settings, how):
    """A subject like «Für Frau Muster: …» names the person; the hash and
    the version say what went out without naming anyone."""
    from review_seed import SEND, seed

    from company_reach.forget import forget, purge

    seed(settings.db_path)
    with connect(settings.db_path) as conn:
        record_decision(
            conn,
            SEND,
            "sent",
            address="info@muster-metallbau.ch",
            subject="Für Frau Muster: Masterarbeit an der OST",
            body_sha256="ab" * 32,
            prompt_version="4",
            decided_at="2026-09-25T08:00:00+00:00",
        )
    if how == "forget":
        forget(settings, SEND)
    else:
        purge(settings, older_than_days=365, today="2027-10-01")
    with connect(settings.db_path) as conn:
        row = conn.execute(
            "select subject, body_sha256, prompt_version from ledger"
        ).fetchone()
    assert tuple(row) == (None, "ab" * 32, "4")


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


def test_contacted_once_survives_forget(settings):
    """Contacted once, ever: a sent row that no later `not_sent` or
    `bounced` took back. The pair is kept by the row's id, not by its
    address, because forget clears the address."""
    from review_seed import HOLD, SEND, seed

    from company_reach.forget import forget

    seed(settings.db_path)
    with connect(settings.db_path) as conn:
        first = record_decision(conn, SEND, "sent", address="info@muster-metallbau.ch")
        record_decision(
            conn, SEND, "bounced", address="info@muster-metallbau.ch", reverses=first
        )
        record_decision(conn, SEND, "sent", address="anna.muster@muster-metallbau.ch")
        never_left = record_decision(conn, HOLD, "sent", address="a@x.example")
        record_decision(conn, HOLD, "not_sent", reverses=never_left)
    forget(settings, SEND)
    forget(settings, HOLD)
    with connect(settings.db_path) as conn:
        assert was_contacted(conn, SEND)
        assert not was_contacted(conn, HOLD)


def test_a_send_taken_back_is_not_counted_this_month(db: Path):
    with connect(db) as conn:
        sent = record_decision(
            conn, UID, "sent", decided_at="2026-09-02T08:00:00+00:00"
        )
        record_decision(conn, UID, "not_sent", reverses=sent)
        record_decision(conn, UID, "sent", decided_at="2026-09-02T08:05:00+00:00")
        assert sent_this_month(conn, today="2026-09-24") == 1
