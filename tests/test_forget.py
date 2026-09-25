"""`forget`: a deletion request, honoured everywhere the tool keeps it.

The audit's acceptance (`audit-2026-09-19.md:256`): afterwards nothing under
`data/` names the person, and the key is suppressed. The tool's own records
— the database and the page cache — are cleaned; hand-kept research files
(`data/v0`, `data/golden`) are not edited by code, but every one that still
names the person is reported so a human can.
"""

import json
from pathlib import Path

import pytest
from review_seed import SEND, SKIP, seed

from company_reach.forget import forget
from company_reach.tools.db import connect, is_suppressed, record_decision

NAME = "Anna Muster"
OTHER = "Beat Beispiel"


@pytest.fixture
def data(settings) -> Path:
    seed(settings.db_path)
    with connect(settings.db_path) as conn:
        record_decision(conn, SEND, "sent", address="info@muster-metallbau.ch")
    cache = settings.data_dir / "cache"
    cache.mkdir(parents=True)
    for key, url in (
        ("a1", "https://muster-metallbau.ch/team"),
        ("b2", "https://other.ch/"),
    ):
        (cache / f"{key}.html").write_text(
            f"<p>{NAME}, Inhaberin</p>" if key == "a1" else "<p>x</p>"
        )
        (cache / f"{key}.json").write_text(json.dumps({"url": url, "status": "200"}))
    with connect(settings.db_path) as conn:
        conn.execute(
            "insert into pages (url, fetched_at, status, text, raw_path)"
            " values (?,?,?,?,?)",
            (
                "https://muster-metallbau.ch/team",
                "t",
                200,
                f"{NAME}, Inhaberin",
                str(cache / "a1.html"),
            ),
        )
    v0 = settings.data_dir / "v0"
    v0.mkdir()
    (v0 / "outreach.md").write_text(
        f"| 2026-09-12 | CHE-000.000.046 | x | {NAME} | skip | y |\n"
    )
    return settings.data_dir


def everything_under(data: Path, *, skip: Path | None = None) -> str:
    return "\n".join(
        p.read_bytes().decode("utf-8", "ignore")
        for p in data.rglob("*")
        if p.is_file() and p != skip
    )


def test_the_database_and_the_cache_no_longer_name_the_person(settings, data):
    report = forget(settings, SEND)
    text = everything_under(data, skip=data / "v0" / "outreach.md")
    assert NAME not in text
    assert OTHER not in text  # the alternatives named beside her
    assert "info@muster-metallbau.ch" not in text
    assert report.suppressed == [SEND]


def test_other_sites_in_the_cache_are_left_alone(settings, data):
    forget(settings, SEND)
    assert (data / "cache" / "b2.html").exists()


def test_the_decision_survives_without_the_address(settings, data):
    # "contacted once, ever" still has to hold after the person is forgotten
    forget(settings, SEND)
    with connect(settings.db_path) as conn:
        row = conn.execute(
            "select status, address from ledger where uid = ?", (SEND,)
        ).fetchone()
        assert (row["status"], row["address"]) == ("sent", None)
        assert is_suppressed(conn, SEND)


def test_a_hand_kept_file_that_still_names_the_person_is_reported(settings, data):
    report = forget(settings, SEND)
    assert report.still_named == [data / "v0" / "outreach.md"]
    assert NAME in (data / "v0" / "outreach.md").read_text()  # not edited by code


def test_an_address_finds_the_company_and_is_suppressed_too(settings, data):
    report = forget(settings, "Info@Muster-Metallbau.ch")
    assert report.suppressed == [SEND, "info@muster-metallbau.ch"]
    with connect(settings.db_path) as conn:
        assert (
            conn.execute("select count(*) from contacts").fetchone()[0] == 1
        )  # HOLD's
        assert is_suppressed(conn, "info@muster-metallbau.ch")


def test_an_unknown_key_is_still_suppressed(settings, data):
    # a request can come before the tool ever found the person
    report = forget(settings, "someone@nowhere.example")
    assert report.companies == []
    with connect(settings.db_path) as conn:
        assert is_suppressed(conn, "someone@nowhere.example")


def test_a_company_from_before_the_site_record_still_loses_its_cache(settings, data):
    # found on a copy of the real data: runs from before M7 wrote no `sites`
    # row, so the site's domain has to come from the contact instead
    with connect(settings.db_path) as conn:
        conn.execute("delete from sites where uid = ?", (SEND,))
    forget(settings, SEND)
    assert not (data / "cache" / "a1.html").exists()
    assert (data / "cache" / "b2.html").exists()


def test_forget_removes_the_search_log(settings, data):
    """Search results can name people — a directory listing, a profile URL —
    so the log goes with everything else."""
    forget(settings, SKIP)
    with connect(settings.db_path) as conn:
        left = conn.execute(
            "select count(*) from searches where uid = ?", (SKIP,)
        ).fetchone()[0]
    assert left == 0


def test_a_home_page_that_moved_is_deleted_from_the_cache(settings, data):
    """The cache keeps a page under the URL that was asked for. When that
    URL redirected to the company's site, the text is the site's, so it goes
    too."""
    cache = settings.data_dir / "cache"
    (cache / "c3.html").write_text(f"<p>{NAME}</p>")
    (cache / "c3.json").write_text(
        json.dumps(
            {
                "url": "https://muster-alt.ch/",
                "final_url": "https://muster-metallbau.ch/",
                "status": 200,
            }
        )
    )
    forget(settings, SEND)
    assert not (cache / "c3.html").exists()
