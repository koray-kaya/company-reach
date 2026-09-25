import json
import sqlite3
from pathlib import Path

from company_reach.models import CompanyProfile, CompanyRecord, Person
from company_reach.tools.db import (
    connect,
    errored_uids,
    init_db,
    profile_by_uid,
    record_decision,
    record_page,
    record_searches,
    search_log,
    suppress,
    upsert_companies,
    upsert_profile,
)
from company_reach.tools.search import Asked, Result


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


def test_an_older_database_gains_the_columns_added_since(tmp_path: Path):
    # contacts shipped in M1 without source_date; a database created then
    # must still take a SHAB contact's notice date
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE contacts (
             id INTEGER PRIMARY KEY, run_id TEXT, uid TEXT, name TEXT, role TEXT,
             email TEXT, email_kind TEXT, source TEXT, source_url TEXT,
             linkedin_lead TEXT)"""
    )
    conn.close()

    init_db(path)
    init_db(path)  # and a second start does not try to add it again

    conn = sqlite3.connect(path)
    columns = {row[1] for row in conn.execute("pragma table_info(contacts)")}
    conn.close()
    assert {"source_date", "alternatives", "addresses"} <= columns


def test_an_old_database_gains_the_new_columns(tmp_path: Path):
    # frame@1: every draft records the model's own sentence (the card
    # rebuilds the mail from it) and the frame and arm it was written with.
    # The tables as M6 and M7 shipped them:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE drafts (
             id INTEGER PRIMARY KEY, run_id TEXT, uid TEXT, contact_id INTEGER,
             subject TEXT, body TEXT, mailto_fits INTEGER, prompt_version TEXT,
             model TEXT, created_at TEXT)"""
    )
    conn.execute(
        """CREATE TABLE contacts (
             id INTEGER PRIMARY KEY, run_id TEXT, uid TEXT, name TEXT, role TEXT,
             email TEXT, email_kind TEXT, source TEXT, source_url TEXT,
             source_date TEXT, linkedin_lead TEXT, alternatives TEXT,
             addresses TEXT)"""
    )
    conn.close()

    init_db(path)
    init_db(path)

    conn = sqlite3.connect(path)
    drafts = {row[1] for row in conn.execute("pragma table_info(drafts)")}
    contacts = {row[1] for row in conn.execute("pragma table_info(contacts)")}
    conn.close()
    assert {"model_text", "frame_version", "arm"} <= drafts
    # the outcome of check_draft: null = never checked, "" = passed
    assert "problems" in drafts
    # Frau / Herr as the page wrote it or the reviewer chose it
    assert "salutation" in contacts


def test_any_connection_brings_an_older_database_up_to_date(tmp_path: Path):
    # found live: `enrich` opened a database created before `addresses`
    # existed and failed on its first contact, because only some commands
    # ran init_db. Now the first connection in a process does.
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE contacts (
             id INTEGER PRIMARY KEY, run_id TEXT, uid TEXT, name TEXT, role TEXT,
             email TEXT, email_kind TEXT, source TEXT, source_url TEXT,
             linkedin_lead TEXT)"""
    )
    conn.close()

    with connect(path) as c:
        columns = {row[1] for row in c.execute("pragma table_info(contacts)")}
    assert "addresses" in columns


# --- the search log (#20) ----------------------------------------------------

QUERY = '"Muster Metallbau" Musterstadt'


def test_record_search_keeps_no_brave_urls(tmp_path: Path):
    """Brave's terms forbid storing its results. For a Brave query the log
    keeps the query, the count and any error — never a URL."""
    db = tmp_path / "t.db"
    asked = [
        Asked(
            QUERY,
            "searxng",
            [
                Result(f"https://muster-{n}.ch/", "t", "s", "duckduckgo")
                for n in range(12)
            ],
            unresponsive=["brave", "mojeek"],
        ),
        Asked(
            QUERY,
            "brave",
            [
                Result(
                    "https://muster-metallbau.ch/",
                    "t",
                    "s",
                    "brave-api",
                    provider="brave",
                )
            ],
        ),
        Asked(
            "Muster Metallbau Impressum", "searxng", error="SearXNG answered HTTP 503"
        ),
    ]
    with connect(db) as conn:
        record_searches(conn, "r1", "CHE000000046", asked)
    with connect(db) as conn:
        rows = search_log(conn, "r1", "CHE000000046")

    assert [
        (r["query"], r["provider"], r["result_count"], r["error"]) for r in rows
    ] == [
        (QUERY, "searxng", 12, None),
        (QUERY, "brave", 1, None),
        ("Muster Metallbau Impressum", "searxng", 0, "SearXNG answered HTTP 503"),
    ]
    assert len(json.loads(rows[0]["results"])) == 10
    assert json.loads(rows[0]["unresponsive"]) == ["brave", "mojeek"]
    assert rows[1]["results"] is None
    assert all(r["at"] for r in rows)
    assert "muster-metallbau.ch" not in json.dumps([dict(r) for r in rows])


def test_a_new_attempt_replaces_the_log(tmp_path: Path):
    """A retried company shows the searches of its last attempt, the way its
    site record and contact are replaced."""
    db = tmp_path / "t.db"
    with connect(db) as conn:
        record_searches(
            conn, "r1", "CHE000000046", [Asked(QUERY, "searxng", error="x")]
        )
        record_searches(conn, "r1", "CHE000000046", [Asked(QUERY, "searxng")])
        rows = search_log(conn, "r1", "CHE000000046")
    assert [r["error"] for r in rows] == [None]


def test_an_older_searches_table_gains_count_and_error(tmp_path: Path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE searches (
             id INTEGER PRIMARY KEY, run_id TEXT, uid TEXT, query TEXT,
             provider TEXT, results TEXT, unresponsive TEXT, chosen_url TEXT,
             tier TEXT, evidence TEXT, at TEXT)"""
    )
    conn.close()
    init_db(path)
    conn = sqlite3.connect(path)
    columns = {row[1] for row in conn.execute("pragma table_info(searches)")}
    conn.close()
    assert {"result_count", "error"} <= columns


def test_errored_uids_skips_decided_and_suppressed_companies(tmp_path: Path):
    """`retry` must not collect data again about a company a reviewer
    decided about or that asked never to be contacted."""
    db = tmp_path / "t.db"
    with connect(db) as conn:
        for uid in ("CHE000000001", "CHE000000002", "CHE000000003"):
            conn.execute(
                "insert into results (run_id, uid, error_kind, error_text,"
                " finished_at) values ('r1', ?, 'fetch', 'x', 't')",
                (uid,),
            )
        suppress(conn, "CHE000000002", reason="forgotten on request")
        record_decision(conn, "CHE000000003", "skipped")
        assert errored_uids(conn, "r1") == ["CHE000000001"]
