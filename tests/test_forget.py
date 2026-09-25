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
    # the address the mail went to stays where the promise keeps it — on
    # the never-again list — and nowhere else
    assert report.suppressed == [SEND, "info@muster-metallbau.ch"]
    with connect(settings.db_path) as conn:
        tables = conn.execute(
            "select name from sqlite_master"
            " where type = 'table' and name <> 'suppression'"
        ).fetchall()
        for (table,) in tables:
            rows = [tuple(r) for r in conn.execute(f"select * from {table}")]
            assert "info@muster-metallbau.ch" not in repr(rows), table
    for path in (data / "cache").iterdir():
        assert "info@muster-metallbau.ch" not in path.read_text()


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


def test_the_promise_matches_what_forget_deletes(settings, data):
    """The mail promises "Ein kurzes «Nein» genügt, dann lösche ich Ihren
    Namen". `forget` deletes the name and keeps the address on the
    never-again list for good, so no text may promise to delete the
    address, and the docstring quotes the promise the mail makes."""
    import itertools
    import re

    from fictional_profile import INVITATION

    import company_reach.forget
    from company_reach.models import Contact
    from company_reach.tools.invitation import privacy

    for source, kind, name in itertools.product(
        ("site", "shab"),
        ("seen", "generic", "constructed"),
        ("Anna Muster", "Reto", None),
    ):
        if kind == "constructed" and name != "Anna Muster":
            continue  # nobody named is never written to at a guessed address
        text = privacy(
            Contact(name=name, email="a@b.example", email_kind=kind, source=source),
            INVITATION,
        )
        assert not re.search(r"lösche ich (sie|beides|Ihre|diese)\b", text), text
    promise = "Ein kurzes «Nein» genügt, dann lösche ich Ihren Namen"
    assert promise in " ".join(company_reach.forget.__doc__.split())

    forget(settings, "info@muster-metallbau.ch")
    with connect(settings.db_path) as conn:
        assert conn.execute("select count(*) from drafts").fetchone()[0] == 0
        assert is_suppressed(conn, "info@muster-metallbau.ch")


# --- copies the tool did not make (audit: backups and logs under data/) ------


def test_a_backup_database_is_reported(settings, data):
    """A copy of the database taken before a risky step, and a log of a
    run, both name the person. forget does not edit them, so it lists them;
    the live database, which it just cleaned, is not listed."""
    import sqlite3

    backups = data / "backups"
    backups.mkdir()
    source = sqlite3.connect(settings.db_path)
    copy = sqlite3.connect(backups / "company_reach-before.db")
    source.backup(copy)
    copy.close()
    source.close()
    (data / "audit").mkdir()
    (data / "audit" / "run.log").write_text(f"contact: {NAME} <info@x.example>\n")

    report = forget(settings, SEND)
    assert backups / "company_reach-before.db" in report.still_named
    assert data / "audit" / "run.log" in report.still_named
    assert settings.db_path not in report.still_named


def test_after_a_purge_forget_still_finds_the_backups(settings, data):
    """Review: a purge deletes the contact, so `forget <UID>` a year later
    had no name to look for and listed nothing. The address the mail went
    to is still in the ledger, and a backup made before the purge holds it."""
    import sqlite3

    from company_reach.forget import purge

    backups = data / "backups"
    backups.mkdir()
    source = sqlite3.connect(settings.db_path)
    copy = sqlite3.connect(backups / "company_reach-before.db")
    source.backup(copy)
    copy.close()
    source.close()
    purge(settings, older_than_days=365, today="2028-01-01")

    report = forget(settings, SEND)
    assert backups / "company_reach-before.db" in report.still_named


def test_a_name_across_a_read_boundary_is_found(settings, data, monkeypatch):
    """Files are read in blocks, never whole — a backup can be gigabytes.
    A name split between two blocks is still found."""
    import company_reach.forget as forget_module

    monkeypatch.setattr(forget_module, "_BLOCK", 4)
    (data / "notes.log").write_text(f"xxxxxx{NAME.upper()}xxxxxx")
    report = forget(settings, SEND)
    assert data / "notes.log" in report.still_named


def test_any_file_naming_the_person_is_reported_whatever_its_ending(settings, data):
    """Review: an allow-list of endings skipped files data/ holds today — a
    command's .out, a hand-made .bak. Only code, binaries and tool folders
    are left out."""
    (data / "export.out").write_text(f"{NAME}\n")
    (data / "backups").mkdir()
    (data / "backups" / "company_reach.db.bak").write_text(f"x {NAME} x")
    (data / ".git").mkdir()
    (data / ".git" / "COMMIT_EDITMSG").write_text(NAME)
    (data / "helper.py").write_text(f"# {NAME}")
    report = forget(settings, SEND)
    assert data / "export.out" in report.still_named
    assert data / "backups" / "company_reach.db.bak" in report.still_named
    assert data / ".git" / "COMMIT_EDITMSG" not in report.still_named
    assert data / "helper.py" not in report.still_named


def test_a_file_that_cannot_be_searched_is_named(settings, data, monkeypatch):
    """A compressed backup or a file the tool may not read could name the
    person; it is listed as not searched, never passed over in silence."""
    import gzip

    from typer.testing import CliRunner

    from company_reach import cli

    (data / "backup.db.gz").write_bytes(gzip.compress(NAME.encode()))
    locked = data / "locked.txt"
    locked.write_text(NAME)
    locked.chmod(0)
    try:
        monkeypatch.setattr(cli, "get_settings", lambda: settings)
        r = CliRunner().invoke(cli.app, ["forget", SEND])
    finally:
        locked.chmod(0o600)
    assert r.exit_code == 0, r.output
    assert f"not searched: {data / 'backup.db.gz'}" in r.output
    assert f"not searched: {locked}" in r.output


# --- forget by address (audit: a reply from another address deletes nothing) --


def test_forget_by_an_address_only_in_a_profile(settings, data):
    """The site named her with her own address; the mail went to the inbox.
    She answers from her own address, which only her profile holds."""
    own = "anna.muster@muster-metallbau.ch"
    persons = [{"name": NAME, "role": "Inhaberin", "email": own}]
    with connect(settings.db_path) as conn:
        conn.execute(
            "update profiles set profile = ? where uid = ?",
            (json.dumps({"description": "Stahltreppen.", "persons": persons}), SEND),
        )
    report = forget(settings, own)
    assert report.companies == [SEND]
    with connect(settings.db_path) as conn:
        left = conn.execute(
            "select count(*) from profiles where uid = ?", (SEND,)
        ).fetchone()[0]
        assert left == 0
        assert is_suppressed(conn, SEND)
        assert is_suppressed(conn, own)


def test_forget_after_purge_still_suppresses(settings, data):
    """A purge keeps a sent row's address as the key to a later request.
    A year on, the contact is gone and only the ledger knows the address."""
    from company_reach.forget import purge

    purge(settings, older_than_days=365, today="2028-01-01")
    report = forget(settings, "info@muster-metallbau.ch")
    assert report.companies == [SEND]
    with connect(settings.db_path) as conn:
        row = conn.execute("select address from ledger where uid = ?", (SEND,))
        assert row.fetchone()["address"] is None
        assert is_suppressed(conn, SEND)
        assert is_suppressed(conn, "info@muster-metallbau.ch")


def test_an_address_matches_exactly_not_as_a_pattern(settings, data):
    # LIKE reads "_" as any character: inf_@ matched info@ of another firm
    report = forget(settings, "inf_@muster-metallbau.ch")
    assert report.companies == []
    with connect(settings.db_path) as conn:
        assert not is_suppressed(conn, SEND)


def test_an_idn_address_matches_its_ascii_form(settings, data):
    from company_reach.models import Contact, ContactAddress
    from company_reach.tools.db import record_contact

    unicode_form = "info@müller-druck.ch"
    with connect(settings.db_path) as conn:
        record_contact(
            conn,
            "r1",
            SKIP,
            Contact(
                email=unicode_form,
                email_kind="generic",
                source="site",
                addresses=[ContactAddress(email=unicode_form, kind="generic")],
            ),
        )
    ascii_form = "INFO@" + "müller-druck.ch".encode("idna").decode()
    report = forget(settings, ascii_form)
    assert report.companies == [SKIP]


def test_forget_by_uid_keeps_the_sent_address_on_the_never_again_list(settings, data):
    """The ledger loses the address; the never-again list keeps it, so a
    sister company sharing the inbox is not written to again (D3)."""
    forget(settings, SEND)
    with connect(settings.db_path) as conn:
        assert is_suppressed(conn, "info@muster-metallbau.ch")


def test_an_unknown_address_explains_and_exits_2(settings, data, monkeypatch):
    """Review focus 4: an address the tool never stored. It is suppressed,
    and the operator is told how to find the company — the UID in the
    quoted survey link — instead of reading "0 companies" as success."""
    from typer.testing import CliRunner

    from company_reach import cli

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    r = CliRunner().invoke(cli.app, ["forget", "someone@nowhere.example"])
    assert r.exit_code == 2, r.output
    assert "c=" in r.output
    assert "company-reach forget CHE" in r.output
    with connect(settings.db_path) as conn:
        assert is_suppressed(conn, "someone@nowhere.example")


def forget_cli(settings, monkeypatch, key: str):
    from typer.testing import CliRunner

    from company_reach import cli

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return CliRunner().invoke(cli.app, ["forget", key])


def suppression_keys(settings) -> set[str]:
    with connect(settings.db_path) as conn:
        return {r["key"] for r in conn.execute("select key from suppression")}


def test_a_uid_with_a_wrong_check_digit_is_refused(settings, data, monkeypatch):
    """Review: a mistyped UID was suppressed and reported as a success."""
    r = forget_cli(settings, monkeypatch, "CHE-000.000.047")
    assert r.exit_code == 2, r.output
    assert "check digit" in r.output
    assert suppression_keys(settings) == set()


def test_a_uid_in_no_table_exits_2(settings, data, monkeypatch):
    """A valid UID the tool never held: nothing to delete, which must not
    read as a deletion done."""
    r = forget_cli(settings, monkeypatch, "CHE-900.000.016")
    assert r.exit_code == 2, r.output
    assert "No record of CHE900000016" in r.output
    assert suppression_keys(settings) == {"CHE900000016"}


def test_a_pasted_survey_link_forgets_its_uid(settings, data):
    """The reply quotes the invitation; its link is what gets pasted. The
    link's c= UID is the key — the link itself is no suppression key."""
    report = forget(settings, f"https://survey.test/form/?c={SEND}&l=de")
    assert report.companies == [SEND]
    assert suppression_keys(settings) == {SEND, "info@muster-metallbau.ch"}


def test_a_key_that_is_neither_is_refused(settings, data, monkeypatch):
    r = forget_cli(settings, monkeypatch, "Anna Muster")
    assert r.exit_code == 2, r.output
    assert "neither" in r.output
    assert suppression_keys(settings) == set()


def test_an_unknown_address_forgotten_twice_still_exits_2(settings, data, monkeypatch):
    """Review: the first run suppressed the address, and the second read
    that as a known address and reported success — although no company was
    ever found and nothing was ever deleted."""
    from typer.testing import CliRunner

    from company_reach import cli

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    runner = CliRunner()
    first = runner.invoke(cli.app, ["forget", "someone@nowhere.example"])
    second = runner.invoke(cli.app, ["forget", "Someone@Nowhere.example"])
    assert (first.exit_code, second.exit_code) == (2, 2), second.output
    assert "already on the never-again list since" in second.output
    assert "no company found" in second.output
    assert "company-reach forget CHE" in second.output
