"""The survey's answers, joined to the ledger by the UID in the link.

Every invitation's link carries `?c=<UID>`, so a started or completed
response ties to one `sent` row without a second mail or a tracking pixel.
`responses import` reads the survey's export into a local table; `report`
counts per frame, A/B arm and kind of contact, with Wilson intervals,
because at thesis scale a rate without its interval says more than it
knows. All UIDs and dates below are fictional.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from company_reach import cli
from company_reach.errors import CompanyReachError
from company_reach.responses import import_responses, report_rows, wilson
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
    assert "2 responses · 1 matched to a sent invitation · 1 without one" in r.output

    r = runner.invoke(cli.app, ["report"])
    assert r.exit_code == 0, r.output
    assert "frame@1" in r.output
    assert "voll" in r.output and "kurz" in r.output
    assert "100.0% [20.7–100.0]" in r.output  # 1 of 1 started, Wilson 95%
    assert "responses without a sent invitation: 1" in r.output
