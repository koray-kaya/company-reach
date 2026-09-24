"""Bringing the prototype's decisions into the ledger, so "contacted once,
ever" holds across the rewrite (`audit-2026-09-19.md:304`).

v0 sent no mail (confirmed by Koray, 2026-09-24): its decisions were skip,
hold and a pending "recommended send" nobody acted on. So every v0 company
goes into `seen` and is never drawn again, a skip becomes a `skipped` row,
and a hold or a pending one gets no ledger row — nobody decided about it.
The fixtures are fictional companies in the same shape as `data/v0/`.
"""

from pathlib import Path

import pytest

from company_reach.import_v0 import import_v0
from company_reach.tools.db import connect, decision_for, init_db, was_contacted

V0 = Path(__file__).parent / "fixtures/v0"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "t.db"
    init_db(path)
    return path


def seen(db: Path) -> dict[str, tuple]:
    with connect(db) as conn:
        return {
            r["uid"]: (r["run_id"], r["batch_no"], r["drawn_at"][:10])
            for r in conn.execute("select * from seen")
        }


def test_every_v0_company_is_seen_with_its_batch_and_date(db: Path):
    with connect(db) as conn:
        import_v0(conn, V0)
    assert seen(db) == {
        "CHE000000046": ("v0", 1, "2026-09-12"),
        "CHE123456788": ("v0", 2, "2026-09-13"),
        "CHE111111118": ("v0", 2, "2026-09-13"),
    }


def test_a_skip_is_a_skipped_row_and_nothing_else_is_decided(db: Path):
    with connect(db) as conn:
        import_v0(conn, V0)
        assert decision_for(conn, "CHE000000046")["status"] == "skipped"
        assert decision_for(conn, "CHE000000046")["run_id"] == "v0"
        assert decision_for(conn, "CHE123456788") is None  # pending
        assert decision_for(conn, "CHE111111118") is None  # hold
        assert not any(
            was_contacted(conn, u)
            for u in ("CHE000000046", "CHE123456788", "CHE111111118")
        )


def test_the_import_can_run_twice(db: Path):
    with connect(db) as conn:
        first = import_v0(conn, V0)
        second = import_v0(conn, V0)
        rows = conn.execute("select count(*) from ledger").fetchone()[0]
    assert first == (3, 1)
    assert second == (0, 0)
    assert rows == 1


def test_a_newer_draw_of_the_same_company_is_not_overwritten(db: Path):
    with connect(db) as conn:
        conn.execute(
            "insert into seen values "
            "('CHE123456788', 'r7', 1, '2026-09-20T00:00:00+00:00')"
        )
        import_v0(conn, V0)
    assert seen(db)["CHE123456788"] == ("r7", 1, "2026-09-20")


def test_a_v0_log_that_disagrees_with_its_seen_file_is_refused(tmp_path: Path, db):
    broken = tmp_path / "v0"
    broken.mkdir()
    (broken / "seen.json").write_text((V0 / "seen.json").read_text())
    (broken / "outreach.md").write_text(
        (V0 / "outreach.md").read_text()
        + "| 2026-09-13 | CHE-000.000.001 | Nirgends AG | none | skip | x |\n"
    )
    with connect(db) as conn, pytest.raises(ValueError, match="CHE000000001"):
        import_v0(conn, broken)
