from pathlib import Path

from company_reach.models import CompanyRecord
from company_reach.tools.db import connect, init_db, upsert_companies


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
