"""`purge`: personal data nobody asked about is not kept for ever (#27).

Decided with Koray (M8 open point 3): after 365 days a company's pages,
cache, profile, contacts, drafts and site evidence go — what `forget`
removes — while the ledger and the suppression list stay, because "contacted
once, ever" rests on them, and a `sent` row keeps its address as the record
of what was sent. Unlike `forget`, nobody is suppressed.
"""

import json
from pathlib import Path

import pytest
from review_seed import HOLD, SEND, SKIP, seed

from company_reach.forget import purge
from company_reach.tools.db import connect, is_suppressed, record_decision

NAME = "Anna Muster"
# review_seed draws every company on 2026-09-24
A_YEAR_LATER = "2027-10-01"


@pytest.fixture
def data(settings) -> Path:
    seed(settings.db_path)
    with connect(settings.db_path) as conn:
        record_decision(
            conn,
            SEND,
            "sent",
            address="info@muster-metallbau.ch",
            decided_at="2026-09-25T08:00:00+00:00",
        )
    cache = settings.data_dir / "cache"
    cache.mkdir(parents=True)
    (cache / "a1.html").write_text(f"<p>{NAME}</p>")
    (cache / "a1.json").write_text(
        json.dumps({"url": "https://muster-metallbau.ch/team", "status": "200"})
    )
    return settings.data_dir


def count(settings, table: str) -> int:
    with connect(settings.db_path) as conn:
        return conn.execute(f"select count(*) from {table}").fetchone()[0]


def test_nothing_younger_than_the_cut_off_is_touched(settings, data):
    report = purge(settings, older_than_days=365, today="2026-10-01")
    assert report.companies == []
    assert count(settings, "contacts") == 2
    assert (data / "cache" / "a1.html").exists()


def test_a_year_on_the_personal_data_is_gone(settings, data):
    report = purge(settings, older_than_days=365, today=A_YEAR_LATER)
    assert sorted(report.companies) == sorted([SEND, HOLD, SKIP])
    for table in ("contacts", "drafts", "profiles", "sites"):
        assert count(settings, table) == 0, table
    assert not (data / "cache" / "a1.html").exists()
    raw = (settings.db_path).read_bytes().decode("utf-8", "ignore")
    assert NAME not in raw


def test_the_ledger_keeps_its_decisions_and_the_sent_address(settings, data):
    purge(settings, older_than_days=365, today=A_YEAR_LATER)
    with connect(settings.db_path) as conn:
        row = conn.execute("select status, address from ledger").fetchone()
    assert (row["status"], row["address"]) == ("sent", "info@muster-metallbau.ch")


def test_purging_suppresses_nobody(settings, data):
    purge(settings, older_than_days=365, today=A_YEAR_LATER)
    with connect(settings.db_path) as conn:
        assert not any(is_suppressed(conn, uid) for uid in (SEND, HOLD, SKIP))


def test_a_recent_decision_keeps_an_old_company(settings, data):
    with connect(settings.db_path) as conn:
        record_decision(conn, HOLD, "skipped", decided_at="2027-09-20T08:00:00+00:00")
    report = purge(settings, older_than_days=365, today=A_YEAR_LATER)
    assert HOLD not in report.companies


def test_a_company_that_only_has_a_search_log_is_purged(settings, data):
    """A company whose search or site failed has no contact, profile or site
    record — only its search log, which can name people (a directory entry,
    a profile URL). It goes after a year like the rest."""
    from company_reach.tools.db import record_searches
    from company_reach.tools.search import Asked, Result

    old = "CHE222222228"
    with connect(settings.db_path) as conn:
        conn.execute(
            "insert into seen values (?, 'r0', 1, '2020-03-01T09:00:00+00:00')",
            (old,),
        )
        record_searches(
            conn,
            "r0",
            old,
            [
                Asked(
                    '"Alt Beispiel" Musterstadt',
                    "searxng",
                    [Result("https://ch.linkedin.com/in/anna-muster", "", "", "ddg")],
                )
            ],
        )
    report = purge(settings, older_than_days=365, today="2021-06-01")
    assert report.companies == [old]
    with connect(settings.db_path) as conn:
        left = conn.execute(
            "select count(*) from searches where uid = ?", (old,)
        ).fetchone()[0]
    assert left == 0
