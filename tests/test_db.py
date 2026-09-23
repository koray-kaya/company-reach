from pathlib import Path

from company_reach.models import CompanyProfile, CompanyRecord, Person
from company_reach.tools.db import (
    connect,
    init_db,
    profile_by_uid,
    record_page,
    upsert_companies,
    upsert_profile,
)


def rec(uid: str) -> CompanyRecord:
    return CompanyRecord(
        uid=uid,
        name=f"{uid} AG",
        legal_form="0106",
        municipality="3203",
        purpose="Betrieb einer Schreinerei.",
        purpose_head="Betrieb einer Schreinerei.",
    )


def test_init_creates_tables_and_wal(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    with connect(db) as conn:
        names = {
            r["name"]
            for r in conn.execute("select name from sqlite_master where type='table'")
        }
        assert {
            "companies",
            "runs",
            "scores",
            "seen",
            "results",
            "ledger",
            "suppression",
        } <= names
        assert conn.execute("pragma journal_mode").fetchone()[0] == "wal"


def test_upsert_is_idempotent(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    with connect(db) as conn:
        two = [rec("CHE000000001"), rec("CHE000000002")]
        assert upsert_companies(conn, two, "r1") == 2
        assert upsert_companies(conn, [rec("CHE000000001")], "r2") == 1
        assert conn.execute("select count(*) from companies").fetchone()[0] == 2


def test_connection_rolls_back_on_error(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    try:
        with connect(db) as conn:
            upsert_companies(conn, [rec("CHE000000003")], "r1")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with connect(db) as conn:
        assert conn.execute("select count(*) from companies").fetchone()[0] == 0


# --- profiles (M5) ------------------------------------------------------------


def a_profile(**over) -> CompanyProfile:
    fields = {
        "description": "Baut Treppen und Innenausbau aus Massivholz.",
        "size_signal": "about eight people",
        "persons": [Person(name="Anna Muster", role="Geschäftsführerin")],
        "addresses": ["Musterweg 3, 9000 St. Gallen"],
    }
    return CompanyProfile(**(fields | over))


def test_a_profile_survives_the_round_trip(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    with connect(db) as conn:
        upsert_profile(conn, "run-1", "CHE109047505", a_profile())
    with connect(db) as conn:
        back = profile_by_uid(conn, "run-1", "CHE109047505")
    assert back == a_profile()


def test_an_empty_profile_is_not_a_missing_one(tmp_path: Path):
    """The difference the pipeline turns on: "we read the site and it names
    nobody" is a finding, "we never got there" is not. They must not both
    read as falsy at the call site."""
    db = tmp_path / "t.db"
    init_db(db)
    with connect(db) as conn:
        upsert_profile(conn, "run-1", "CHE109047505", a_profile(persons=[]))
    with connect(db) as conn:
        found = profile_by_uid(conn, "run-1", "CHE109047505")
        missing = profile_by_uid(conn, "run-1", "CHE102494429")
    assert found is not None and found.persons == []
    assert missing is None


def test_a_rerun_replaces_the_profile_rather_than_duplicating_it(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    with connect(db) as conn:
        upsert_profile(conn, "run-1", "CHE109047505", a_profile())
        upsert_profile(conn, "run-1", "CHE109047505", a_profile(size_signal="two"))
    with connect(db) as conn:
        rows = conn.execute("select count(*) from profiles").fetchone()[0]
        back = profile_by_uid(conn, "run-1", "CHE109047505")
    assert rows == 1
    assert back is not None and back.size_signal == "two"


def test_the_offsite_mark_survives_storage(tmp_path: Path):
    """check_profile's verdict is the reason the row is worth keeping; a
    round trip that dropped it would hide a third-party address."""
    db = tmp_path / "t.db"
    init_db(db)
    marked = Person(name="Anna Muster", email="ceo@evil.example", email_offsite=True)
    with connect(db) as conn:
        upsert_profile(conn, "run-1", "CHE109047505", a_profile(persons=[marked]))
    with connect(db) as conn:
        back = profile_by_uid(conn, "run-1", "CHE109047505")
    assert back is not None and back.persons[0].email_offsite is True


def test_a_page_that_was_read_is_indexed(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    with connect(db) as conn:
        record_page(
            conn,
            "https://muster-metallbau.ch/impressum",
            status=200,
            text="Muster Metallbau AG, Beispielstrasse 1",
            raw_path="data/cache/abc.html",
        )
    with connect(db) as conn:
        row = conn.execute("select * from pages").fetchone()
    assert row["url"] == "https://muster-metallbau.ch/impressum"
    assert row["status"] == 200
    assert "Beispielstrasse" in row["text"]
    assert row["raw_path"] == "data/cache/abc.html"
    assert row["fetched_at"]


def test_reading_a_page_again_updates_its_row(tmp_path: Path):
    """The url is the key, because the table indexes the cache and the cache
    holds one copy per url. A second run must not double the table."""
    db = tmp_path / "t.db"
    init_db(db)
    url = "https://muster-metallbau.ch/impressum"
    with connect(db) as conn:
        record_page(conn, url, status=200, text="old", raw_path="a.html")
        record_page(conn, url, status=200, text="new", raw_path="a.html")
    with connect(db) as conn:
        rows = conn.execute("select text from pages").fetchall()
    assert [r["text"] for r in rows] == ["new"]
