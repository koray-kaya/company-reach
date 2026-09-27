"""The survey's answers, joined to the ledger by the UID in the link.

Every invitation's link carries `?c=<UID>`, so a started or completed
response ties to one `sent` row without a second mail or a tracking pixel.
`responses import` reads the survey's export into a local table; `report`
counts per frame, A/B arm and kind of contact, with Wilson intervals,
because at thesis scale a rate without its interval says more than it
knows. All UIDs and dates below are fictional.
"""

from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from company_reach import cli
from company_reach.errors import CompanyReachError
from company_reach.invites import NewInvite, record_invite
from company_reach.responses import (
    SurveyUnreachable,
    fetch_tags,
    import_responses,
    refresh_from_survey,
    report_rows,
    wilson,
)
from company_reach.tools.db import connect, init_db, record_decision, suppress

runner = CliRunner()
A, B, C, D = "CHE000000046", "CHE900000016", "CHE900000022", "CHE123456788"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    init_db(path)
    return path


def sent(conn, uid, arm="voll", kind="generic/site/named", at="2026-10-01"):
    record_decision(
        conn,
        uid,
        "sent",
        address=f"info@{uid.lower()}.example",
        frame_version="frame@1",
        arm=arm,
        contact_kind=kind,
        decided_at=f"{at}T09:00:00+00:00",
    )


def export(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "export.csv"
    path.write_text("uid,started_at,completed_at\n" + "\n".join(rows) + "\n")
    return path


def group(rows, arm, kind):
    return next(r for r in rows if (r.arm, r.contact_kind) == (arm, kind))


def test_a_response_is_matched_by_uid(db, tmp_path):
    with connect(db) as conn:
        sent(conn, A)
        # the export writes the UID the way the link carried it, or dotted
        report = import_responses(
            conn,
            export(
                tmp_path, "CHE-000.000.046,2026-10-03T10:00:00Z,2026-10-03T10:14:00Z"
            ),
        )
        rows, unmatched = report_rows(conn)
    assert (report.rows, report.matched) == (1, 1)
    row = group(rows, "voll", "generic/site/named")
    assert (row.sent, row.started, row.completed) == (1, 1, 1)
    assert unmatched == 0


def test_an_unknown_uid_is_counted_not_dropped(db, tmp_path):
    # a forwarded link typed by hand, the survey's own smoke test: kept and
    # counted, so the totals add up to the export
    with connect(db) as conn:
        sent(conn, A)
        report = import_responses(
            conn,
            export(
                tmp_path,
                f"{A},2026-10-03,",
                "SMOKE,2026-10-02,2026-10-02",
                f"{B},2026-10-04,",
            ),
        )
        stored = conn.execute("select count(*) from responses").fetchone()[0]
        _, unmatched = report_rows(conn)
    assert (report.rows, report.matched, stored) == (3, 1, 3)
    assert unmatched == 2


def test_report_counts_per_arm_and_kind(db, tmp_path):
    with connect(db) as conn:
        sent(conn, A, arm="voll", kind="generic/site/named")
        sent(conn, B, arm="kurz", kind="generic/site/named")
        sent(conn, C, arm="kurz", kind="generic/site/named")
        sent(conn, D, arm="kurz", kind="constructed/shab/named")
        suppress(conn, C, reason="forgotten on request")  # a «Nein»
        record_decision(conn, "CHE111111118", "skipped")  # never sent
        import_responses(
            conn,
            export(tmp_path, f"{B},2026-10-02,2026-10-02", f"{D},2026-10-05,"),
        )
        rows, _ = report_rows(conn)
    kurz = group(rows, "kurz", "generic/site/named")
    assert (kurz.sent, kurz.never, kurz.delivered, kurz.started, kurz.completed) == (
        2,
        1,
        2,
        1,
        1,
    )
    shab = group(rows, "kurz", "constructed/shab/named")
    assert (shab.sent, shab.started, shab.completed) == (1, 1, 0)
    voll = group(rows, "voll", "generic/site/named")
    assert (voll.sent, voll.started) == (1, 0)
    assert sum(r.sent for r in rows) == 4


def test_a_start_after_the_window_is_not_counted(db, tmp_path):
    # the primary metric: started within 21 days of the mail
    with connect(db) as conn:
        sent(conn, A, at="2026-10-01")
        import_responses(conn, export(tmp_path, f"{A},2026-10-30,"))
        within, _ = report_rows(conn, within_days=21)
        ever, _ = report_rows(conn, within_days=None)
    assert within[0].started == 0
    assert ever[0].started == 1


@pytest.mark.parametrize(
    ("started", "counted"),
    [
        ("2026-09-30T23:00:00+00:00", False),  # the day before the mail
        ("2026-10-01T08:00:00+00:00", True),  # the same day, earlier hour
        ("2026-10-22", True),  # day 21
        ("2026-10-23", False),  # day 22
    ],
)
def test_the_window_counts_whole_days_from_the_mail(db, tmp_path, started, counted):
    """Review: the window had no lower bound, so a start before the mail —
    the owner trying the link, a link forwarded from an earlier contact —
    counted as an answer to it."""
    with connect(db) as conn:
        sent(conn, A, at="2026-10-01")
        import_responses(conn, export(tmp_path, f"{A},{started},"))
        rows, _ = report_rows(conn, within_days=21)
    assert rows[0].started == int(counted)


def test_an_import_replaces_the_last_export(db, tmp_path):
    # the survey exports everything so far, every time
    with connect(db) as conn:
        import_responses(conn, export(tmp_path, f"{A},2026-10-03,"))
        import_responses(conn, export(tmp_path, f"{A},2026-10-03,2026-10-04"))
        rows = conn.execute("select uid, completed_at from responses").fetchall()
    assert [(r["uid"], r["completed_at"][:10]) for r in rows] == [(A, "2026-10-04")]


def test_a_bad_date_names_its_line(db, tmp_path):
    with connect(db) as conn, pytest.raises(CompanyReachError, match="line 3"):
        import_responses(conn, export(tmp_path, f"{A},2026-10-03,", f"{B},gestern,"))


def test_a_missing_column_is_named(db, tmp_path):
    path = tmp_path / "export.csv"
    path.write_text("c,start\nCHE000000046,2026-10-03\n")
    with connect(db) as conn, pytest.raises(CompanyReachError, match="uid"):
        import_responses(conn, path)


def test_wilson_matches_the_measurement_plan():
    lo, hi = wilson(5, 100)
    assert (round(lo, 3), round(hi, 3)) == (0.022, 0.112)
    lo, hi = wilson(10, 200)
    assert (round(lo, 3), round(hi, 3)) == (0.027, 0.090)
    assert wilson(0, 0) == (0.0, 0.0)


def test_forget_removes_the_response_row(settings, tmp_path):
    from company_reach.forget import forget

    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        sent(conn, A)
        import_responses(conn, export(tmp_path, f"{A},2026-10-03,"))
    forget(settings, A)
    with connect(settings.db_path) as conn:
        assert conn.execute("select count(*) from responses").fetchone()[0] == 0


# --- the commands --------------------------------------------------------------


def test_the_commands_import_and_report(settings, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        sent(conn, A)
        sent(conn, B, arm="kurz")
    path = export(tmp_path, f"{A},2026-10-03,2026-10-03", "SMOKE,2026-10-02,")

    r = runner.invoke(cli.app, ["responses", "import", str(path)])
    assert r.exit_code == 0, r.output
    assert (
        "2 responses · 1 matched to a sent mail or a personal link · 1 without one"
        in r.output
    )

    r = runner.invoke(cli.app, ["report"])
    assert r.exit_code == 0, r.output
    assert "frame@1" in r.output
    assert "voll" in r.output and "kurz" in r.output
    assert "100.0% [20.7–100.0]" in r.output  # 1 of 1 started, Wilson 95%
    # Important 4: relabelled to what it actually counts
    assert "responses with no mail and no personal link: 1" in r.output


def test_a_personal_links_answer_gets_its_own_line_in_report(
    settings, monkeypatch, tmp_path
):
    """Review Important 4: an answer through a personal link is in neither
    the per-arm groups (built from the ledger's mails) nor "no mail and no
    personal link" (it is matched) — without its own line it simply vanished
    from the report."""
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    init_db(settings.db_path)
    with connect(settings.db_path) as conn:
        sent(conn, A)
        made = record_invite(
            conn, NewInvite(person="Beat Beispiel", company="Muster AG"), uid=None
        )
    path = export(tmp_path, f"{A},2026-10-03,2026-10-03", f"{made.code},2026-10-02,")
    r = runner.invoke(cli.app, ["responses", "import", str(path)])
    assert r.exit_code == 0, r.output

    r = runner.invoke(cli.app, ["report"])
    assert r.exit_code == 0, r.output
    assert "responses with no mail and no personal link: 0" in r.output
    assert "through a personal link: 1" in r.output


def test_a_bounce_counts_until_another_address_is_written_to(db, tmp_path):
    """Delivered is sent minus bounced, per company: a bounce followed by a
    mail to another address is delivered, and a send taken back as
    not_sent was never a mail."""

    def first_mail(conn, uid) -> int:
        return record_decision(
            conn,
            uid,
            "sent",
            frame_version="frame@1",
            arm="voll",
            contact_kind="generic/site/named",
        )

    with connect(db) as conn:
        record_decision(conn, A, "bounced", reverses=first_mail(conn, A))
        sent(conn, A, kind="seen/site/named")
        record_decision(conn, B, "bounced", reverses=first_mail(conn, B))
        record_decision(conn, C, "not_sent", reverses=first_mail(conn, C))
        rows, _ = report_rows(conn)
    generic = group(rows, "voll", "generic/site/named")
    assert (generic.sent, generic.bounced, generic.delivered) == (1, 1, 0)
    seen = group(rows, "voll", "seen/site/named")
    assert (seen.sent, seen.bounced, seen.delivered) == (1, 0, 1)
    assert sum(r.sent for r in rows) == 2  # C's mail never left


# --- fetched from the survey ----------------------------------------------------

TAGS = "https://survey.test/api/admin/tags"


def test_the_surveys_own_export_header_is_read(db, tmp_path):
    path = tmp_path / "survey.csv"
    path.write_text(
        "reference,company_uid,lang,started_at,completed_at\n"
        "R1,CHE-000.000.046,de,2026-10-03T10:00:00Z,2026-10-03T10:14:00Z\n"
    )
    with connect(db) as conn:
        sent(conn, A)
        report = import_responses(conn, path)
    assert (report.rows, report.matched) == (1, 1)


def test_a_personal_code_matches_its_link_whatever_its_case(db, tmp_path):
    with connect(db) as conn:
        made = record_invite(
            conn, NewInvite(person="Anna Muster", company="Muster AG"), uid=None
        )
        report = import_responses(
            conn, export(tmp_path, f"{made.code.lower()},2026-10-03T10:00:00Z,")
        )
        rows, unmatched = report_rows(conn)
        stored = conn.execute("select uid from responses").fetchone()[0]
    assert stored == made.code
    assert (report.rows, report.matched, unmatched) == (1, 1, 0)


def test_an_untagged_response_is_left_out(db, tmp_path):
    with connect(db) as conn:
        report = import_responses(conn, export(tmp_path, ",2026-10-03T10:00:00Z,"))
    assert report.rows == 0


@respx.mock
def test_the_survey_hands_over_its_tags():
    route = respx.get(TAGS).mock(
        return_value=httpx.Response(
            200,
            json={
                "tags": [
                    {
                        "tag": "P-7K3Q9X",
                        "started_at": "2026-10-03T10:00:00.123+00:00",
                        "completed_at": None,
                    }
                ]
            },
        )
    )
    rows = fetch_tags("https://survey.test/form", "a-long-password")
    assert rows == [("P-7K3Q9X", "2026-10-03T10:00:00.123000+00:00", None)]
    # the admin endpoint sits at the survey's root, whatever path the link has
    assert route.calls.last.request.headers["authorization"].startswith("Basic ")


@respx.mock
@pytest.mark.parametrize(
    ("answer", "words"),
    [
        (httpx.Response(401), "refused the password"),
        (httpx.Response(500), "answered 500"),
        (httpx.Response(200, json={"rows": []}), "not a list of tags"),
        (httpx.ConnectError("down"), "Could not reach the survey"),
        # Important 2: a malformed answer must not crash with an
        # AttributeError or KeyError instead of being reported as malformed
        (
            httpx.Response(
                200,
                json={"tags": [{"tag": "P-7K3Q9X", "started_at": 20261003}]},
            ),
            "not a list of tags",
        ),
        (
            httpx.Response(200, json={"tags": [{"tag": None}]}),
            "not a list of tags",
        ),
        (
            httpx.Response(200, json={"tags": [{"tag": ""}]}),
            "not a list of tags",
        ),
    ],
)
def test_a_survey_that_cannot_answer_says_why(answer, words):
    respx.get(TAGS).mock(side_effect=[answer])
    with pytest.raises(SurveyUnreachable, match=words):
        fetch_tags("https://survey.test", "a-long-password")


@respx.mock
def test_a_failed_fetch_keeps_the_last_answers(db, tmp_path):
    respx.get(TAGS).mock(side_effect=httpx.ConnectError("down"))
    with connect(db) as conn:
        import_responses(conn, export(tmp_path, f"{A},2026-10-03T10:00:00Z,"))
        with pytest.raises(SurveyUnreachable):
            refresh_from_survey(conn, "https://survey.test", "a-long-password")
        kept = conn.execute("select count(*) from responses").fetchone()[0]
    assert kept == 1


@respx.mock
def test_a_malformed_time_leaves_the_table_untouched(db, tmp_path):
    """Review Important 2: a `started_at` that is not a string or null (a
    number, here) used to raise AttributeError inside `_when`, escaping the
    `except` tuple and crashing the Contacts page with a 500."""
    respx.get(TAGS).mock(
        return_value=httpx.Response(
            200,
            json={"tags": [{"tag": "P-7K3Q9X", "started_at": 20261003}]},
        )
    )
    with connect(db) as conn:
        import_responses(conn, export(tmp_path, "OLD,2026-10-01,"))
        with pytest.raises(SurveyUnreachable, match="not a list of tags"):
            refresh_from_survey(conn, "https://survey.test", "a-long-password")
        kept = [r["uid"] for r in conn.execute("select uid from responses")]
    assert kept == ["OLD"]


@respx.mock
def test_a_malformed_url_is_reported_as_unreachable_not_raised_raw():
    """`httpx.InvalidURL` is not a subclass of `httpx.HTTPError`, so a
    malformed survey_url used to escape `fetch_tags` unhandled."""
    with pytest.raises(SurveyUnreachable, match="Could not reach the survey"):
        fetch_tags("https://survey.test:abc/form", "a-long-password")


@respx.mock
def test_an_empty_tag_list_is_the_one_case_where_nobody_answered(db, tmp_path):
    """The only case where an empty table is the true answer, not a fetch
    that could not look."""
    respx.get(TAGS).mock(return_value=httpx.Response(200, json={"tags": []}))
    with connect(db) as conn:
        import_responses(conn, export(tmp_path, "OLD,2026-10-01,"))
        refresh_from_survey(conn, "https://survey.test", "a-long-password")
        left = conn.execute("select count(*) from responses").fetchone()[0]
    assert left == 0
