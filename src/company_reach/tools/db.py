"""SQLite access. One connection per call: LangGraph runs sync nodes in a
thread pool, and a sqlite3 connection must not cross threads. WAL lets the
review page read while a run writes; busy_timeout waits instead of failing
when two writers meet. `with conn:` commits on success and rolls back on an
exception — it does not close, so we close in the finally."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from company_reach.models import CompanyRecord


def _open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # The PRAGMAs must run outside a transaction ("cannot change into wal mode
    # from within a transaction"), and autocommit=False opens one before the
    # first statement. So: set them first, then take over transaction control.
    conn = sqlite3.connect(path, autocommit=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.autocommit = False
    return conn


def init_db(path: Path) -> None:
    schema = files("company_reach").joinpath("schema.sql").read_text()
    conn = _open(path)
    try:
        with conn:
            conn.executescript(schema)
    finally:
        conn.close()


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    conn = _open(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def upsert_companies(
    conn: sqlite3.Connection, records: list[CompanyRecord], run_id: str
) -> int:
    rows = [
        (
            r.uid,
            r.name,
            r.legal_form,
            r.municipality,
            r.street,
            r.postal_code,
            r.city,
            r.purpose,
            r.purpose_head,
            now(),
            run_id,
        )
        for r in records
    ]
    conn.executemany(
        """INSERT INTO companies (uid, name, legal_form, municipality, street,
             postal_code, city, purpose, purpose_head, imported_at, import_run_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(uid) DO UPDATE SET name=excluded.name, purpose=excluded.purpose,
             purpose_head=excluded.purpose_head, street=excluded.street,
             postal_code=excluded.postal_code, city=excluded.city,
             imported_at=excluded.imported_at, import_run_id=excluded.import_run_id""",
        rows,
    )
    return len(rows)
